#!/usr/bin/env python3
"""语料清洗：把下载/导入的文章整理成适合训练的样子。

只做三件事，刻意保持简单：

1. 配图 → 占位符        （图床 URL 会变、对学文风没价值）
2. 超链接 → 只留文字     （跳转参数是噪音，还容易让模型学会输出 URL）
3. 自定义特征删除：
   - 删除匹配到的全部内容（可选整行）
   - 删除某个特征之前的全部内容
   - 删除某个特征之后的全部内容

第 3 条故意只提供这三个动作。用户文章里的固定导流语（"关注+星标"、
"点个在看"）各不相同，写死规则没有意义，让用户自己给"特征"就行。

安全阀
------
批量清洗最怕一条规则把整批文章砍没了。所以：
- 每条规则命中后，如果正文长度掉到原来的 50% 以下，就认为这条规则对该篇不适用，跳过并汇报；
- 首次写入前把原文备份到 `data/originals/`，随时可还原。
"""

from __future__ import annotations

import os
import re
import shutil

IMG_MD = re.compile(r"!\[[^\]]*\]\([^)]*\)")
IMG_HTML = re.compile(r"<img\b[^>]*>", re.I)
LINK_MD = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
LINK_HTML = re.compile(r"<a\b[^>]*>(.*?)</a>", re.I | re.S)
BARE_URL = re.compile(r"https?://[^\s<>\"')]+")

DEFAULT_RULES = [
    {"id": "img", "enabled": True, "placeholder": "<!-- 配图 -->"},
    {"id": "link", "enabled": True},
    {"id": "feat", "enabled": False, "mode": "remove", "text": "", "line": True},
]


def _frontmatter_split(text):
    """拆出 --- 头，正文单独处理，避免把 source 字段里的 URL 删掉。"""
    if text.startswith("---"):
        m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
        if m:
            return text[: m.end()], text[m.end():]
    return "", text


def rule_images(body, placeholder="<!-- 配图 -->"):
    n = 0

    def repl_md(_):
        nonlocal n
        n += 1
        return placeholder

    def repl_html(_):
        nonlocal n
        n += 1
        return placeholder

    body = IMG_MD.sub(repl_md, body)
    body = IMG_HTML.sub(repl_html, body)
    return body, n


def rule_links(body):
    """保留链接文字，删掉 URL 和裸链接。"""
    n = 0

    def repl_md(m):
        nonlocal n
        n += 1
        return m.group(1)

    body = LINK_MD.sub(repl_md, body)
    body = LINK_HTML.sub(lambda m: m.group(1), body)
    body, k = BARE_URL.subn("", body)
    n += k
    return body, n


def rule_feature(body, mode="remove", text="", whole_line=True):
    """按用户给的特征处理：删除 / 删除之前 / 删除之后。"""
    if not text:
        return body, 0
    hits = body.count(text)
    if hits == 0:
        return body, 0

    if mode == "before":
        i = body.index(text)
        return body[i:], 1
    if mode == "after":
        i = body.index(text)
        return body[:i], 1

    if whole_line:
        out = []
        for ln in body.split("\n"):
            if text in ln:
                continue
            out.append(ln)
        return "\n".join(out), hits
    return body.replace(text, ""), hits


def _tidy_punct(body):
    body = re.sub(r"([，。、；：！？])\1+", r"\1", body)
    body = re.sub(r"[，、；：]+([。！？])", r"\1", body)
    body = re.sub(r"。([，、；：])", r"。", body)
    out = []
    for ln in body.split("\n"):
        out.append(re.sub(r"^[，、；：。]+", "", ln))
    return "\n".join(out)


