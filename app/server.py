#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公众号文风微调 —— 本地零依赖的配对语料生产工具

用法：
    python3 app/server.py            # 默认 http://127.0.0.1:8765
    python3 app/server.py --port 9000

依赖：Python 3.9+，只用标准库。所有模型调用走 OpenAI 兼容的 /chat/completions。
"""

import argparse
import concurrent.futures as futures
import hashlib
import json
import random
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLES_DIR = os.environ.get("CW_CORPUS_DIR") or os.path.join(ROOT, "my-articles")
PROMPTS_DIR = os.path.join(ROOT, "prompts")
DATA_DIR = os.path.join(ROOT, "data")
WORK_DIR = os.path.join(DATA_DIR, "work")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
STATIC_DIR = os.path.join(ROOT, "app", "static")

# 产品层：导入 / 清洗 / 数据集 / 训练。失败不阻断语料流水线。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import studio  # noqa: E402
except Exception as _e:  # noqa: BLE001
    studio = None
    sys.stderr.write("[warn] studio 模块加载失败：%s\n" % _e)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")

for d in (DATA_DIR, WORK_DIR, EXPORT_DIR):
    os.makedirs(d, exist_ok=True)

LOCK = threading.RLock()

DEFAULT_SYSTEM_PROMPT = (
    "你是一位公众号作者，长期写 AI 工具、产品经理转型、私域与平台生态观察，"
    "读者是想用 AI 提效的一线从业者。\n"
    "你会拿到一篇同主题的通用稿。把它改写成你自己的写法：短段落、直接对读者说话、"
    "有具体数字和真实判断、不装、不堆术语，该吐槽的地方就吐槽；拒绝总结段和套话。\n"
    "保留原文全部事实、数字与专名，不要新增任何事实。"
)

DEFAULT_INSTRUCTIONS = [
    "按你自己的写法改写这篇文章。",
    "这篇稿子太像通稿了，用你的风格重写一遍。",
    "把下面这篇文章改成你平时发公众号的样子。",
    "照着这个内容，写一篇你自己的版本。",
    "这篇内容是对的，但读起来不像我写的，改。",
    "用你的口吻把这篇文章重新写一遍，事实别动。",
]

DEFAULT_WRITERS = [
    {"id": "w_ds_generic", "provider": "deepseek", "model": "", "style": "generic", "voice": "impersonal", "temperature": 0.8, "enabled": True},
    {"id": "w_ds_news", "provider": "deepseek", "model": "", "style": "news", "voice": "impersonal", "temperature": 0.7, "enabled": True},
    {"id": "w_mimo_generic", "provider": "mimo", "model": "", "style": "generic", "voice": "flat_first", "temperature": 0.8, "enabled": True},
    {"id": "w_mimo_academic", "provider": "mimo", "model": "", "style": "academic", "voice": "impersonal", "temperature": 0.7, "enabled": True},
    {"id": "w_doubao_consult", "provider": "doubao", "model": "", "style": "consult", "voice": "impersonal", "temperature": 0.8, "enabled": True},
    {"id": "w_doubao_marketing", "provider": "doubao", "model": "", "style": "marketing", "voice": "flat_first", "temperature": 1.0, "enabled": True},
]

DEFAULT_CONFIG = {
    "providers": [
        {
            "id": "deepseek",
            "label": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "models": ["deepseek-chat", "deepseek-reasoner"],
            "json_mode": True,
            "disable_thinking": True,
            "reasoning": False,
            "enabled": True,
        },
        {
            "id": "doubao",
            "label": "豆包 · 火山方舟",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "",
            "models": [""],
            "json_mode": False,
            "disable_thinking": True,
            "reasoning": False,
            "enabled": True,
        },
        {
            "id": "mimo",
            "label": "MiMo",
            "base_url": "",
            "api_key": "",
            "models": [""],
            "json_mode": False,
            "disable_thinking": True,
            "reasoning": False,
            "enabled": True,
        },
        {
            "id": "mock",
            "label": "Mock（离线自测，不花钱）",
            "base_url": "mock://local",
            "api_key": "",
            "models": ["mock-1"],
            "json_mode": True,
            "disable_thinking": False,
            "reasoning": False,
            "enabled": True,
        },
    ],
    "roles": {
        "classify": {"provider": "deepseek", "model": "deepseek-chat", "temperature": 0.1},
        "brief": {"provider": "deepseek", "model": "deepseek-chat", "temperature": 0.3},
        "destyle": {"provider": "deepseek", "model": "deepseek-chat", "temperature": 0.3},
        "judge": {"provider": "doubao", "model": "", "temperature": 0.1},
    },
    "judge_policy": "cross",          # cross = 裁判尽量不同于写手；fixed = 永远用 roles.judge
    "writers": DEFAULT_WRITERS,
    "settings": {
        "min_year": 2023,
        "concurrency": 3,
        "drafts_per_article": 2,
        "structure_override_rate": 0.12,
        "request_timeout": 600,
        "max_tokens_cap": 32000,
        "auto_repair_rounds": 1,
    },
    "export": {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "instructions": DEFAULT_INSTRUCTIONS,
        "only_accepted": True,
        "strip_promo": True,
        "segment": True,
        "segment_min": 2000,
        "segment_max": 3000,
    },
}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 补齐缺失字段
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = json.loads(json.dumps(v))
    if not cfg.get("providers"):
        cfg["providers"] = json.loads(json.dumps(DEFAULT_CONFIG["providers"]))
    return cfg


def save_config(cfg):
    seen = {}
    for p in cfg.get("providers", []):
        p["api_key"] = (p.get("api_key") or "").strip()
        p["base_url"] = (p.get("base_url") or "").strip()
        p["models"] = [str(m).strip() for m in (p.get("models") or []) if str(m).strip()]
        seen[p.get("id")] = p
    cfg["providers"] = list(seen.values())
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def mask_config(cfg):
    out = json.loads(json.dumps(cfg))
    for p in out.get("providers", []):
        key = p.get("api_key") or ""
        p["api_key_masked"] = (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("已设置" if key else "")
        p["api_key"] = ""
        p["has_key"] = bool(key)
    return out


def provider_by_id(cfg, pid):
    for p in cfg.get("providers", []):
        if p.get("id") == pid:
            return p
    raise KeyError(f"provider not found: {pid}")


def role_of(cfg, name):
    r = cfg.get("roles", {}).get(name) or {}
    pid = r.get("provider") or "deepseek"
    p = provider_by_id(cfg, pid)
    model = r.get("model") or (p.get("models") or [""])[0]
    return p, model, float(r.get("temperature", 0.2))


# --------------------------------------------------------------------------
# 文章扫描与清洗
# --------------------------------------------------------------------------

def article_id(filename):
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()[:12]


def parse_article(path):
    raw = open(path, "r", encoding="utf-8", errors="replace").read()
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n?", raw, re.S)
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
        raw = raw[m.end():]
    image_slots = len(re.findall(r"<!--\s*文章配图\s*-->", raw))
    body = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    body = re.sub(r"^#\s+.*$", "", body, flags=re.M)          # 去掉 H1（标题单独存）
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    # 只清空格行，绝不动标点、中英空格、破折号
    meta_title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
    published = meta.get("published_at", "")
    year = None
    my = re.match(r"(\d{4})", published)
    if my:
        year = int(my.group(1))
    else:
        my = re.search(r"(20\d{2})", os.path.basename(path))
        year = int(my.group(1)) if my else None
    chars = len(re.sub(r"\s", "", body))
    return {
        "title": meta_title,
        "account": meta.get("account", ""),
        "published_at": published,
        "year": year,
        "source": meta.get("source", ""),
        "body": body,
        "chars": chars,
        "image_slots": image_slots,
    }


_INDEX_CACHE = {"mtime": 0, "items": []}


def scan_articles(force=False):
    os.makedirs(ARTICLES_DIR, exist_ok=True)   # 首次运行自动建目录
    with LOCK:
        files = sorted(f for f in os.listdir(ARTICLES_DIR) if f.endswith(".md"))
        stamp = max([os.path.getmtime(os.path.join(ARTICLES_DIR, f)) for f in files] or [0])
        if not force and _INDEX_CACHE["items"] and _INDEX_CACHE["mtime"] >= stamp:
            return _INDEX_CACHE["items"]
        items = []
        for f in files:
            path = os.path.join(ARTICLES_DIR, f)
            try:
                a = parse_article(path)
            except Exception as e:  # noqa: BLE001
                print(f"[scan] skip {f}: {e}")
                continue
            aid = article_id(f)
            st = work_status(aid)
            items.append({
                "id": aid,
                "file": f,
                "title": a["title"],
                "year": a["year"],
                "published_at": a["published_at"],
                "chars": a["chars"],
                "image_slots": a["image_slots"],
                "theme": a["body"],
                "has_work": st is not None,
                "status": st or status_of(None),
            })
        _INDEX_CACHE["items"] = items
        _INDEX_CACHE["mtime"] = stamp
        return items


def status_of(w):
    if not w:
        return {"classified": False, "brief": False, "destyle": False,
                "drafts": 0, "judged": 0, "accepted": 0}
    drafts = w.get("drafts", [])
    return {
        "classified": bool(w.get("classify")),
        "brief": bool(w.get("brief")),
        "destyle": bool(w.get("brief_destyle")),
        "drafts": len(drafts),
        "judged": sum(1 for d in drafts if d.get("judge")),
        "accepted": sum(1 for d in drafts if (d.get("judge") or {}).get("verdict") == "accept"),
    }


_WORK_STATUS_CACHE = {}


def work_status(aid):
    """按 work 文件 mtime 缓存状态，供列表接口实时刷新。"""
    p = work_path(aid)
    if not os.path.exists(p):
        return None
    mt = os.path.getmtime(p)
    c = _WORK_STATUS_CACHE.get(aid)
    if c and c[0] == mt:
        return c[1]
    w = load_work(aid, create=False) or {}
    st = status_of(w)
    _WORK_STATUS_CACHE[aid] = (mt, st)
    return st


def work_path(aid):
    return os.path.join(WORK_DIR, f"{aid}.json")


def load_work(aid, create=True):
    p = work_path(aid)
    if not os.path.exists(p):
        return {} if create else None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {} if create else None


def save_work(aid, w):
    w["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = work_path(aid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False, indent=2)
    os.replace(tmp, work_path(aid))


def promo_cut(body, classify):
    """按分类结果里能定位到的推广段，保守地切掉文末推广块。"""
    segs = ((classify or {}).get("promo") or {}).get("segments") or []
    cut_chars = 0
    text = body
    for s in segs:
        if s.get("keep"):
            continue
        q = (s.get("quote") or "").strip()
        if len(q) < 8:
            continue
        i = text.find(q)
        if i < 0:
            # 允许半句匹配
            q2 = q[:12]
            i = text.find(q2) if len(q2) >= 8 else -1
        if i < 0:
            continue
        # 从该段开头切到结尾（只在文末 35% 区域内才切，避免误伤正文）
        if i < len(text) * 0.65:
            continue
        text = text[:i].rstrip()
        text = re.sub(r"[\s\-—>]+$", "", text)
        cut_chars = len(body) - len(text)
    return text, cut_chars


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------

def norm_ws(s):
    return re.sub(r"\s+", "", s or "")


_MASK = (1 << 64) - 1


def _prefix(s, base=1000003):
    pre = [0] * (len(s) + 1)
    pw = [1] * (len(s) + 1)
    h = 0
    for i, ch in enumerate(s):
        h = (h * base + ord(ch)) & _MASK
        pre[i + 1] = h
        pw[i + 1] = (pw[i] * base) & _MASK
    return pre, pw


def longest_common_substring(a, b):
    """二分 + 滚动哈希求最长公共子串长度，O(n log n)。"""
    a, b = norm_ws(a), norm_ws(b)
    if not a or not b:
        return 0, ""
    lo, hi = 0, min(len(a), len(b))
    preA, pwA = _prefix(a)
    preB, pwB = _prefix(b)
    best = 0

    def hashes(pre, pw, L):
        out = {}
        for i in range(len(pre) - L):
            h = (pre[i + L] - pre[i] * pw[L]) & _MASK
            out.setdefault(h, []).append(i)
        return out

    while lo <= hi:
        mid = (lo + hi) // 2
        if mid == 0:
            lo = 1
            continue
        hb = hashes(preB, pwB, mid)
        found = -1
        for i in range(len(preA) - mid):
            h = (preA[i + mid] - preA[i] * pwA[mid]) & _MASK
            if h in hb:
                found = i
                break
        if found >= 0:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best == 0:
        return 0, ""
    # 精确找回文本（哈希可能碰撞，取第一个命中做校验）
    for i in range(len(a) - best + 1):
        seg = a[i:i + best]
        if seg in b:
            return best, seg
    return 0, ""


def ngram_jaccard(a, b, n=4):
    a, b = norm_ws(a), norm_ws(b)
    A = {a[i:i + n] for i in range(max(0, len(a) - n + 1))}
    B = {b[i:i + n] for i in range(max(0, len(b) - n + 1))}
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def numbers_of(s):
    return set(re.findall(r"\d+(?:\.\d+)?", s or ""))


def style_profile(text):
    text = text or ""
    chars = len(norm_ws(text))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    paras = [l for l in lines if not l.startswith("#")]
    bullets = [l for l in lines if re.match(r"^([-*+]|\d+[.、])\s", l)]
    sents = re.split(r"[。！？!?；;]", text)
    sents = [s for s in sents if len(s.strip()) > 1]
    single = sum(1 for p in paras if len(re.split(r"[。！？!?]", p)) <= 2)
    return {
        "chars": chars,
        "paras": len(paras),
        "avg_para": round(chars / max(1, len(paras)), 1),
        "avg_sent": round(sum(len(s) for s in sents) / max(1, len(sents)), 1),
        "bullet_ratio": round(len(bullets) / max(1, len(lines)), 3),
        "h2": len(re.findall(r"^##\s", text, re.M)),
        "wo": len(re.findall("我", text)),
        "ni": len(re.findall("你", text)),
        "dash": len(re.findall("——", text)),
        "question": len(re.findall("[？?]", text)),
        "exclaim": len(re.findall("[！!]", text)),
        "cjk_latin_space": len(re.findall(r"[\u4e00-\u9fa5] [A-Za-z0-9]", text)),
        "single_sent_ratio": round(single / max(1, len(paras)), 3),
    }


def style_delta(y, x):
    py, px = style_profile(y), style_profile(x)
    keys = ["avg_para", "avg_sent", "bullet_ratio", "wo", "ni", "dash",
            "question", "exclaim", "cjk_latin_space", "single_sent_ratio"]
    d = {}
    total = 0.0
    for k in keys:
        vy, vx = py[k], px[k]
        denom = max(1.0, abs(vy))
        v = abs(vy - vx) / denom
        v = min(v, 3.0)
        d[k] = round(v, 3)
        total += v
    return {"profile_original": py, "profile_draft": px, "per_dim": d,
            "score": round(total / len(keys), 3)}


def protected_terms(brief):
    """这些是「本来就该逐字一致」的内容：工具名、专名、事实里被引用的原话。"""
    terms = [t for t in (brief.get("keep_terms") or []) if isinstance(t, str) and len(t) >= 3]
    for sec in brief.get("sections") or []:
        for f in sec.get("facts") or []:
            for q in re.findall(r"[「“\"]([^」”\"]{4,})[」”\"]", f.get("content") or ""):
                terms.append(q)
    return sorted(set(terms), key=len, reverse=True)


def mask_terms(text, terms, filler="\u203b"):
    if not terms:
        return text
    out = text
    for t in terms:
        if len(t) >= 3 and t in out:
            out = out.replace(t, filler * len(t))
    return out


_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,:;!?'\"()\-_/&+<>=@#%]{5,}")


def mask_latin(text):
    """英文串（工具名、模型名、英文口号）不是中文文风，算重合没有意义。"""
    return _LATIN_RUN.sub(lambda m: "\u203b" * len(m.group(0)), text)


def common_span(original, draft, protected=None):
    """最长连续重合，但排除受保护的专名/引用——否则工具名重合会被误判成抄风格。"""
    o = norm_ws(original)
    d = mask_latin(mask_terms(norm_ws(draft), protected or []))
    L, txt = longest_common_substring(o, d)
    if txt and "\u203b" in txt:
        L, txt = 0, ""
    return L, txt


def _term_key(t):
    return re.sub(r"\s+", "", (t or "")).lower()


def _missing_terms(draft, keep_terms):
    """保留词要求原样出现；大小写与空格差异不算没写。"""
    if not keep_terms:
        return []
    d = _term_key(draft)
    return [t for t in keep_terms if len(t) >= 4 and _term_key(t) not in d][:20]


def compare(original, draft, protected=None, keep_terms=None):
    lcs_len, lcs_txt = common_span(original, draft, protected)
    ny, nx = len(norm_ws(original)), len(norm_ws(draft))
    dy, dx = numbers_of(original), numbers_of(draft)
    return {
        "chars_original": ny,
        "chars_draft": nx,
        "length_ratio": round(nx / max(1, ny), 3),
        "lcs": lcs_len,
        "lcs_text": lcs_txt[:120],
        "jaccard4": round(ngram_jaccard(mask_terms(norm_ws(original), protected or []),
                                        mask_terms(norm_ws(draft), protected or [])), 4),
        # 用「是否作为子串出现在原文」判断，避免日期/时间被拆碎后误报
        "numbers_extra": [n for n in sorted(dx - dy) if n not in norm_ws(original)][:40],
        "numbers_missing": [n for n in sorted(dy - dx) if n not in norm_ws(draft)][:40],
        "style": style_delta(original, draft),
        "protected_terms": len(protected or []),
        "keep_terms_missing": _missing_terms(draft, keep_terms),
    }


def gate(metrics, settings):
    ok, reasons = True, []
    lr = metrics["length_ratio"]
    if not (0.7 <= lr <= 1.35):
        ok = False
        reasons.append(f"长度比 {lr} 越界")
    if metrics["lcs"] > 25:
        ok = False
        reasons.append(f"最长公共子串 {metrics['lcs']} 字过长")
    if metrics["jaccard4"] > 0.2:
        ok = False
        reasons.append(f"4-gram 重合 {metrics['jaccard4']} 过高")
    if metrics["numbers_extra"]:
        ok = False
        reasons.append(f"多出 {len(metrics['numbers_extra'])} 个原文没有的数字")
    return ok, reasons


# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------

PROMPT_FILES = {
    "classify": "01-classify.md",
    "brief": "02-brief.md",
    "destyle": "03-brief-destyle.md",
    "destyle_patch": "03b-destyle-patch.md",
    "writer": "04-writer.md",
    "personas": "04b-personas.md",
    "judge": "05-judge.md",
    "repair": "06-repair.md",
}

STYLE_LABELS = {
    "generic": "普通内容编辑",
    "news": "科技资讯号编辑",
    "academic": "研究机构分析员",
    "consult": "咨询顾问",
    "marketing": "新媒体运营",
}


def _split_sections(md):
    """按二级标题切分，返回 {标题: 内容}。"""
    parts = re.split(r"^##\s+(.+)$", md, flags=re.M)
    out = {}
    if parts and parts[0].strip():
        out["_head"] = parts[0].strip()
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip()] = parts[i + 1].strip("\n")
    return out


def load_prompt(name, cfg=None):
    path = os.path.join(PROMPTS_DIR, PROMPT_FILES[name])
    md = open(path, "r", encoding="utf-8").read()
    version = 0
    for line in md.split("\n")[:12]:
        m = re.match(r"version:\s*(\d+)", line.strip())
        if m:
            version = int(m.group(1))
    secs = _split_sections(md)
    system = ""
    for k in secs:
        if k.lower().startswith("system"):
            system = secs[k]
            break
    # User 之后的所有内容都属于用户消息
    idx = re.search(r"^##\s+User\s*$", md, flags=re.M)
    user = md[idx.end():].strip("\n") if idx else ""
    return {"system": system, "user": user, "version": version}


def personas(cfg=None):
    path = os.path.join(PROMPTS_DIR, PROMPT_FILES["personas"])
    md = open(path, "r", encoding="utf-8").read()
    secs = _split_sections(md)
    out = {}
    for k, v in secs.items():
        m = re.match(r"`([a-z_]+)`", k)
        if m:
            out[m.group(1)] = v
    return out


VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render(tmpl, vars_):
    def sub(m):
        return str(vars_.get(m.group(1), ""))
    return VAR_RE.sub(sub, tmpl)


# --------------------------------------------------------------------------
# LLM 调用
# --------------------------------------------------------------------------

class LLMError(Exception):
    pass


TOKEN_CAP = 32000          # 单次输出 token 硬上限，推理模型会按需从初始值涨到这里


def _msg_of(resp):
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):          # 部分兼容层把正文拆成分段
        content = "".join((x.get("text") or "") for x in content if isinstance(x, dict))
    content = (content or "").strip()
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    return content, ch.get("finish_reason"), len(reasoning or "")


def _post(url, headers, payload, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:800]
        except Exception:
            pass
        raise LLMError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"网络错误: {e.reason}") from e
    except TimeoutError as e:
        raise LLMError("请求超时") from e


def chat_url(base_url):
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise LLMError("Base URL 为空：请填到 /v1 或 /api/v3 这一级（不要自己拼 /chat/completions）")
    if b.startswith("mock://"):
        return b
    if not re.match(r"^https?://", b):
        raise LLMError(f"Base URL 不合法：「{b}」，必须以 http:// 或 https:// 开头")
    if b.endswith("/chat/completions"):
        return b
    return b + "/chat/completions"


def call_llm(provider, model, messages, temperature=0.3, max_tokens=4000,
             json_mode=False, timeout=300):
    """OpenAI 兼容调用。对推理模型做了两件事：
    1) 正文为空且 finish_reason=length 时，自动把 max_tokens 翻倍重试（直到 TOKEN_CAP）；
    2) 仍然为空就报一条能看懂的错，而不是丢一个空字符串给下游。
    """
    if (provider.get("base_url") or "").startswith("mock://"):
        return mock_reply(messages, json_mode=json_mode)
    if not provider.get("api_key"):
        raise LLMError(f"{provider.get('label')} 未配置 API Key")
    if not (model or (provider.get("models") or [""])[0]):
        raise LLMError(f"{provider.get('label')} 未配置模型 ID（在配置页填）")
    payload = {
        "model": model or (provider.get("models") or [""])[0],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if provider.get("disable_thinking"):
        payload["thinking"] = {"type": "disabled"}   # 各家兼容层通用写法
    if provider.get("reasoning") and not provider.get("disable_thinking"):
        # max_tokens 只是上限，不影响计费；推理模型先思考再输出，给太小会只剩思考、正文为空
        payload["max_tokens"] = max(payload["max_tokens"], TOKEN_CAP)
    if json_mode and provider.get("json_mode"):
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider['api_key']}",
    }
    last = None
    http_tries = 0
    empty_tries = 0
    while True:
        try:
            resp = _post(chat_url(provider["base_url"]), headers, payload, timeout)
            content, finish, rlen = _msg_of(resp)
            if finish == "length":
                if payload["max_tokens"] < TOKEN_CAP:
                    bumped = min(TOKEN_CAP, payload["max_tokens"] * 2)
                    print(f"[llm] {provider.get('id')}/{payload['model']} 输出被截断"
                          f"（已出 {len(content)} 字），max_tokens {payload['max_tokens']} → {bumped}")
                    payload["max_tokens"] = bumped
                    continue
                raise LLMError(
                    f"{provider.get('label')} 的输出达到 token 上限仍没写完（已出 {len(content)} 字）。"
                    f"把「单次输出 token 上限」调大，或把文章拆短。")
            if content:
                return content
            empty_tries += 1
            if empty_tries <= 1:
                time.sleep(1.0)
                continue
            raise LLMError(
                f"{provider.get('label')} 返回空正文（finish_reason={finish}，思考 {rlen} 字，"
                f"max_tokens={payload['max_tokens']}）。把「单次输出 token 上限」调大，"
                f"或换一个非推理模型做抽取/改写。")
        except LLMError as e:
            last = e
            if "空正文" in str(e) or "token 上限" in str(e):
                break                      # 这两类重试也没用，直接抛
            if "response_format" in str(e) and "response_format" in payload:
                payload.pop("response_format", None)
                continue
            if "HTTP 4" in str(e) and "rate" not in str(e).lower():
                break                      # 鉴权/参数类错误重试也没用
            http_tries += 1
            if http_tries >= 3:
                break
            time.sleep(1.5 * http_tries)
    raise LLMError(str(last))


def strip_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        for cand in (t[i:j + 1],):
            try:
                return json.loads(cand)
            except Exception:
                pass
        try:
            fixed = re.sub(r",\s*([}\]])", r"\1", t[i:j + 1])
            return json.loads(fixed)
        except Exception:
            pass
    raise LLMError("返回内容不是合法 JSON：" + t[:200])


def call_json(provider, model, messages, temperature, max_tokens, timeout):
    txt = call_llm(provider, model, messages, temperature, max_tokens,
                   json_mode=True, timeout=timeout)
    return strip_json(txt), txt


# --------------------------------------------------------------------------
# Mock（离线自测，验证整条链路）
# --------------------------------------------------------------------------

def mock_reply(messages, json_mode=False):
    system = messages[0]["content"] if messages else ""
    user = messages[-1]["content"] if len(messages) > 1 else ""
    time.sleep(0.4)
    if "数据标注员" in system:
        return json.dumps({
            "kind": ["干货教程"], "body_ratio": 0.88,
            "promo": {"has": True, "segments": [
                {"type": "课程/训练营", "quote": "扫码吧，后面还会涨", "approx_chars": 60, "keep": False}]},
            "structure_mode": "flow", "length_tier": "mid", "needs_split": False,
            "topic_tags": ["AI工具"], "pii": False, "train_role": "train",
            "reason": "", "has_code": False, "has_table": False, "warnings": [],
            "_mock": True,
        }, ensure_ascii=False)
    if "内容结构分析师" in system:
        m = re.search(r"标题：(.*)", user)
        title = m.group(1).strip() if m else "未命名"
        body = user
        claims = [s.strip() for s in re.split(r"[。\n]", body) if len(s.strip()) > 18][:12]
        return json.dumps({
            "topic": title[:12],
            "target_reader": "对 AI 工具感兴趣的从业者",
            "speaker": {"position": "有实操经验的从业者",
                        "first_person_claims": [{"claim": "作者实际操作过文中描述的方案", "kind": "个人操作"}]},
            "structure_mode": "flow",
            "sections": [{
                "order": 1, "function": "给结论", "topic_line": "说明文中方案的做法与结论",
                "min_chars": max(200, len(norm_ws(body))),
                "claims": claims[:8] or ["文中给出了一套可操作的做法"],
                "facts": [{"content": "文中出现的关键数字与工具名", "kind": "数据"}],
                "example_hint": "文中使用的实操例子",
                "punchline_intent": "这套做法值得试一次",
                "thin": False,
            }],
            "keep_terms": [], "cta": {"exists": True, "kind": "扫码", "message": "课程报名"},
            "length_target": max(300, len(norm_ws(body))), "image_slots": 0,
            "_mock": True,
        }, ensure_ascii=False)
    if "风格侦探" in system:
        brief = extract_block(user, "BRIEF")
        try:
            b = json.loads(brief)
        except Exception:
            b = {}
        b["_mock"] = True
        return json.dumps({
            "leaks": [], "style_residue_score": 1, "info_loss": [],
            "revised_brief": b, "notes": "mock 模式未做真实检查",
        }, ensure_ascii=False)
    if "质检员" in system:
        return json.dumps({
            "scores": {"same_topic": 5, "faithfulness": 5, "style_distance": 4,
                       "leak": 5, "length_fit": 4, "publishable": 4},
            "evidence": {"topic_drift": "无", "number_mismatch": [], "fabrications": [],
                         "omissions": [], "style_leaks": [], "longest_common_span": ""},
            "issues": [], "verdict": "accept", "repair_instructions": "",
            "_mock": True,
        }, ensure_ascii=False)
    # writer / repair
    brief = extract_block(user, "BRIEF")
    try:
        claims = json.loads(brief)["sections"][0]["claims"]
        target = json.loads(brief)["length_target"]
    except Exception:
        claims, target = ["这是一段占位内容"], 800
    target = int(min(max(target * 0.8, 400), 2500))
    out, i = [], 0
    while sum(len(x) for x in out) < target:
        c = claims[i % len(claims)]
        out.append(f"在当前的背景下，{c}。从实践来看，这一点值得关注。"
                   f"一方面，它降低了尝试的成本；另一方面，也为后续的优化留下了空间。")
        i += 1
        if i > 60:
            break
    return "\n\n".join(out) + "\n\n总的来说，这套做法有一定的参考价值，值得在实践中验证。"


def extract_block(text, tag):
    m = re.search(rf"<<<{tag}>>>\n(.*?)\n<<<{tag}>>>", text, re.S)
    if m:
        return m.group(1)
    m = re.search(rf"<<<{tag}>>>\n(.*?)\n<<<END>>>", text, re.S)
    if m:
        return m.group(1)
    # 兜底：取第一个 ```json 块
    m = re.search(r"```json\n(.*?)\n```", text, re.S)
    return m.group(1) if m else text


# --------------------------------------------------------------------------
# 流水线
# --------------------------------------------------------------------------

def get_article(aid):
    for a in scan_articles():
        if a["id"] == aid:
            full = parse_article(os.path.join(ARTICLES_DIR, a["file"]))
            full["id"] = aid
            full["file"] = a["file"]
            return full
    raise KeyError(f"article not found: {aid}")


def ensure_article(aid):
    w = load_work(aid)
    if not w:
        a = get_article(aid)
        w = {
            "id": aid, "file": a["file"], "title": a["title"],
            "published_at": a["published_at"], "year": a["year"],
            "source": a["source"],
            "body_original": a["body"], "chars": a["chars"],
            "flags": {"eval": False, "cut_promo": False, "skip": False},
            "drafts": [],
        }
        save_work(aid, w)
    return w


def target_body(w):
    body = w["body_original"]
    if w.get("flags", {}).get("cut_promo") and w.get("classify"):
        body, cut = promo_cut(body, w["classify"])
        w.setdefault("meta", {})["promo_cut_chars"] = cut
    return body


def run_classify(cfg, w, log):
    p, model, temp = role_of(cfg, "classify")
    t = load_prompt("classify")
    msgs = [
        {"role": "system", "content": render(t["system"], {})},
        {"role": "user", "content": render(t["user"], {
            "title": w["title"], "published_at": w.get("published_at", ""),
            "body": w["body_original"]})},
    ]
    data, raw = call_json(p, model, msgs, temp, 2000, settings(cfg)["request_timeout"])
    w["classify"] = data
    w.setdefault("prompt_versions", {})["classify"] = t["version"]
    log(f"分类完成：{','.join(data.get('kind', []))} · 正文占比 {data.get('body_ratio')}")
    return w


def validate_brief(data):
    """Brief 结构校验：空节、缺字段、节太少都算失败，别把垃圾交给写手。"""
    secs = data.get("sections")
    if not isinstance(secs, list) or not secs:
        return False, "sections 为空"
    good = [x for x in secs if isinstance(x, dict) and x.get("claims")]
    if len(good) < 2:
        return False, f"sections 里只有 {len(good)} 个有效节（共 {len(secs)}）"
    if not data.get("topic"):
        return False, "缺 topic"
    return True, ""


def run_brief(cfg, w, log):
    p, model, temp = role_of(cfg, "brief")
    t = load_prompt("brief")
    body = target_body(w)
    msgs = [
        {"role": "system", "content": render(t["system"], {})},
        {"role": "user", "content": render(t["user"], {
            "title": w["title"], "published_at": w.get("published_at", ""), "body": body})},
    ]
    data, raw = call_json(p, model, msgs, temp, 16000, settings(cfg)["request_timeout"])
    ok, why = validate_brief(data)
    if not ok:
        log(f"Brief 结构不合格（{why}），带提示重试一次")
        msgs = msgs + [
            {"role": "assistant", "content": raw[:2000]},
            {"role": "user", "content": f"上面这份输出不可用：{why}。"
                                        f"请重新输出完整 JSON：sections 至少 3 节，"
                                        f"每节都要有 order / claims（非空）/ facts / topic_line。"},
        ]
        data, raw = call_json(p, model, msgs, 0.2, 16000, settings(cfg)["request_timeout"])
        ok, why = validate_brief(data)
        if not ok:
            raise LLMError(f"Brief 两次都不合格：{why}")
    # 篇幅基准必须和实际目标正文一致，否则写手和裁判各按一个数算
    data["length_target"] = len(norm_ws(body))
    data["_length_target_extracted"] = data.get("length_target")
    w["brief"] = data
    w["brief_metrics"] = compare(body, style_surface(data), protected_terms(data))
    w.setdefault("prompt_versions", {})["brief"] = t["version"]
    log(f"Brief 完成：{len(data.get('sections', []))} 节 · 与原文最长重合 {w['brief_metrics']['lcs']} 字")
    return w


def style_strings(brief):
    """只挑「表达性字段」，facts / keep_terms 不在里面——那些本来就该原样保留。"""
    def add(path, val):
        if isinstance(val, str) and len(val.strip()) >= 8:
            return (path, val)
        return None
    for path, val in (("topic", brief.get("topic")),
                      ("target_reader", brief.get("target_reader")),
                      ("speaker.position", (brief.get("speaker") or {}).get("position")),
                      ("cta.message", (brief.get("cta") or {}).get("message"))):
        it = add(path, val)
        if it:
            yield it
    for i, sec in enumerate(brief.get("sections") or []):
        for key in ("topic_line", "punchline_intent", "example_hint"):
            it = add(f"sections[{i}].{key}", sec.get(key))
            if it:
                yield it
        for j, c in enumerate(sec.get("claims") or []):
            it = add(f"sections[{i}].claims[{j}]", c)
            if it:
                yield it


def style_surface(brief):
    """把 Brief 里"表达性字段"拼成一段文本，专供重合度指标使用。"""
    return "\n".join(t for _, t in style_strings(brief))


def style_leaks(brief, body, protected=None, threshold=12):
    out = []
    for path, text in style_strings(brief):
        L, span = common_span(body, text, protected)
        if L >= threshold:
            out.append({"path": path, "text": text, "lcs": L, "span": span})
    return out


_PATH_RE = re.compile(r"([a-zA-Z_]+)(?:\[(\d+)\])?")


def set_path(brief, path, value):
    """支持 sections[2].claims[1] / speaker.position / topic 这类路径。"""
    parts = [m for m in _PATH_RE.finditer(path)]
    if not parts:
        return False
    cur = brief
    for idx, m in enumerate(parts):
        key, sub = m.group(1), m.group(2)
        last = idx == len(parts) - 1
        if sub is None:
            if last:
                if isinstance(cur, dict):
                    cur[key] = value
                    return True
                return False
            cur = cur.get(key) if isinstance(cur, dict) else None
        else:
            i = int(sub)
            arr = cur.get(key) if isinstance(cur, dict) else None
            if not isinstance(arr, list) or i >= len(arr):
                return False
            if last:
                arr[i] = value
                return True
            cur = arr[i]
        if cur is None:
            return False
    return False


def run_destyle(cfg, w, log):
    """确定性字段级去味：脚本找出泄漏字段 → 只重写这些字段 → 复检，最多 3 轮。

    不用「整份 Brief 重写」的原因：整份重写会让模型顺手改变其他字段，且经常改不动。
    """
    p, model, temp = role_of(cfg, "destyle")
    body = target_body(w)
    brief = w.get("brief") or {}
    protected = protected_terms(brief)
    t = load_prompt("destyle_patch")
    rounds = []
    leaks = style_leaks(brief, body, protected)
    first_n = len(leaks)
    for r in range(4):
        if not leaks:
            break
        payload = leaks[:25]
        listing = "\n".join(
            f"{i+1}. [{x['path']}]\n   原句：{x['text']}\n   必须打断的片段：「{x['span']}」"
            for i, x in enumerate(payload))
        msgs = [
            {"role": "system", "content": render(t["system"], {})},
            {"role": "user", "content": render(t["user"], {
                "body": body,
                "leak_list": listing,
                "protected": "、".join(protected[:20]) or "（无）",
            })},
        ]
        try:
            data, _ = call_json(p, model, msgs, temp, 8000, settings(cfg)["request_timeout"])
        except LLMError as e:
            log(f"去风格化第 {r+1} 轮失败：{e}")
            break
        patches = data.get("patches") or []
        applied = 0
        for it in patches:
            path = (it.get("path") or "").strip()
            text = (it.get("text") or "").strip()
            if path and text and set_path(brief, path, text):
                applied += 1
        leaks_after = style_leaks(brief, body, protected)
        rounds.append({"round": r + 1, "leaks_before": len(leaks), "leaks_after": len(leaks_after),
                       "patches_model": len(patches), "patches_applied": applied})
        log(f"去风格化第 {r+1} 轮：泄漏 {len(leaks)} → {len(leaks_after)}（应用 {applied} 处改写）")
        leaks = leaks_after
        if applied == 0:
            break
    score = 1 if not leaks else (2 if len(leaks) <= 2 else (3 if len(leaks) <= 5 else 4))
    w["brief"] = brief
    w["brief_metrics"] = compare(body, style_surface(brief), protected)
    w["brief_destyle"] = {
        "leaks": leaks,
        "leaks_before": first_n,
        "style_residue_score": score,
        "info_loss": [],
        "rounds": rounds,
        "revised_brief": brief,
        "notes": f"字段级去味，{len(rounds)} 轮" if rounds else "无需改写",
    }
    w.setdefault("prompt_versions", {})["destyle"] = t["version"]
    log(f"去风格化完成：残留分 {score} · 剩余泄漏 {len(leaks)} 处（开始 {first_n} 处）")
    return w


def pick_writers(cfg, aid, n):
    ws = [x for x in cfg.get("writers", []) if x.get("enabled", True)]
    if not ws:
        raise LLMError("没有启用任何写手")
    seed = int(hashlib.sha1(aid.encode()).hexdigest()[:8], 16)
    ws = ws[seed % len(ws):] + ws[:seed % len(ws)]
    chosen = [ws[0]]
    sig = lambda w: (w.get("provider"), w.get("model") or "")
    for w in ws[1:]:
        if len(chosen) >= n:
            break
        if sig(w) not in {sig(c) for c in chosen}:
            chosen.append(w)
    for w in ws:                     # 不够就随便补齐
        if len(chosen) >= n:
            break
        if w not in chosen:
            chosen.append(w)
    return chosen[:n]


def run_draft(cfg, w, writer, log):
    p = provider_by_id(cfg, writer["provider"])
    model = writer.get("model") or (p.get("models") or [""])[0]
    t = load_prompt("writer")
    per = personas()
    body = target_body(w)
    brief = w.get("brief") or {}
    style = writer.get("style", "generic")
    voice = writer.get("voice", "impersonal")
    target = int(brief.get("length_target") or len(norm_ws(body)))
    override = "none"
    rate = float(settings(cfg).get("structure_override_rate") or 0)
    if rate > 0 and (int(hashlib.sha1((w["id"] + writer["id"]).encode()).hexdigest()[:6], 16) % 100) < rate * 100:
        override = "flatten" if brief.get("structure_mode") != "flow" else "sectionize"
    msgs = [
        {"role": "system", "content": render(t["system"], {"writer_style_label": STYLE_LABELS.get(style, style)})},
        {"role": "user", "content": render(t["user"], {
            "brief": json.dumps(brief, ensure_ascii=False, indent=2),
            "writer_style": style, "voice_mode": voice,
            "length_target": target, "structure_override": override,
            "persona_rules": per.get(style, per.get("generic", "")),
        })},
    ]
    txt = call_llm(p, model, msgs, float(writer.get("temperature", 0.8)), 12000,
                   json_mode=False, timeout=settings(cfg)["request_timeout"])
    txt = txt.strip()
    txt = re.sub(r"^```(?:markdown|md)?\s*", "", txt)
    txt = re.sub(r"\s*```$", "", txt)
    metrics = compare(body, txt, protected_terms(brief), brief.get("keep_terms"))
    ok, reasons = gate(metrics, settings(cfg))
    draft = {
        "key": f"{writer['id']}|{p['id']}|{model}|{style}|{voice}",
        "writer_id": writer["id"], "provider": p["id"], "provider_label": p.get("label"),
        "model": model, "style": style, "voice": voice,
        "temperature": writer.get("temperature"), "structure_override": override,
        "text": txt, "metrics": metrics, "gate": {"ok": ok, "reasons": reasons},
        "prompt_version": t["version"],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "judge": None, "repair_history": [],
    }
    w.setdefault("drafts", [])
    w["drafts"] = [d for d in w["drafts"] if d["key"] != draft["key"]] + [draft]
    log(f"稿子完成 [{style}/{voice}] {metrics['chars_draft']} 字 · LCS {metrics['lcs']} · "
        f"多出数字 {len(metrics['numbers_extra'])} · {'过闸' if ok else '未过闸'}")
    return w, draft


def _version_rank(v):
    """评估一个版本：先看硬伤（闸门/编造），再看裁判结论。"""
    m = v.get("metrics") or {}
    j = v.get("judge") or {}
    sc = j.get("scores") or {}
    gate_ok = 1 if (m.get("lcs", 99) <= 25 and not m.get("numbers_extra")
                    and 0.7 <= (m.get("length_ratio") or 1) <= 1.35) else 0
    fab = 1 if (j.get("evidence") or {}).get("fabrications") else 0
    verdict = {"accept": 3, "repair": 2, "reject": 1}.get(j.get("verdict"), 0)
    return (gate_ok, -fab, verdict, sc.get("faithfulness") or 0,
            sc.get("style_distance") or 0, -(m.get("lcs") or 99))


def best_version(draft):
    """回炉有可能越修越差，所以从历史里挑最好的一版留下。"""
    hist = draft.get("repair_history") or []
    cands = list(hist) + [{"text": draft["text"], "metrics": draft.get("metrics"),
                           "judge": draft.get("judge")}]
    best = max(cands, key=_version_rank)
    if best.get("text") != draft["text"]:
        if not hist or hist[-1].get("text") != draft["text"]:
            hist.append({"text": draft["text"], "metrics": draft.get("metrics"),
                         "judge": draft.get("judge"), "discarded": True})
        draft["text"] = best["text"]
        draft["metrics"] = best.get("metrics")
        draft["judge"] = best.get("judge")
        draft["reverted_to_best"] = True
    return draft


def auto_issues(metrics, brief, body):
    """脚本能确定的问题，直接写进修复指令，别指望裁判用一句话讲清楚。"""
    out = []
    if metrics.get("keep_terms_missing"):
        out.append("这些专名/术语骨架要求原样出现，稿子里没有，必须补上原文写法："
                   + "、".join(metrics["keep_terms_missing"][:10]))
    if metrics.get("numbers_extra"):
        out.append("骨架与原文里都没有这些数字，必须删掉或换成骨架里的数字："
                   + "、".join(metrics["numbers_extra"][:10]))
    if metrics.get("lcs", 0) >= 12:
        out.append(f'有一段连续文字与原文重合 {metrics["lcs"]} 字，必须整段换一种说法：'
                   f'「{metrics.get("lcs_text", "")[:70]}」')
    lr = metrics.get("length_ratio", 1)
    if lr < 0.8:
        out.append(f"篇幅偏短（{lr:.2f}×原文），把骨架里没展开的 facts 写进去，但不许编内容")
    if lr > 1.3:
        out.append(f"篇幅偏长（{lr:.2f}×原文），删掉重复论述与套话")
    return "\n".join(f"{i+1}. {x}" for i, x in enumerate(out))


def derive_verdict(scores, evidence):
    """模型偶尔漏输出 verdict，按同一套规则用分数兜底，避免导出时被当成脏数据丢掉。"""
    sc = scores or {}
    ev = evidence or {}
    if (sc.get("same_topic") or 0) < 5:
        return "reject", "same_topic<5"
    if ev.get("fabrications"):
        return "reject", "有编造事实"
    if (sc.get("leak") or 5) <= 2 and (sc.get("style_distance") or 5) <= 3:
        return "reject", "距离过近"
    if (sc.get("publishable") or 5) <= 1 or (sc.get("style_distance") or 5) <= 2:
        return "reject", "不可发/太像原文"
    if ((sc.get("faithfulness") or 0) >= 4 and (sc.get("style_distance") or 0) >= 4
            and (sc.get("leak") or 0) >= 3 and (sc.get("publishable") or 0) >= 3
            and (sc.get("length_fit") or 0) >= 3):
        return "accept", ""
    return "repair", "分数未达 accept"


def judge_provider(cfg, draft):
    """裁判尽量区别于写手。三家都走同一个中转站时，按「模型不同」也算交叉。"""
    jp, jm, jt = role_of(cfg, "judge")
    if cfg.get("judge_policy", "cross") == "fixed":
        return jp, jm, jt
    d_key = (draft.get("provider"), draft.get("model"))
    if (jp.get("id"), jm) != d_key:
        return jp, jm, jt
    for cand in cfg.get("providers", []):
        if not cand.get("api_key") or cand.get("id") == "mock":
            continue
        for m in (cand.get("models") or []):
            if m and (cand.get("id"), m) != d_key:
                return cand, m, 0.1
    return jp, jm, jt


def run_judge(cfg, w, draft, log):
    p, model, temp = judge_provider(cfg, draft)
    t = load_prompt("judge")
    body = target_body(w)
    msgs = [
        {"role": "system", "content": render(t["system"], {})},
        {"role": "user", "content": render(t["user"], {
            "brief": json.dumps(w.get("brief") or {}, ensure_ascii=False, indent=2),
            "ai_draft": draft["text"],
            "original": body,
            "structure_override": draft.get("structure_override", "none"),
            "voice_mode": draft.get("voice", "impersonal"),
            "machine_lcs": f"{draft.get('metrics', {}).get('lcs', 0)} 字"
                           f"（已排除专名/英文/引号引用；请以此为准给 leak 打分）",
        })},
    ]
    data, raw = call_json(p, model, msgs, temp, 4500, settings(cfg)["request_timeout"])
    verd = data.get("verdict")
    if verd not in ("accept", "repair", "reject"):
        verd, why = derive_verdict(data.get("scores"), data.get("evidence"))
        data["verdict"] = verd
        data.setdefault("issues", []).append(f"模型未给 verdict，按分数判定为 {verd}"
                                             + (f"（{why}）" if why else ""))
    data["_judge"] = {"provider": p["id"], "model": model,
                      "prompt_version": t["version"]}
    draft["judge"] = data
    log(f"质检完成 [{p['id']}]：{data.get('verdict')} · "
        f"保真 {data.get('scores', {}).get('faithfulness')} · 距离 {data.get('scores', {}).get('style_distance')}")
    return w, draft


def run_repair(cfg, w, draft, log):
    t = load_prompt("repair")
    p = provider_by_id(cfg, draft["provider"])
    body = target_body(w)
    brief = w.get("brief") or {}
    hard = auto_issues(draft.get("metrics") or {}, brief, body) or "（无硬性问题）"
    msgs = [
        {"role": "system", "content": render(t["system"], {
            "writer_style_label": STYLE_LABELS.get(draft["style"], draft["style"])})},
        {"role": "user", "content": render(t["user"], {
            "brief": json.dumps(brief, ensure_ascii=False, indent=2),
            "ai_draft": draft["text"],
            "judge_report": json.dumps(draft.get("judge") or {}, ensure_ascii=False, indent=2),
            "auto_issues": hard,
            "voice_mode": draft["voice"],
            "length_target": int(brief.get("length_target") or len(norm_ws(body))),
        })},
    ]
    txt = call_llm(p, draft["model"], msgs, 0.6, 12000, timeout=settings(cfg)["request_timeout"]).strip()
    draft.setdefault("repair_history", []).append({
        "text": draft["text"],
        "metrics": draft.get("metrics"),
        "judge": draft.get("judge"),
    })
    draft["text"] = txt
    draft["metrics"] = compare(body, txt, protected_terms(brief), brief.get("keep_terms"))
    draft["gate"] = {"ok": gate(draft["metrics"], settings(cfg))[0],
                     "reasons": gate(draft["metrics"], settings(cfg))[1]}
    draft["judge"] = None
    log(f"修复完成：{draft['metrics']['chars_draft']} 字 · LCS {draft['metrics']['lcs']}")
    return w, draft


def settings(cfg):
    s = dict(DEFAULT_CONFIG["settings"])
    s.update(cfg.get("settings") or {})
    return s


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------

class Job:
    def __init__(self, jid, aid_list, stages, cfg):
        self.id = jid
        self.article_ids = aid_list
        self.stages = stages
        self.cfg = cfg
        self.total = len(aid_list)
        self.done = 0
        self.failed = 0
        self.status = "running"
        self.stop_flag = False
        self.current = {}
        self.finished = set()
        self.accept_logs = True
        self.logs = []
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.ended_at = None
        self.lock = threading.Lock()

    def log(self, aid, msg, force=False):
        if not force and not self.accept_logs:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        title = (self.current.get(aid) or "")[:18]
        with self.lock:
            self.logs.append({"t": ts, "aid": aid, "title": title, "msg": msg})
            if len(self.logs) > 1200:
                self.logs = self.logs[-800:]

    def to_dict(self, log_tail=200):
        with self.lock:
            status = self.status
            if status == "running" and self.stop_flag:
                status = "stopping"
            return {
                "id": self.id, "status": status, "total": self.total,
                "done": self.done, "failed": self.failed,
                "stages": self.stages, "started_at": self.started_at,
                "ended_at": self.ended_at, "current": list(self.current.values()),
                "logs": self.logs[-log_tail:],
            }


JOBS = {}


def start_job(cfg, aid_list, stages):
    global TOKEN_CAP
    TOKEN_CAP = int(settings(cfg).get("max_tokens_cap") or 32000)
    jid = secrets.token_hex(6)
    job = Job(jid, aid_list, stages, cfg)
    JOBS[jid] = job
    t = threading.Thread(target=_run_job, args=(job,), daemon=True)
    t.start()
    return job


def _run_job(job):
    conc = max(1, int(settings(job.cfg).get("concurrency") or 3))
    cfg = job.cfg
    n_drafts = int(settings(cfg).get("drafts_per_article") or 2)
    repair_rounds = int(settings(cfg).get("auto_repair_rounds", 1) or 0)

    def one(aid):
        if job.stop_flag:
            return
        try:
            w = ensure_article(aid)
            job.current[aid] = w.get("title", "")
            if w.get("flags", {}).get("skip"):
                job.log(aid, "已标记跳过")
                return
            want = set(job.stages)
            if want & {"draft", "judge"} and not w.get("brief"):
                want |= {"classify", "brief"}
            for stage, fn in (("classify", run_classify), ("brief", run_brief), ("destyle", run_destyle)):
                if job.stop_flag:
                    return
                if stage in want and not w.get({"classify": "classify", "brief": "brief",
                                                "destyle": "brief_destyle"}[stage]):
                    fn(cfg, w, lambda m, a=aid: job.log(a, m))
                    save_work(aid, w)
            if "draft" in job.stages:
                need = n_drafts - len(w.get("drafts") or [])
                writers = pick_writers(cfg, aid, n_drafts)
                used_keys = {d["key"] for d in (w.get("drafts") or [])}
                for wr in writers:
                    if job.stop_flag or need <= 0:
                        break
                    key_prefix = f"{wr['id']}|{wr['provider']}|"
                    if any(k.startswith(key_prefix) for k in used_keys):
                        continue
                    w, draft = run_draft(cfg, w, wr, lambda m, a=aid: job.log(a, m))
                    save_work(aid, w)
                    need -= 1
                    used_keys.add(draft["key"])
                    if job.stop_flag:
                        break
                    if "judge" in job.stages:
                        w, draft = run_judge(cfg, w, draft, lambda m, a=aid: job.log(a, m))
                        save_work(aid, w)
                        for _ in range(repair_rounds):
                            verd = (draft.get("judge") or {}).get("verdict")
                            if job.stop_flag or not (verd == "repair" or not draft["gate"]["ok"]):
                                break
                            w, draft = run_repair(cfg, w, draft, lambda m, a=aid: job.log(a, m))
                            w, draft = run_judge(cfg, w, draft, lambda m, a=aid: job.log(a, m))
                            save_work(aid, w)
                        if (draft.get("repair_history") or []):
                            draft = best_version(draft)
                            save_work(aid, w)
            if "judge" in job.stages and "draft" not in job.stages:
                for d in w.get("drafts") or []:
                    if job.stop_flag:
                        break
                    if not d.get("judge"):
                        w, d = run_judge(cfg, w, d, lambda m, a=aid: job.log(a, m))
                        save_work(aid, w)
            with job.lock:
                job.done += 1
                job.finished.add(aid)
        except Exception as e:  # noqa: BLE001
            with job.lock:
                job.done += 1
                job.failed += 1
                job.finished.add(aid)
            job.log(aid, f"失败：{e}")
        finally:
            job.current.pop(aid, None)

    ex = futures.ThreadPoolExecutor(max_workers=conc)
    futs = [ex.submit(one, aid) for aid in job.article_ids]
    try:
        while True:
            if job.stop_flag:
                for f in futs:
                    f.cancel()
                break
            if all(f.done() for f in futs):
                break
            time.sleep(0.25)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        with job.lock:
            job.status = "stopped" if job.stop_flag else "done"
            job.ended_at = datetime.now().isoformat(timespec="seconds")
        if job.stop_flag:
            job.log("", f"已停止：完成 {job.done}/{job.total}，进行中的调用已丢弃", force=True)


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

def blocks_with_offsets(text):
    """按空行切块，并记录每块在原文里的字符区间。"""
    lines = (text or "").split("\n")
    out, i, off = [], 0, 0
    while i < len(lines):
        if lines[i].strip():
            start = off
            buf = []
            while i < len(lines) and lines[i].strip():
                buf.append(lines[i])
                off += len(lines[i]) + 1
                i += 1
            body = "\n".join(buf)
            out.append({"start": start, "end": start + len(body), "text": body,
                        "n": len(norm_ws(body))})
        else:
            off += len(lines[i]) + 1
            i += 1
    return out


def segment_pair(x_text, y_text, min_chars=2000, max_chars=3000):
    """把一对长文按 y 的自然段切成 2000-3000 字，x 用比例对齐到最近的段边界。"""
    yb = blocks_with_offsets(y_text)
    xb = blocks_with_offsets(x_text)
    ytot = sum(b["n"] for b in yb)
    if not yb or not xb or ytot <= max_chars:
        return [(x_text, y_text)]
    # 1) y 按段落累积切块
    groups, cur, size = [], [], 0
    for b in yb:
        cur.append(b)
        size += b["n"]
        if size >= min_chars or size >= max_chars:
            groups.append(cur)
            cur, size = [], 0
    if cur:
        groups.append(cur)
    if len(groups) > 1 and sum(b["n"] for b in groups[-1]) < min_chars * 0.5:
        groups[-2].extend(groups.pop())          # 尾巴太短就并进前一块
    # 2) 用累计比例把切点映射到 x 的段边界
    xtot = sum(b["n"] for b in xb)
    xcum, acc = [], 0
    for b in xb:
        acc += b["n"]
        xcum.append(acc)
    cuts, last = [], 0
    ycum = 0
    for g in groups[:-1]:
        ycum += sum(b["n"] for b in g)
        target = ycum / ytot * xtot
        idx = min(range(len(xcum)), key=lambda i: abs(xcum[i] - target))
        idx = max(idx, last)
        if idx >= len(xb) - 1:
            return [(x_text, y_text)]            # 切不开，退回整篇
        cuts.append(idx + 1)
        last = idx + 1
    if len(cuts) != len(groups) - 1 or any(
            (cuts[i + 1] if i + 1 < len(cuts) else len(xb)) - cuts[i] < 1
            for i in range(len(cuts))):
        return [(x_text, y_text)]
    # 3) 切成文本
    out = []
    prev = 0
    for i, c in enumerate(cuts + [len(xb)]):
        y_start = groups[i][0]["start"]
        y_end = groups[i][-1]["end"]
        x_start = 0 if prev == 0 else xb[prev]["start"]
        x_end = len(x_text) if c >= len(xb) else xb[c]["start"]
        xseg, yseg = x_text[x_start:x_end].strip(), y_text[y_start:y_end].strip()
        if len(norm_ws(xseg)) < 200 or len(norm_ws(yseg)) < 200:
            return [(x_text, y_text)]
        out.append((xseg, yseg))
        prev = c
    return out


_CTA_STRONG = re.compile(
    r"(扫码|长按|二维码|星标|在看|赞赏|打赏|领券|立减金|交流群|加群|知识星球|行动圈|"
    r"关注公众号|公众号回复|点个赞|转发给|置顶|题图：|小编)"
)
_CTA_SEP = re.compile(r"^[\s\-—*_·]{2,}$")
_SPECIAL_TOKEN = re.compile(
    r"<\|?\s*(im_start|im_end|endoftext|tool_call|tool_response|vision_start|vision_end)\s*\|?>"
    r"|<tool_call>|</tool_call>|<tool_response>|</tool_response>"
)


def strip_tail_cta(text):
    """砍掉结尾的公众号导流段（"赞赏、点赞、转发、在看" / "扫码领券加入" 那一类）。

    41% 的文章结尾都有这种尾巴，模型会把它当成"文章该有的收尾"整段复读。
    """
    parts = re.split(r"\n\s*\n", text or "")
    for i in range(len(parts) - 1, -1, -1):
        t = parts[i].strip()
        hit = bool(t and len(t) <= 260 and _CTA_STRONG.search(t)) or bool(t and _CTA_SEP.match(t))
        if hit:
            if len("\n\n".join(parts[i:])) < 420:
                parts = parts[:i]
            break
    out = [p for p in parts if p.strip() and not _CTA_SEP.match(p.strip())]
    return "\n\n".join(out).strip()


def sanitize_special(text):
    """把会被 tokenizer 当成特殊 token 的标记降级成普通文字，否则模型会学着吐它们。"""
    return _SPECIAL_TOKEN.sub(lambda m: "[" + (m.group(1) or "tag") + "]", text or "")


def clean_for_training(text):
    return sanitize_special(strip_tail_cta(text))


def build_pairs(cfg):
    exp = cfg.get("export", {})
    only_acc = exp.get("only_accepted", True)
    strip_promo = exp.get("strip_promo", True)
    seg_on = exp.get("segment", True)
    seg_min = int(exp.get("segment_min", 2000))
    seg_max = int(exp.get("segment_max", 3000))
    sys_prompt = exp.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    instrs = exp.get("instructions") or DEFAULT_INSTRUCTIONS
    pairs = []
    for fn in os.listdir(WORK_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            w = json.load(open(os.path.join(WORK_DIR, fn), encoding="utf-8"))
        except Exception:
            continue
        if w.get("flags", {}).get("skip"):
            continue
        body = target_body(w)
        if strip_promo:
            body = clean_for_training(body)
        for d in w.get("drafts") or []:
            j = d.get("judge") or {}
            if only_acc and j.get("verdict") != "accept":
                continue
            if not d.get("text"):
                continue
            draft_text = clean_for_training(d["text"]) if strip_promo else d["text"]
            i = int(hashlib.sha1((w["id"] + d["key"]).encode()).hexdigest()[:8], 16)
            segs = (segment_pair(draft_text, body, seg_min, seg_max)
                    if seg_on else [(draft_text, body)])
            multi = len(segs) > 1
            for si, (xs, ys) in enumerate(segs):
                pairs.append({
                    "id": f"{w['id']}-{d['writer_id']}" + (f"-s{si+1}" if multi else ""),
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": instrs[i % len(instrs)] + "\n\n" + xs},
                        {"role": "assistant", "content": ys},
                    ],
                    "meta": {
                        "article_id": w["id"],
                        "title": w["title"],
                        "year": w.get("year"),
                        "writer": d["writer_id"],
                        "provider": d["provider"],
                        "style": d["style"],
                        "voice": d["voice"],
                        "verdict": j.get("verdict"),
                        "scores": j.get("scores"),
                        "seg_index": si + 1,
                        "seg_total": len(segs),
                        "chars_target": len(norm_ws(ys)),
                        "length_ratio": round(len(norm_ws(xs)) / max(1, len(norm_ws(ys))), 3),
                        "lcs": d["metrics"]["lcs"],
                        "eval": bool(w.get("flags", {}).get("eval")),
                    },
                })
    return pairs


def export_pairs(cfg):
    pairs = build_pairs(cfg)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    train = [p for p in pairs if not p["meta"]["eval"]]
    evals = [p for p in pairs if p["meta"]["eval"]]
    paths = {}
    for name, arr in (("train", train), ("eval", evals)):
        p = os.path.join(EXPORT_DIR, f"{name}-{ts}.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for item in arr:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        paths[name] = {"path": p, "count": len(arr)}
    return {"paths": paths, "total": len(pairs)}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "CorpusWorkshop/1.0"

    def log_message(self, fmt, *args):  # 降噪
        if os.environ.get("CW_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _err(self, code, msg):
        self._send(code, {"ok": False, "error": str(msg)})

    # ---- routes ----
    def do_GET(self):
        try:
            self.route_get()
        except Exception as e:  # noqa: BLE001
            self._err(500, e)

    def do_POST(self):
        try:
            self.route_post()
        except Exception as e:  # noqa: BLE001
            self._err(500, e)

    def route_get(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)
        if path == "/" or path == "/index.html":
            return self.serve_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if path.startswith("/static/"):
            return self.serve_file(os.path.join(STATIC_DIR, path[len("/static/"):]))
        if studio and path.startswith("/api/studio/"):
            if studio.get(path, qs, self) is not False:
                return None
        if path == "/api/health":
            return self._send(200, {"ok": True, "time": datetime.now().isoformat()})
        if path == "/api/config":
            return self._send(200, {"ok": True, "config": mask_config(load_config()),
                                    "styles": STYLE_LABELS})
        if path == "/api/prompts":
            out = {}
            for k in PROMPT_FILES:
                try:
                    out[k] = load_prompt(k)["version"]
                except Exception:
                    out[k] = None
            return self._send(200, {"ok": True, "versions": out})
        if path == "/api/articles":
            items = scan_articles(force=bool(qs.get("force")))
            cfg = load_config()
            miny = int(settings(cfg).get("min_year") or 0)
            for it in items:
                it["in_scope"] = (it["year"] or 0) >= miny
                st = work_status(it["id"])
                it["has_work"] = st is not None
                it["status"] = st or status_of(None)
            return self._send(200, {"ok": True, "items": items, "min_year": miny})
        m = re.match(r"^/api/articles/([0-9a-f]+)$", path)
        if m:
            aid = m.group(1)
            w = load_work(aid, create=False)
            a = get_article(aid)
            if w:
                w["_target_body"] = target_body(w)
                w["_status"] = status_of(w)
            return self._send(200, {"ok": True, "article": a, "work": w or None})
        m = re.match(r"^/api/run/([0-9a-f]+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._err(404, "job not found")
            return self._send(200, {"ok": True, "job": job.to_dict()})
        if path == "/api/run":
            return self._send(200, {"ok": True, "jobs": [j.to_dict(0) for j in list(JOBS.values())[-10:]]})
        if path == "/api/export/list":
            files = []
            for f in sorted(os.listdir(EXPORT_DIR), reverse=True):
                if f.endswith(".jsonl"):
                    p = os.path.join(EXPORT_DIR, f)
                    files.append({"name": f, "size": os.path.getsize(p),
                                  "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
            return self._send(200, {"ok": True, "files": files})
        return self._err(404, "not found")

    def route_post(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if studio and path.startswith("/api/studio/"):
            body = self._json_body()
            if studio.post(path, body, self) is not False:
                return None
        if path == "/api/config":
            body = self._json_body()
            cfg = load_config()
            new = body.get("config") or {}
            # 保留未提交的 api_key
            key_map = {p["id"]: p.get("api_key", "") for p in cfg.get("providers", [])}
            for p in new.get("providers", []):
                if not p.get("api_key"):
                    p["api_key"] = key_map.get(p.get("id"), "")
            save_config(new)
            return self._send(200, {"ok": True, "config": mask_config(load_config())})
        if path == "/api/providers/test":
            global TOKEN_CAP
            body = self._json_body()
            cfg = load_config()
            TOKEN_CAP = int(settings(cfg).get("max_tokens_cap") or 32000)
            pid = body.get("provider")
            saved = dict(provider_by_id(cfg, pid))
            # 关键：以「表单当前值」为准，否则改了 Base URL 没保存就去测，打到的还是旧地址
            p = {
                "id": pid,
                "label": body.get("label") or saved.get("label"),
                "base_url": (body.get("base_url") or "").strip() or saved.get("base_url", ""),
                "api_key": (body.get("api_key") or "").strip() or saved.get("api_key", ""),
                "models": body.get("models") or saved.get("models") or [""],
                "json_mode": bool(body.get("json_mode", saved.get("json_mode"))),
            }
            model = (body.get("model") or "").strip() or (p.get("models") or [""])[0]
            url = ""
            try:
                url = chat_url(p["base_url"])
                t0 = time.time()
                out = call_llm(p, model, [
                    {"role": "system", "content": "你是连通性测试助手。"},
                    {"role": "user", "content": "只回复两个字：可用"},
                ], 0, 128, json_mode=False, timeout=60)
                reply = (out or "").strip()
                return self._send(200, {"ok": True, "ms": int((time.time() - t0) * 1000),
                                        "reply": reply[:40] or "（连接正常，正文为空——多为推理模型 token 不足）",
                                        "model": model, "url": url})
            except Exception as e:  # noqa: BLE001
                return self._send(200, {"ok": False, "error": str(e)[:600],
                                        "url": url or (p.get("base_url") or "(空)")})
        if path == "/api/run":
            body = self._json_body()
            cfg = load_config()
            ids = body.get("article_ids") or []
            stages = body.get("stages") or ["brief"]
            if not ids:
                # 自动挑选：范围内、任何一个被请求的阶段还没做完的文章
                miny = int(settings(cfg).get("min_year") or 0)
                items = [a for a in scan_articles() if (a["year"] or 0) >= miny]

                def incomplete(a):
                    st = a["status"]
                    if "classify" in stages and not st["classified"]:
                        return True
                    if "brief" in stages and not st["brief"]:
                        return True
                    if "destyle" in stages and not st["destyle"]:
                        return True
                    if "draft" in stages and st["drafts"] == 0:
                        return True
                    if "judge" in stages and st["judged"] < st["drafts"]:
                        return True
                    return False

                items = [a for a in items if incomplete(a)]
                ids = ([a["id"] for a in items] if body.get("all")
                       else [a["id"] for a in items[: int(body.get("limit") or 20)]])
            if not ids:
                return self._err(400, "没有可处理的文章")
            job = start_job(cfg, ids, stages)
            return self._send(200, {"ok": True, "job_id": job.id, "total": job.total})
        m = re.match(r"^/api/run/([0-9a-f]+)/stop$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._err(404, "job not found")
            job.stop_flag = True
            job.log("", "已停止：不再派发新任务，进行中的调用返回后丢弃", force=True)
            job.accept_logs = False             # 之后的 in-flight 日志一律丢弃
            with job.lock:
                job.status = "stopped"          # 立刻回显，不等进行中的调用
                job.ended_at = datetime.now().isoformat(timespec="seconds")
            return self._send(200, {"ok": True, "status": "stopped"})
        m = re.match(r"^/api/articles/([0-9a-f]+)/flags$", path)
        if m:
            aid = m.group(1)
            body = self._json_body()
            w = ensure_article(aid)
            w.setdefault("flags", {}).update(body.get("flags") or {})
            save_work(aid, w)
            return self._send(200, {"ok": True, "flags": w["flags"], "status": status_of(w)})
        m = re.match(r"^/api/articles/([0-9a-f]+)/judge$", path)
        if m:
            aid = m.group(1)
            body = self._json_body()
            key = body.get("key")
            cfg = load_config()
            w = ensure_article(aid)
            tgt = [d for d in w.get("drafts") or [] if d["key"] == key]
            if not tgt:
                return self._err(404, "draft not found")
            d = tgt[0]
            w, d = run_judge(cfg, w, d, lambda m: None)
            save_work(aid, w)
            return self._send(200, {"ok": True, "draft": d})
        if path == "/api/eval/sample":
            body = self._json_body()
            cfg = load_config()
            n = int(body.get("n") or 40)
            seed = int(body.get("seed") or 42)
            reset = body.get("reset", True)
            miny = int(settings(cfg).get("min_year") or 0)
            items = [a for a in scan_articles()
                     if (a["year"] or 0) >= miny and a["chars"] >= 800]
            if reset:
                # 清掉所有历史评测集标记（包括不在本次抽样池里的）
                for fn in os.listdir(WORK_DIR):
                    if not fn.endswith(".json"):
                        continue
                    try:
                        w = json.load(open(os.path.join(WORK_DIR, fn), encoding="utf-8"))
                    except Exception:
                        continue
                    if w.get("flags", {}).get("eval"):
                        w["flags"]["eval"] = False
                        save_work(w["id"], w)
            # 按字数分层，层内随机
            tiers = {"short": [], "mid": [], "long": [], "xl": []}
            for a in items:
                c = a["chars"]
                k = "short" if c < 1200 else "mid" if c < 2400 else "long" if c < 5000 else "xl"
                tiers[k].append(a)
            total = sum(len(v) for v in tiers.values()) or 1
            rnd = random.Random(seed)
            picked = []
            for k, arr in tiers.items():
                want = round(n * len(arr) / total)
                if arr and want == 0:
                    want = 1
                rnd.shuffle(arr)
                picked.extend(arr[:want])
            if len(picked) > n:
                rnd.shuffle(picked)
                picked = picked[:n]
            elif len(picked) < n:
                pool = [a for a in items if a not in picked]
                rnd.shuffle(pool)
                picked.extend(pool[:n - len(picked)])
            for a in picked:
                w = ensure_article(a["id"])
                w.setdefault("flags", {})["eval"] = True
                save_work(a["id"], w)
            return self._send(200, {"ok": True, "count": len(picked), "seed": seed,
                                    "tiers": {k: len(v) for k, v in tiers.items()},
                                    "picked": [a["title"][:40] for a in picked]})
        if path == "/api/export":
            cfg = load_config()
            body = self._json_body()
            if body.get("export"):
                cfg["export"] = body["export"]
                save_config(cfg)
            return self._send(200, {"ok": True, **export_pairs(cfg)})
        return self._err(404, "not found")

    def serve_file(self, path, ctype=None):
        path = os.path.abspath(path)
        if not path.startswith(os.path.abspath(ROOT)):
            return self._err(403, "forbidden")
        if not os.path.isfile(path):
            return self._err(404, "not found")
        if not ctype:
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
            }.get(os.path.splitext(path)[1], "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("CW_PORT", 8765)))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    cfg = load_config()
    scan_articles(force=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"语料工坊已启动 → {url}")
    print(f"文章目录：{ARTICLES_DIR}")
    print(f"数据目录：{DATA_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
