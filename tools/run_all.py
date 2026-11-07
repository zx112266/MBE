#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按顺序运行全部日常脚本，带加载等待、界面识别、失败重试与汇总报告。

用法:
    python tools/run_all.py                 # 跑全部
    python tools/run_all.py park guild      # 只跑指定脚本
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPE_DIR = ROOT / "assets" / "resource" / "pipeline"
SESSION = "work"

# (脚本文件, 入口节点, 中文名, 该脚本最长允许耗时/秒)
SCRIPTS = [
    ("start_game.json", "启动深空之眼", "启动游戏", 300),
    ("stamina.json", "体力获取", "领取体力", 180),
    ("mimir.json", "弥弥观测站", "弥弥观测站", 240),
    ("shop.json", "商店每日采购", "商店每日采购", 240),
    ("guild.json", "公会日常", "公会日常", 240),
    ("park.json", "游园街日常", "游园街日常", 300),
    ("medium.json", "介质攫取", "介质攫取", 420),
    ("supply.json", "物资挑战", "物资挑战", 300),
    ("tasks.json", "任务领取", "任务领取", 180),
    ("contract.json", "对策协议领取", "对策协议领取", 180),
]

LOBBY_KEYWORD = "前往作战"


def cli(*args: str, timeout: float = 180.0) -> dict:
    """调用 maafw-cli，返回解析后的 JSON（失败时返回带 error 的 dict）。"""
    cmd = ["uvx", "--from", "maafw-cli==0.1.6", "maafw-cli",
           "--on", SESSION, "--json", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "timeout": timeout}
    out = (p.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        return {"error": "parse", "returncode": p.returncode,
                "stdout": out[:400], "stderr": (p.stderr or "")[:400]}


def ocr_texts() -> list[tuple[str, list[int]]]:
    data = cli("ocr")
    results = data.get("results") or []
    return [(r.get("text", ""), r.get("box") or [0, 0, 0, 0]) for r in results]


def has_text(texts, needle: str) -> bool:
    return any(needle in t for t, _ in texts)


def press_back() -> None:
    cli("key", "back", timeout=30)


SPLASH_KEYS = ("健康游戏忠告", "适龄提示", "CADPA", "点击任意")


def is_splash(texts) -> bool:
    return any(has_text(texts, k) for k in SPLASH_KEYS)


def wait_for_lobby(timeout: float = 150.0, allow_back: bool = True) -> bool:
    """轮询等待回到大厅（识别「前往作战」）。

    - 启动画面（健康提示/适龄提示）只点一下推进，绝不按 BACK（BACK 会直接退游戏）
    - 其它界面才用 BACK 兜底，且间隔较宽
    """
    deadline = time.time() + timeout
    n = 0
    while time.time() < deadline:
        texts = ocr_texts()
        if has_text(texts, LOBBY_KEYWORD):
            return True
        n += 1
        if is_splash(texts):
            cli("click", "640,600", timeout=30)
            time.sleep(3.0)
            continue
        if allow_back and n % 4 == 0:
            press_back()
        time.sleep(2.0)
    return False


def run_pipeline(path: Path, entry: str, budget: float) -> dict:
    t0 = time.time()
    override = json.dumps({entry: {"timeout": 60000}}, ensure_ascii=False)
    data = cli("pipeline", "run", str(path), entry, "--override", override, timeout=budget)
    elapsed = time.time() - t0
    nodes = [n.get("name", "") for n in (data.get("nodes") or [])]
    return {
        "status": data.get("status") or data.get("error") or "unknown",
        "elapsed": elapsed,
        "nodes": nodes,
        "raw": data,
    }


def main() -> int:
    only = {a.lower() for a in sys.argv[1:]}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / ".tmp" / f"run_all_{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    log("=== 开始顺序执行日常脚本 ===")
    cli("resource", "load-image", str(ROOT / "assets" / "resource" / "image"), timeout=60)
    log("已加载模板图 assets/resource/image")

    if not wait_for_lobby(15, allow_back=False):
        log("当前不在大厅，先运行启动脚本把游戏拉起来…")
        run_pipeline(PIPE_DIR / "start_game.json", "启动深空之眼", 420)
        if not wait_for_lobby(240, allow_back=False):
            log("!! 无法进入大厅，中止")
            return 2
    log("已确认大厅界面（识别到「前往作战」）")

    rows = []
    for fname, entry, label, budget in SCRIPTS:
        if only and label.lower() not in only and fname.replace(".json", "") not in only:
            continue
        path = PIPE_DIR / fname
        if not path.exists():
            rows.append((label, "MISSING", 0.0, []))
            continue

        attempts = 0
        result = None
        while attempts < 2:
            attempts += 1
            if not wait_for_lobby(30):
                log(f"   [{label}] 前置检查：不在大厅，按 BACK 归位…")
                wait_for_lobby(60)
            log(f"   [{label}] 第 {attempts} 次执行 {fname} ({entry})")
            result = run_pipeline(path, entry, budget)
            ok = str(result["status"]).lower() == "succeeded"
            log(f"   [{label}] -> {result['status']}  {result['elapsed']:.1f}s  "
                f"节点 {len(result['nodes'])}")
            if ok:
                break
            tail = result["nodes"][-3:] if result["nodes"] else []
            log(f"   [{label}] 失败节点尾部: {tail}")
            recovery = wait_for_lobby(45)
            log(f"   [{label}] 恢复到大厅 {'OK' if recovery else 'FAILED'}，准备重试")
            if not recovery:
                break

        rows.append((label, str(result["status"]), result["elapsed"], result["nodes"]))

    log("=== 汇总 ===")
    for label, status, elapsed, nodes in rows:
        flag = "OK  " if status.lower() == "succeeded" else "FAIL"
        log(f"{flag} {label:<14} {status:<12} {elapsed:6.1f}s  节点 {len(nodes)}  "
            f"{nodes[-3:] if nodes else ''}")

    failed = [r for r in rows if r[1].lower() != "succeeded"]
    log(f"共 {len(rows)} 个脚本，成功 {len(rows) - len(failed)}，失败 {len(failed)}")
    log(f"日志: {log_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