def apply_rules(text, rules, image_placeholder="<!-- 配图 -->", min_keep=0.5, guard_min_len=80):
    """对单篇文章跑一遍规则。

    返回 (新正文, 命中统计, 被安全阀拦下的规则名或 None)。

    只有「自定义特征删除」会触发安全阀：图片/链接替换本来就会让长度变化，
    而且 URL 越多掉得越狠，属于正常现象，不该拦。
    """
    fm, body = _frontmatter_split(text)
    original_len = max(1, len(body.strip()))
    stats, blocked = {}, None

    for r in rules:
        if not r.get("enabled"):
            continue
        rid = r.get("id")
        before = body

        if rid == "img":
            body, n = rule_images(body, r.get("placeholder") or image_placeholder)
        elif rid == "link":
            body, n = rule_links(body)
        elif rid == "feat":
            feat = (r.get("text") or "").strip()
            whole = bool(r.get("line", True))
            body, n = rule_feature(body, r.get("mode", "remove"), feat, whole)
            if n and not whole and feat:
                # 删掉句子后常留下孤立的逗号句号，收拾一下
                body = _tidy_punct(body)
            kept = len(body.strip())
            if n and original_len >= guard_min_len and kept < original_len * min_keep:
                body, n = before, 0          # 只回退这一条规则，前两条的成果保留
                blocked = rid
        else:
            continue

        if n:
            stats[rid] = stats.get(rid, 0) + n

    if len(re.sub(r"\s", "", body)) < 20 and len(re.sub(r"\s", "", fm + "\n" + _frontmatter_split(text)[1])) > 80:
        return text, {}, "feat"      # 洗成空文章了，整篇回退

    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r" +([，。！？；：、）」』])", r"\1", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return fm + "\n" + body + "\n", stats, blocked


def run(corpus_dir, rules, image_placeholder="<!-- 配图 -->",
        backup_dir=None, dry_run=False, log=print):
    """对目录下所有 .md 批量清洗。dry_run=True 只统计不落盘。"""
    files = sorted(f for f in os.listdir(corpus_dir) if f.endswith(".md"))
    report = {"total": len(files), "changed": 0, "blocked": [], "files": [], "hits": {}}

    if not dry_run and backup_dir:
        os.makedirs(backup_dir, exist_ok=True)

    for name in files:
        path = os.path.join(corpus_dir, name)
        text = open(path, encoding="utf-8").read()
        new, stats, blocked = apply_rules(text, rules, image_placeholder)
        if blocked:
            report["blocked"].append({"file": name, "rule": blocked})
            report["files"].append({"file": name, "blocked": blocked, "hits": stats})
            log("⚠ %s：规则 %s 会砍掉过半正文，已跳过" % (name[:40], blocked))
            continue
        changed = new != text
        if changed:
            report["changed"] += 1
            for k, v in stats.items():
                report["hits"][k] = report["hits"].get(k, 0) + v
            if not dry_run:
                if backup_dir:
                    bak = os.path.join(backup_dir, name)
                    if not os.path.exists(bak):
                        shutil.copy2(path, bak)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
        report["files"].append({"file": name, "changed": changed, "hits": stats})

    log("%s：%d 篇，改动 %d 篇，跳过 %d 篇"
        % ("试运行" if dry_run else "清洗完成", report["total"], report["changed"], len(report["blocked"])))
    return report


def restore(corpus_dir, backup_dir, log=print):
    """从备份还原（只还原有备份的）。"""
    if not os.path.isdir(backup_dir):
        return {"restored": 0}
    n = 0
    for name in os.listdir(backup_dir):
        src = os.path.join(backup_dir, name)
        if os.path.isfile(src) and name.endswith(".md"):
            shutil.copy2(src, os.path.join(corpus_dir, name))
            n += 1
    log("已还原 %d 篇" % n)
    return {"restored": n}


def preview(corpus_dir, rules, image_placeholder="<!-- 配图 -->", limit=3, chars=600):
    """给 UI 用：挑几篇示例，返回清洗前后片段。"""
    out = []
    for name in sorted(os.listdir(corpus_dir)):
        if not name.endswith(".md"):
            continue
        text = open(os.path.join(corpus_dir, name), encoding="utf-8").read()
        new, stats, blocked = apply_rules(text, rules, image_placeholder)
        out.append({
            "file": name,
            "before": text[:chars],
            "after": new[:chars],
            "hits": stats,
            "blocked": blocked,
            "changed": new != text,
        })
        if len(out) >= limit:
            break
    return out
