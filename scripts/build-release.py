#!/usr/bin/env python3
"""打发布包：生成可供非技术用户「下载 → 解压 → 双击」的 zip。

要点：
1. 文件名用 UTF-8 并设置 zip 的 UTF-8 标记（0x800），否则部分解压工具会把中文名显示成乱码；
2. 保留可执行位，`启动.command` 解压后双击就能跑；
3. 排除运行时目录（虚拟环境、语料、日志、adapter），保证包小且干净。

用法： python3 scripts/build-release.py [版本号]
"""

import os
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv-mlx", "data", "my-articles", "logs", "adapters",
             "models", "output", "mlx-data", "__pycache__"}
SKIP_SUFFIX = {".pyc", ".DS_Store", ".log"}


def should_skip(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    if parts & SKIP_DIRS:
        return True
    return path.suffix in SKIP_SUFFIX or path.name == ".DS_Store"


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "1.0.0"
    # 外层用 ASCII 名字：部分下载/解压工具对中文文件名处理不一致，包内再保留中文
    root_name = "wechat-style-finetune"
    out = ROOT.parent / f"{root_name}-v{version}.zip"
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and not should_skip(p))

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            arc = root_name + "/" + str(p.relative_to(ROOT))
            info = zipfile.ZipInfo(arc)
            mode = p.stat().st_mode
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())

    size = out.stat().st_size / 1024
    print(f"✓ {out}  （{len(files)} 个文件，{size:.0f} KB）")


if __name__ == "__main__":
    main()
