#!/bin/bash
# 构建 macOS 分发包：公众号文风微调.app（签名 + 公证）与 .dmg
#
# 依赖：本机已装 Xcode、keychain 里有 "Developer ID Application" 证书。
# 用法：
#   APPLE_ID=you@example.com APPLE_PW=xxxx-xxxx-xxxx-xxxx TEAM_ID=XXXXXXXXXX ./scripts/build-mac-app.sh
#
# 产物：dist/公众号文风微调.app、dist/公众号文风微调-1.0.0.dmg

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VERSION="${VERSION:-1.0.0}"
APP_NAME="公众号文风微调"
BUILD="$ROOT/dist"
APP="$BUILD/$APP_NAME.app"
IDENTITY="${IDENTITY:-$(security find-identity -v -p codesigning | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)"/\1/')}"

echo "== 1/6 清理 =="
rm -rf "$BUILD"
mkdir -p "$BUILD"

echo "== 2/6 生成 applet 外壳 =="
TMP_SCRIPT="$(mktemp /tmp/applet-XXXX.applescript)"
cat > "$TMP_SCRIPT" <<'APPLESCRIPT'
on run
	set myPath to POSIX path of (path to me)
	set launcher to myPath & "Contents/Resources/launch.command"
	tell application "Terminal"
		activate
		do script "exec " & quoted form of launcher
	end tell
end run
APPLESCRIPT
osacompile -o "$APP" "$TMP_SCRIPT"
rm -f "$TMP_SCRIPT"

echo "== 3/6 放入程序文件 =="
mkdir -p "$APP/Contents/Resources/project"
for f in app prompts schema docs scripts setup.sh start.sh 启动.command 使用说明.txt README.md LICENSE; do
  [ -e "$f" ] && cp -R "$f" "$APP/Contents/Resources/project/"
done
# 精简：不把构建脚本自己带进去，也不带运行时垃圾
rm -rf "$APP/Contents/Resources/project/scripts" 2>/dev/null || true
find "$APP/Contents/Resources/project" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$APP/Contents/Resources/project" -name "*.pyc" -delete 2>/dev/null || true

cat > "$APP/Contents/Resources/launch.command" <<'LAUNCH'
#!/bin/bash
set -u
SELF="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/公众号文风微调"

if [ ! -f "$DEST/app/server.py" ]; then
  echo "首次运行：正在把程序安装到「应用支持」目录…"
  mkdir -p "$DEST"
  cp -R "$SELF/project/." "$DEST/"
  chmod +x "$DEST/启动.command" "$DEST/setup.sh" "$DEST/start.sh" 2>/dev/null || true
fi
cd "$DEST" || exit 1
exec ./启动.command
LAUNCH
chmod +x "$APP/Contents/Resources/launch.command"
chmod +x "$APP/Contents/Resources/project/启动.command" 2>/dev/null || true

# Info.plist：改 bundle id / 版本 / 最低系统
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.comeonzhj.wxstyle" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.comeonzhj.wxstyle" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleName string $APP_NAME" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 12.0" "$APP/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$APP/Contents/Info.plist" 2>/dev/null || true

# 图标（有就用）
if [ -f "$ROOT/scripts/icon.icns" ]; then
  cp "$ROOT/scripts/icon.icns" "$APP/Contents/Resources/applet.icns"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet" "$APP/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string applet" "$APP/Contents/Info.plist"
fi

echo "== 4/6 签名 =="
echo "   身份：$IDENTITY"
codesign --force --deep --options runtime --timestamp \
  --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2

echo "== 5/6 公证 app 并贴票 =="
if [ -z "${APPLE_ID:-}" ] || [ -z "${APPLE_PW:-}" ] || [ -z "${TEAM_ID:-}" ]; then
  echo "   跳过：没给 APPLE_ID / APPLE_PW / TEAM_ID"; exit 0
fi
APPZIP="$BUILD/app.zip"
ditto -c -k --keepParent "$APP" "$APPZIP"
xcrun notarytool submit "$APPZIP" --apple-id "$APPLE_ID" --password "$APPLE_PW" \
  --team-id "$TEAM_ID" --wait
rm -f "$APPZIP"
xcrun stapler staple "$APP"

echo "== 6/6 制作 dmg 并公证 =="
DMG="$BUILD/${APP_NAME}-${VERSION}.dmg"
STAGE="$BUILD/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null 2>&1
rm -rf "$STAGE"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"
xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --password "$APPLE_PW" \
  --team-id "$TEAM_ID" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl -a -vvv --type open --context context:primary-signature "$DMG" 2>&1 | tail -2
echo
echo "✓ 完成：$DMG"
