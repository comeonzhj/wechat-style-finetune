#!/usr/bin/env python3
"""Mac 本地微调：用 MLX 把「AI 稿 → 我的稿」语料训成 LoRA。

为什么只做 Mac
--------------
Apple Silicon 上 MLX 是苹果自己维护的框架，装起来只要一条 pip，
跑 4B 模型占 4GB 内存，普通 MacBook 都能扛。Windows 上要 CUDA + bitsandbytes，
版本组合极不稳定，先不碰。

数据格式
--------
直接吃本工具导出的 jsonl（`{"messages":[system,user,assistant]}`），
不用中间再转一道。MLX 要求目录里是 train.jsonl / valid.jsonl / test.jsonl。
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(ROOT, ".venv-mlx")
DATA_DIR = os.path.join(ROOT, "mlx-data")
ADAPTER_DIR = os.path.join(ROOT, "adapters")
LOG_DIR = os.path.join(ROOT, "logs")

# 国内网络默认走 HF 镜像，用户可在界面里关掉
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"

MODELS = [
    {
        "id": "mlx-community/Qwen3-0.6B-4bit",
        "label": "Qwen3-0.6B（4bit）",
        "size": "0.4GB",
        "note": "只用来验证流程通不通，效果很差",
        "ram": 2,
    },
    {
        "id": "mlx-community/Qwen3-1.7B-4bit",
        "label": "Qwen3-1.7B（4bit）",
        "size": "1GB",
        "note": "能跑，文风会偏平",
        "ram": 4,
    },
    {
        "id": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
        "label": "Qwen3-4B-Instruct-2507（4bit）★",
        "size": "2.3GB",
        "note": "推荐：24GB 内存的 Mac 稳定跑，效果够用",
        "ram": 8,
    },
    {
        "id": "mlx-community/Qwen3-8B-4bit",
        "label": "Qwen3-8B（4bit）",
        "size": "4.5GB",
        "note": "24GB 内存贴着上限，要开梯度检查点",
        "ram": 14,
    },
]


def project_python():
    """优先用项目自带的 venv，退回归宿到当前解释器。"""
    for p in (os.path.join(VENV_DIR, "bin", "python"), os.path.join(VENV_DIR, "bin", "python3")):
        if os.path.exists(p):
            return p
    return sys.executable


def env_status():
    """检查 MLX 是否就绪。"""
    py = project_python()
    try:
        out = subprocess.run(
            [py, "-c", "import mlx.core, mlx_lm; print(mlx.core.__version__); print(mlx_lm.__version__ if hasattr(mlx_lm,'__version__') else 'ok')"],
            capture_output=True, text=True, timeout=90,
        )
        if out.returncode == 0:
            lines = out.stdout.strip().splitlines()
            return {"ok": True, "python": py, "mlx": lines[0] if lines else "?", "venv": os.path.isdir(VENV_DIR)}
        return {"ok": False, "python": py, "error": (out.stderr or out.stdout).strip()[-400:], "venv": os.path.isdir(VENV_DIR)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "python": py, "error": str(e)[:400], "venv": os.path.isdir(VENV_DIR)}


def hf_cache_has(repo: str) -> bool:
    """粗略判断模型是否已下载（HF cache 目录名把 / 换成 --）。"""
    name = "models--" + repo.replace("/", "--")
    for base in (
        os.path.expanduser("~/.cache/huggingface/hub"),
        os.path.expanduser("~/.cache/modelscope/hub/models"),
    ):
        p = os.path.join(base, name)
        if os.path.isdir(p):
            return True
    return False


# ---------------------------------------------------------------- 数据集

def _is_messages(rec: dict) -> bool:
    msgs = rec.get("messages")
    return isinstance(msgs, list) and len(msgs) >= 2 and all("role" in m for m in msgs)


def load_pairs(path: str) -> list:
    """读导出的 jsonl；兼容 sharegpt（system/conversations）和 messages 两种。"""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if _is_messages(rec):
                out.append({"messages": rec["messages"]})
            elif rec.get("conversations"):
                msgs = [{"role": "system", "content": rec.get("system", "")}]
                for turn in rec["conversations"]:
                    role = "user" if turn.get("from") in ("human", "user") else "assistant"
                    msgs.append({"role": role, "content": turn.get("value", "")})
                out.append({"messages": msgs})
    return out


def build_dataset(export_path: str, valid_ratio=0.05, seed=0, max_chars=12000, log=print):
    """把导出文件切成 MLX 需要的 train/valid/test 三个 jsonl。"""
    pairs = load_pairs(export_path)
    if not pairs:
        raise ValueError("这个导出文件里没有可用的样本")

    # 太长/太短的都扔掉：超过 12k 字的基本是切段失败，短于 200 字学不到东西
    kept, dropped = [], {"short": 0, "long": 0}
    for p in pairs:
        total = sum(len(m["content"]) for m in p["messages"])
        if total < 200:
            dropped["short"] += 1
            continue
        if total > max_chars:
            dropped["long"] += 1
            continue
        kept.append(p)

    rnd = random.Random(seed)
    rnd.shuffle(kept)
    n_valid = max(1, int(len(kept) * valid_ratio)) if len(kept) > 20 else 0
    valid = kept[:n_valid]
    train = kept[n_valid:]

    os.makedirs(DATA_DIR, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid), ("test", valid)):
        path = os.path.join(DATA_DIR, name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stats = {
        "total": len(pairs), "kept": len(kept), "train": len(train),
        "valid": len(valid), "dropped": dropped,
        "dir": DATA_DIR, "source": os.path.basename(export_path),
    }
    log("数据集已生成：train %d / valid %d（原 %d 条，丢弃过短 %d / 过长 %d）"
        % (len(train), len(valid), len(pairs), dropped["short"], dropped["long"]))
    return stats


def dataset_status():
    out = {"ready": False, "dir": DATA_DIR}
    for name in ("train", "valid"):
        p = os.path.join(DATA_DIR, name + ".jsonl")
        if os.path.exists(p):
            out[name] = sum(1 for _ in open(p, encoding="utf-8"))
    out["ready"] = bool(out.get("train"))
    return out


# ---------------------------------------------------------------- 训练任务

class TrainJob:
    def __init__(self, job_id, params):
        self.id = job_id
        self.params = params
        self.status = "running"
        self.logs = deque(maxlen=1500)
        self.started = datetime.now().isoformat(timespec="seconds")
        self.ended = None
        self.iters_done = 0
        self.iters_total = int(params.get("iters") or 0)
        self.loss = None
        self.val_loss = None
        self.nan_hint = False
        self.proc = None
        self._lock = threading.Lock()

    def add(self, line):
        clean = re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip("\n")
        with self._lock:
            self.logs.append(clean)
            # mlx-lm 的输出形如： "     5    2.867 ▼      415      3.4k"
            m = re.match(r"^\s*(\d+)\s+(\d+\.\d+|nan|inf)\s+\S+\s+", clean)
            if m:
                self.iters_done = int(m.group(1))
                v = m.group(2)
                self.loss = None if v in ("nan", "inf") else float(v)
                if v in ("nan", "inf") and not self.nan_hint:
                    self.nan_hint = True
                    self.logs.append(
                        "[提示] loss 变成 nan：多半是上下文长度太小，assistant 部分被截没了。"
                        "把「上下文长度」调到 2048 以上再试。")
                return
            # "     1    val 2.008    2.41s"
            m = re.match(r"^\s*(\d+)\s+val\s+(\d+\.\d+|nan)", clean)
            if m:
                self.val_loss = None if m.group(2) in ("nan",) else float(m.group(2))

    def to_dict(self, tail=120):
        return {
            "id": self.id, "status": self.status, "params": self.params,
            "started": self.started, "ended": self.ended,
            "iters_done": self.iters_done, "iters_total": self.iters_total,
            "loss": self.loss, "val_loss": self.val_loss,
            "logs": list(self.logs)[-tail:],
        }


JOBS = {}


def _ram_gb():
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip()) / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return 0


def check_memory(model_id):
    """跑之前先挡一下：内存不够别硬上，Mac 会卡死。"""
    info = next((m for m in MODELS if m["id"] == model_id), None)
    need = (info or {}).get("ram", 8)
    total = _ram_gb()
    problems = []
    if total and need > total * 0.75:
        problems.append("这台机器 %.0fGB 内存，跑这个模型大概要 %dGB，容易卡死" % (total, need))
    return problems


def start(params, hf_endpoint=None, log=print):
    py = project_python()
    model = params.get("model") or MODELS[2]["id"]
    iters = int(params.get("iters") or 300)
    adapter = params.get("adapter") or ("run-" + datetime.now().strftime("%m%d-%H%M%S"))
    adapter_path = os.path.join(ADAPTER_DIR, adapter)
    os.makedirs(ADAPTER_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    cmd = [
        py, "-m", "mlx_lm", "lora",
        "--model", model,
        "--train",
        "--data", DATA_DIR,
        "--batch-size", str(int(params.get("batch_size") or 1)),
        "--iters", str(iters),
        "--max-seq-length", str(int(params.get("max_seq_length") or 2048)),
        "--learning-rate", str(params.get("learning_rate") or 1e-5),
        "--adapter-path", adapter_path,
        "--mask-prompt",
        "--steps-per-report", "5",
        "--steps-per-eval", str(max(20, iters // 2)),
        "--val-batches", "2",
    ]
    if int(params.get("max_seq_length") or 0) < 1024:
        log("提示：上下文长度小于 1024 时，长稿子的 assistant 部分容易被截掉，loss 会变 nan")
    layers = int(params.get("num_layers") or 0)
    if layers:
        cmd += ["--num-layers", str(layers)]
    if params.get("grad_checkpoint"):
        cmd += ["--grad-checkpoint"]
    if params.get("resume_adapter_file"):
        cmd += ["--resume-adapter-file", params["resume_adapter_file"]]

    env = dict(os.environ)
    if hf_endpoint:
        env["HF_ENDPOINT"] = hf_endpoint
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    log_file = os.path.join(LOG_DIR, "train-%s.log" % adapter)
    job = TrainJob(adapter, {**params, "model": model, "adapter": adapter,
                             "adapter_path": adapter_path, "cmd": " ".join(cmd)})
    JOBS[adapter] = job

    def run():
        try:
            with open(log_file, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1, env=env, cwd=ROOT)
                job.proc = proc
                for line in proc.stdout:
                    job.add(line)
                    lf.write(line)
                code = proc.wait()
            job.status = "done" if code == 0 else "failed"
        except Exception as e:  # noqa: BLE001
            job.add("[错误] %s" % e)
            job.status = "failed"
        finally:
            job.ended = datetime.now().isoformat(timespec="seconds")
            log("训练结束：%s（%s）" % (job.id, job.status))

    threading.Thread(target=run, daemon=True).start()
    return job.to_dict()


def stop(job_id):
    job = JOBS.get(job_id)
    if not job or not job.proc:
        return {"ok": False, "error": "任务不存在或还没启动"}
    try:
        job.proc.terminate()
        job.status = "stopped"
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def list_adapters():
    if not os.path.isdir(ADAPTER_DIR):
        return []
    out = []
    for name in sorted(os.listdir(ADAPTER_DIR), reverse=True):
        p = os.path.join(ADAPTER_DIR, name)
        if not os.path.isdir(p):
            continue
        files = os.listdir(p)
        sizes = sum(os.path.getsize(os.path.join(p, f)) for f in files if f.endswith(".safetensors"))
        out.append({
            "name": name, "path": p, "files": len(files),
            "mb": round(sizes / 1024 / 1024, 1),
            "mtime": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds"),
            "has_weights": any(f.endswith(".safetensors") for f in files),
        })
    return out


def delete_adapter(name):
    p = os.path.join(ADAPTER_DIR, name)
    if os.path.isdir(p) and os.path.commonpath([p, ADAPTER_DIR]) == ADAPTER_DIR:
        shutil.rmtree(p)
        return {"ok": True}
    return {"ok": False, "error": "找不到这个 adapter"}


if __name__ == "__main__":
    print(json.dumps({"env": env_status(), "data": dataset_status(),
                      "models": MODELS, "ram": round(_ram_gb(), 1)},
                     ensure_ascii=False, indent=1))
