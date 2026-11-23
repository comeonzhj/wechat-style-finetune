#!/usr/bin/env python3
"""公众号文章导入：把 mp.weixin.qq.com 的文章抓下来，转成干净 Markdown。

只用标准库，不装任何第三方依赖。

设计取舍
--------
- 抓取只认公开文章页，不需要登录、不碰后台接口，避免账号风险；
- 正文靠 `id="js_content"` 定位，这是公众号模板里最稳定的锚点；
- 图片一律换成占位符（默认 `<!-- 配图 -->`），链接只留文字，
  因为微调学的是文字，图床 URL 和跳转链接都是噪音；
- 失败不抛异常打断整体，逐个汇报成功/失败，方便用户重试。
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# 这些标签直接换行，让 Markdown 保持段落
BLOCK_TAGS = {"p", "section", "div", "br", "li", "blockquote", "tr"}
HEADING_TAGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### "}
SKIP_TAGS = {"script", "style", "svg", "noscript"}
VOID_TAGS = {"img", "br", "hr", "meta", "input", "link", "source", "col", "area"}


class _Body(HTMLParser):
    """把 js_content 里的 HTML 转成 Markdown 片段。"""

    def __init__(self, image_placeholder="<!-- 配图 -->"):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # js_content 内部嵌套深度
        self.out = []
        self.skip = 0
        self.img_ph = image_placeholder
        self.pending_img = 0

    # ---- 开标签 ----
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "img":
            self.out.append("\n\n" + self.img_ph + "\n\n")
            return
        if tag == "br":
            self.out.append("\n")
            return
        if tag in HEADING_TAGS:
            self.out.append("\n\n" + HEADING_TAGS[tag])
            return
        if tag in BLOCK_TAGS:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in HEADING_TAGS or tag in BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if self.skip:
            return
        self.out.append(data)

    def flush_images(self):
        if self.pending_img:
            self.out.append("\n\n" + self.img_ph + "\n\n")
            self.pending_img = 0


class ArticleHTMLParser(HTMLParser):
    """整页解析：标题、公众号名、发布时间、正文。"""

    def __init__(self, image_placeholder="<!-- 配图 -->"):
        super().__init__(convert_charrefs=True)
        self.image_placeholder = image_placeholder
        self.title = ""
        self.account = ""
        self.published = ""
        self.body = ""
        self.description = ""
        self._in = {}            # 标记当前处在哪个容器里
        self._buf = []           # 抓文本用
        self._depth = 0
        self._body_parser = None
        self._body_depth = 0

    # 容器判定
    @staticmethod
    def _is_body(a):
        return a.get("id") == "js_content"

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}
        if self._body_parser is not None:
            # 正文已经接管：原样喂给子解析器，自己只维护嵌套深度
            self._body_parser.feed(self.get_starttag_text() or "")
            if tag not in VOID_TAGS:
                self._body_depth += 1
            return
        if self._is_body(a):
            self._body_parser = _Body(self.image_placeholder)
            self._body_depth = 1   # js_content 自身占一层，闭合时归零
            return
        if a.get("id") == "activity-name" or (tag == "h1" and "rich_media_title" in a.get("class", "")):
            self._in["title"] = True
            self._buf = []
        elif a.get("id") == "js_name" or "rich_media_meta_nickname" in a.get("class", ""):
            self._in["account"] = True
            self._buf = []
        elif a.get("id") == "publish_time":
            self._in["time"] = True
            self._buf = []
        elif tag == "meta":
            if a.get("name") == "description" and a.get("content"):
                self.description = a["content"]

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._body_parser is not None:
            if tag in VOID_TAGS:
                return
            self._body_depth -= 1
            self._body_parser.feed("</%s>" % tag)
            if self._body_depth <= 0:
                self._body_parser.flush_images()
                self.body = "".join(self._body_parser.out)
                self._body_parser = None
            return
        for key in ("title", "account", "time"):
            if self._in.get(key):
                val = html.unescape(" ".join(self._buf)).strip()
                if key == "title":
                    self.title = val
                elif key == "account":
                    self.account = val
                else:
                    self.published = val
                self._in[key] = False
                self._buf = []

    def handle_data(self, data):
        if self._body_parser is not None:
            self._body_parser.feed(html.escape(data, quote=False))
            return
        if any(self._in.values()):
            self._buf.append(data)


def _clean_markdown(text: str) -> str:
    """把抓下来的碎片整理成能读的 Markdown。"""
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    # 只保留「整行都是空白」的换行，压掉多余空行
    lines = [ln.strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
                blank = True
            continue
        blank = False
        out.append(ln)
    text = "\n".join(out).strip()
    # 图片占位符前后留一个空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def fetch(url: str, timeout=25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_article(page: str, url: str = "", image_placeholder="<!-- 配图 -->") -> dict:
    p = ArticleHTMLParser(image_placeholder)
    p.feed(page)
    title = p.title.strip() or p.description.strip()[:40]
    if not title:
        m = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        title = html.unescape(m.group(1)).strip() if m else "未命名文章"
    account = p.account.strip()
    if not account:
        m = re.search(r'var nickname = "([^"]*)"', page) or re.search(
            r'<meta property="og:article:author" content="([^"]+)"', page
        )
        account = html.unescape(m.group(1)).strip() if m else ""
    published = p.published.strip()
    if not published:
        m = re.search(r'var ct = "(\d{9,11})"', page) or re.search(r'"create_time":\s*"(\d{9,11})"', page)
        if m:
            published = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d")
    body = _clean_markdown(p.body or "")
    # 有些模板正文抓不到，退回 og:description 之外的整体启发式
    if len(body) < 20:
        m = re.search(r'<div class="rich_media_content[^"]*"[^>]*>(.*?)</div>\s*</div>', page, re.S)
        if m:
            b = _Body(image_placeholder)
            b.feed(m.group(1))
            b.flush_images()
            body = _clean_markdown("".join(b.out))
    return {"title": title, "account": account, "published_at": published, "source": url, "body": body}


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip(" .")
    return (name or "untitled")[:80]


def to_markdown(meta: dict) -> str:
    fm = [
        "---",
        'title: "%s"' % meta["title"].replace('"', "'"),
        'account: "%s"' % meta.get("account", "").replace('"', "'"),
        'published_at: "%s"' % meta.get("published_at", ""),
        'source: "%s"' % meta.get("source", ""),
        "---",
        "",
    ]
    return "\n".join(fm) + "\n" + meta["body"] + "\n"


def import_urls(urls, out_dir, image_placeholder="<!-- 配图 -->", delay=0.8, log=print) -> dict:
    """批量抓取。返回 {"ok": [...], "fail": [...]}，不因单篇失败中断。"""
    os.makedirs(out_dir, exist_ok=True)
    ok, fail = [], []
    for i, url in enumerate(urls, 1):
        url = url.strip()
        if not url or url.startswith("#"):
            continue
        try:
            page = fetch(url)
            meta = parse_article(page, url, image_placeholder)
            if len(meta["body"]) < 50:
                raise ValueError("正文为空（可能需要登录，或该链接不是文章页）")
            path = os.path.join(out_dir, safe_filename(meta["title"]) + ".md")
            n = 1
            while os.path.exists(path):
                n += 1
                path = os.path.join(out_dir, "%s (%d).md" % (safe_filename(meta["title"]), n))
            with open(path, "w", encoding="utf-8") as f:
                f.write(to_markdown(meta))
            ok.append({"url": url, "path": path, "title": meta["title"],
                       "chars": len(meta["body"])})
            log("[%d] ✓ %s（%d 字）" % (i, meta["title"][:30], len(meta["body"])))
        except Exception as e:  # noqa: BLE001
            fail.append({"url": url, "error": str(e)[:200]})
            log("[%d] ✗ %s → %s" % (i, url[:60], str(e)[:100]))
        time.sleep(delay)
    return {"ok": ok, "fail": fail}


def import_local(files, out_dir, image_placeholder="<!-- 配图 -->", log=print) -> dict:
    """从本地 HTML/文本文件导入（用户自己用浏览器另存为的页面）。"""
    os.makedirs(out_dir, exist_ok=True)
    ok, fail = [], []
    for path in files:
        try:
            raw = open(path, "rb").read().decode("utf-8", "replace")
            if path.lower().endswith((".html", ".htm")):
                meta = parse_article(raw, "")
            else:
                meta = {"title": os.path.splitext(os.path.basename(path))[0],
                        "account": "", "published_at": "", "source": "",
                        "body": _clean_markdown(raw)}
            out = os.path.join(out_dir, safe_filename(meta["title"]) + ".md")
            with open(out, "w", encoding="utf-8") as f:
                f.write(to_markdown(meta))
            ok.append({"path": path, "saved": out, "title": meta["title"]})
            log("✓ %s" % meta["title"][:40])
        except Exception as e:  # noqa: BLE001
            fail.append({"path": path, "error": str(e)[:200]})
    return {"ok": ok, "fail": fail}


def import_paths(paths, out_dir, image_placeholder="<!-- 配图 -->", log=print) -> dict:
    """已有文章：直接从本地路径导入（支持 .md，也可以是一个目录）。

    只收 .md：用户自己的语料格式五花八门，全兼容等于全不兼容。
    如果是 .md 但没有 frontmatter，就补一份最小的，保证后续流水线能读。
    """
    files, ok, fail = [], [], []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                for n in names:
                    if n.lower().endswith(".md"):
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p):
            if p.lower().endswith(".md"):
                files.append(p)
            else:
                fail.append({"path": p, "error": "只支持 .md，请先自己转成 Markdown"})
        else:
            fail.append({"path": p, "error": "路径不存在"})

    os.makedirs(out_dir, exist_ok=True)
    for src in files:
        try:
            text = open(src, encoding="utf-8", errors="replace").read()
            if not text.lstrip().startswith("---"):
                name = os.path.splitext(os.path.basename(src))[0]
                text = ('---\ntitle: "%s"\naccount: ""\npublished_at: ""\nsource: "%s"\n---\n\n'
                        % (name.replace('"', "'"), src.replace('"', "'"))) + text
            # 顺手做一遍图片/链接处理，和抓取路径保持一致
            fm, body = _split_frontmatter(text)
            body, n_img = _Body_image(body, image_placeholder)
            dst = os.path.join(out_dir, safe_filename(os.path.splitext(os.path.basename(src))[0]) + ".md")
            k = 1
            while os.path.exists(dst):
                k += 1
                dst = os.path.join(out_dir, safe_filename(os.path.splitext(os.path.basename(src))[0]) + " (%d).md" % k)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(fm + "\n" + body.strip() + "\n")
            ok.append({"path": src, "saved": dst, "chars": len(body), "images": n_img})
            log("✓ %s（%d 字）" % (os.path.basename(src), len(body)))
        except Exception as e:  # noqa: BLE001
            fail.append({"path": src, "error": str(e)[:200]})
            log("✗ %s → %s" % (src, str(e)[:80]))
    return {"ok": ok, "fail": fail, "scanned": len(files)}


def _split_frontmatter(text):
    if text.startswith("---"):
        m = re.match(r"^---\n.*?\n---\n?", text, re.S)
        if m:
            return text[: m.end()].rstrip("\n"), text[m.end():]
    return "", text


def _Body_image(body, placeholder):
    """本地 md 里可能还有 markdown 图片或 img 标签，统一换成占位符。"""
    n = 0
    def sub_md(_):
        nonlocal n
        n += 1
        return placeholder
    def sub_html(_):
        nonlocal n
        n += 1
        return placeholder
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", sub_md, body)
    body = re.sub(r"<img\b[^>]*>", sub_html, body, flags=re.I)
    return body, n


if __name__ == "__main__":  # 手工调试用
    import sys

    urls = [u for u in sys.argv[1:] if u.startswith("http")]
    if urls:
        print(json.dumps(import_urls(urls, "my-articles"), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
