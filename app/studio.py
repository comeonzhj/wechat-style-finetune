#!/usr/bin/env python3
"""产品级接口层：导入 / 清洗 / 数据集 / 训练。

server.py 只负责把请求转进来（见 route_get / route_post 开头的两行），
语料流水线本身仍然复用 server.py 里原有的实现，避免二选一维护两套。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime

import cleanup
import importer
import trainer
import wxmp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_DIR = os.environ.get("CW_CORPUS_DIR") or os.path.join(ROOT, "my-articles")
BACKUP_DIR = os.path.join(ROOT, "data", "originals")
EXPORT_DIR = os.path.join(ROOT, "data", "exports")

TASKS = {}


class Task:
    """通用后台任务：跑一个函数，日志实时可读。"""

    def __init__(self, kind, title):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.title = title
        self.status = "running"
        self.logs = deque(maxlen=800)
        self.result = None
        self.error = None
        self.started = datetime.now().isoformat(timespec="seconds")
        self.ended = None
        TASKS[self.id] = self

    def log(self, msg):
        self.logs.append("%s  %s" % (datetime.now().strftime("%H:%M:%S"), msg))

    def to_dict(self, tail=200):
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "status": self.status, "logs": list(self.logs)[-tail:],
                "result": self.result, "error": self.error,
                "started": self.started, "ended": self.ended}


def start_task(kind, title, fn):
    task = Task(kind, title)

    def run():
        try:
            task.result = fn(task.log)
            task.status = "done"
        except Exception as e:  # noqa: BLE001
            task.error = str(e)[:800]
            task.status = "failed"
            task.log("[错误] %s" % task.error)
        finally:
            task.ended = datetime.now().isoformat(timespec="seconds")

    threading.Thread(target=run, daemon=True).start()
    return task


# ---------------------------------------------------------------- 工具

def corpus_count():
    if not os.path.isdir(CORPUS_DIR):
        return 0
    return sum(1 for f in os.listdir(CORPUS_DIR) if f.endswith(".md"))


def export_files(limit=20):
    if not os.path.isdir(EXPORT_DIR):
        return []
    out = []
    for name in sorted(os.listdir(EXPORT_DIR), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        p = os.path.join(EXPORT_DIR, name)
        out.append({"name": name, "mb": round(os.path.getsize(p) / 1048576, 2),
                    "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
        if len(out) >= limit:
            break
    return out


def corpus_list(q="", limit=200):
    items = []
    if not os.path.isdir(CORPUS_DIR):
        return items
    for name in sorted(os.listdir(CORPUS_DIR)):
        if not name.endswith(".md"):
            continue
        p = os.path.join(CORPUS_DIR, name)
        title = name[:-3]
        try:
            head = open(p, encoding="utf-8").read(400)
            for line in head.splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                    break
        except Exception:  # noqa: BLE001
            pass
        if q and q.lower() not in title.lower():
            continue
        items.append({"file": name, "title": title, "chars": os.path.getsize(p),
                      "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
        if len(items) >= limit:
            break
    return items


def plan():
    """首页「开始」按钮依据：现在该去哪个标签。"""
    corpus = corpus_count()
    exports = export_files(1)
    ds = trainer.dataset_status()
    adapters = trainer.list_adapters()
    steps = [
        {"key": "import", "title": "准备文章", "desc": "抓取公众号 / 提供链接 / 指定本地 md 路径",
         "done": corpus > 0},
        {"key": "clean", "title": "清洗", "desc": "删掉固定的文首文末导流语",
         "done": corpus > 0 and os.path.isdir(BACKUP_DIR)},
        {"key": "corpus", "title": "造语料", "desc": "提取事实 → 造通用稿 → 裁判 → 配对",
         "done": bool(exports)},
        {"key": "train", "title": "训练", "desc": "MLX 本地训 LoRA",
         "done": ds.get("ready", False) or bool(adapters)},
    ]
    nxt = next((s["key"] for s in steps if not s["done"]), "train")
    return {"steps": steps, "next": nxt, "corpus": corpus,
            "exports": len(exports), "dataset": ds, "adapters": len(adapters)}


def status():
    return {
        "corpus": {"count": corpus_count(), "dir": CORPUS_DIR, "backup": os.path.isdir(BACKUP_DIR)},
        "exports": export_files(5),
        "dataset": trainer.dataset_status(),
        "mlx": trainer.env_status(),
        "ram_gb": round(trainer._ram_gb(), 1),
        "models": trainer.MODELS,
        "adapters": trainer.list_adapters(),
        "hf_endpoint": os.environ.get("HF_ENDPOINT") or trainer.DEFAULT_HF_ENDPOINT,
    }


# ---------------------------------------------------------------- GET

def get(path, qs, H):
    if path == "/api/studio/status":
        return H._send(200, {"ok": True, **status()})
    if path == "/api/studio/corpus":
        q = (qs.get("q") or [""])[0]
        return H._send(200, {"ok": True, "items": corpus_list(q)})
    if path.startswith("/api/studio/task/"):
        tid = path.rsplit("/", 1)[-1]
        t = TASKS.get(tid)
        if not t:
            return H._send(404, {"ok": False, "error": "任务不存在"})
        return H._send(200, {"ok": True, "task": t.to_dict()})
    if path == "/api/studio/plan":
        return H._send(200, {"ok": True, **plan()})
    if path == "/api/studio/setup/status":
        return H._send(200, {"ok": True, "mlx": trainer.env_status(), "ram_gb": round(trainer._ram_gb(), 1)})
    return False


# ---------------------------------------------------------------- POST

def post(path, body, H):
    if path == "/api/studio/import":
        urls = [u.strip() for u in (body.get("urls") or "").splitlines() if u.strip().startswith("http")]
        paths = body.get("paths") or []
        uploads = body.get("files") or []
        if uploads:
            inbox = os.path.join(ROOT, "data", "inbox")
            os.makedirs(inbox, exist_ok=True)
            for f in uploads:
                name = os.path.basename(str(f.get("name") or "upload.html"))
                dst = os.path.join(inbox, name)
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(str(f.get("content") or ""))
                paths.append(dst)
        if not urls and not paths:
            return H._send(400, {"ok": False, "error": "请粘贴文章链接，或选择本地文件"})
        placeholder = body.get("placeholder") or "<!-- 配图 -->"

        def run(log):
            if urls:
                log("开始抓取 %d 个链接…" % len(urls))
                res = importer.import_urls(urls, CORPUS_DIR, placeholder,
                                           float(body.get("delay") or 0.8), log=log)
            else:
                log("开始导入 %d 个本地文件…" % len(paths))
                res = importer.import_local(paths, CORPUS_DIR, placeholder, log=log)
            log("完成：成功 %d，失败 %d" % (len(res["ok"]), len(res["fail"])))
            return res

        task = start_task("import", "导入文章", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/wxmp/search":
        try:
            accounts = wxmp.search_account(body.get("name") or "", body.get("token") or "",
                                           body.get("cookie") or "")
            return H._send(200, {"ok": True, "accounts": accounts})
        except Exception as e:  # noqa: BLE001
            return H._send(200, {"ok": False, "error": str(e)[:400]})

    if path == "/api/studio/wxmp/export":
        cookie, token = body.get("cookie") or "", body.get("token") or ""
        fakeid = body.get("fakeid") or ""
        if not (cookie and token and fakeid):
            return H._send(400, {"ok": False, "error": "需要 Cookie、Token 和账号 ID"})
        limit = int(body.get("limit") or 0) or None
        placeholder = body.get("placeholder") or "<!-- 配图 -->"

        def run(log):
            log("开始拉取文章列表…")
            res = wxmp.export_links(fakeid, token, cookie, limit=limit, log=log)
            links = [l["link"] for l in res["links"]]
            log("共 %d 篇，开始逐篇抓取正文（约 %d 分钟）" % (len(links), max(1, len(links) // 12)))
            got = importer.import_urls(links, CORPUS_DIR, placeholder, 0.8, log=log)
            log("完成：成功 %d，失败 %d" % (len(got["ok"]), len(got["fail"])))
            return {"total": res["total"], "links": len(links), **got}

        task = start_task("wxmp", "一键导出公众号", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/import/path":
        paths = body.get("paths") or []
        if isinstance(paths, str):
            paths = [p.strip() for p in paths.splitlines() if p.strip()]
        if not paths:
            return H._send(400, {"ok": False, "error": "请填写路径"})
        placeholder = body.get("placeholder") or "<!-- 配图 -->"

        def run(log):
            log("扫描 %d 个路径…" % len(paths))
            return importer.import_paths(paths, CORPUS_DIR, placeholder, log=log)

        task = start_task("path", "导入本地文章", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/cleanup/preview":
        rules = body.get("rules") or cleanup.DEFAULT_RULES
        return H._send(200, {"ok": True, "preview": cleanup.preview(
            CORPUS_DIR, rules, body.get("placeholder") or "<!-- 配图 -->",
            int(body.get("limit") or 3))})

    if path == "/api/studio/cleanup/apply":
        rules = body.get("rules") or cleanup.DEFAULT_RULES
        dry = bool(body.get("dry_run"))

        def run(log):
            return cleanup.run(CORPUS_DIR, rules, body.get("placeholder") or "<!-- 配图 -->",
                               backup_dir=BACKUP_DIR, dry_run=dry, log=log)

        task = start_task("cleanup", "清洗文章", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/cleanup/restore":
        def run(log):
            return cleanup.restore(CORPUS_DIR, BACKUP_DIR, log=log)

        task = start_task("restore", "还原文章", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/dataset":
        name = body.get("export")
        if not name:
            files = export_files(1)
            if not files:
                return H._send(400, {"ok": False, "error": "还没有导出文件，先在「语料」页生成"})
            name = files[0]["name"]
        path_ = os.path.join(EXPORT_DIR, name)
        if not os.path.exists(path_):
            return H._send(404, {"ok": False, "error": "找不到导出文件"})
        stats = trainer.build_dataset(path_, float(body.get("valid_ratio") or 0.05),
                                      int(body.get("seed") or 0), log=lambda m: None)
        return H._send(200, {"ok": True, "dataset": stats})

    if path == "/api/studio/train/start":
        params = body.get("params") or {}
        problems = trainer.check_memory(params.get("model"))
        if problems and not body.get("force"):
            return H._send(400, {"ok": False, "error": "；".join(problems), "need_confirm": True})
        if not trainer.dataset_status().get("ready"):
            return H._send(400, {"ok": False, "error": "还没有训练数据，先生成数据集"})
        env = trainer.env_status()
        if not env.get("ok"):
            return H._send(400, {"ok": False, "error": "MLX 还没装好，先点「一键安装」"})
        job = trainer.start(params, hf_endpoint=body.get("hf_endpoint"))
        return H._send(200, {"ok": True, "job": job})

    if path == "/api/studio/train/stop":
        return H._send(200, {"ok": True, **trainer.stop(body.get("id"))})

    if path == "/api/studio/adapters/delete":
        return H._send(200, {"ok": True, **trainer.delete_adapter(body.get("name") or "")})

    if path == "/api/studio/setup/install":
        py = trainer.project_python()
        venv = trainer.VENV_DIR
        mirror = body.get("mirror") or "https://pypi.tuna.tsinghua.edu.cn/simple"

        def run(log):
            if not os.path.isdir(venv):
                log("创建虚拟环境 %s" % venv)
                subprocess.run(["python3", "-m", "venv", venv], check=True)
            pip = os.path.join(venv, "bin", "pip")
            log("用镜像安装 mlx-lm：%s" % mirror)
            proc = subprocess.run([pip, "install", "-U", "pip", "-i", mirror],
                                  capture_output=True, text=True)
            log(proc.stdout.strip()[-300:] or "pip 已更新")
            proc = subprocess.run([pip, "install", "-i", mirror, "mlx-lm"],
                                  capture_output=True, text=True)
            log(proc.stdout.strip()[-1200:])
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or "pip 安装失败").strip()[-600:])
            env = trainer.env_status()
            log("安装完成，MLX 版本：%s" % env.get("mlx"))
            return env

        task = start_task("setup", "安装 MLX", run)
        return H._send(200, {"ok": True, "task_id": task.id})

    if path == "/api/studio/train/job":
        jid = body.get("id")
        job = trainer.JOBS.get(jid)
        if not job:
            return H._send(404, {"ok": False, "error": "任务不存在"})
        return H._send(200, {"ok": True, "job": job.to_dict()})

    if path == "/api/studio/jobs":
        tasks = [t.to_dict(3) for t in list(TASKS.values())[-10:]]
        train = [j.to_dict(3) for j in list(trainer.JOBS.values())[-10:]]
        return H._send(200, {"ok": True, "tasks": tasks, "train": train})

    if path == "/api/studio/train/list":
        return H._send(200, {"ok": True, "jobs": [j.to_dict(20) for j in list(trainer.JOBS.values())[-10:]]})

    return False
