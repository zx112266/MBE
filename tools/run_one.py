# -*- coding: utf-8 -*-
"""单个脚本的受控执行器。

规则（按用户要求）：
1. 先判断场景：大厅 / 战斗 / 载入 / 其它；
2. 场景不对就回大厅再开跑；
3. 出现战斗只等待、不按 BACK、不判失败（避免误判）；
4. 失败重试，最多 2 次；仍失败就停下，等人工指示。

用法:
    python tools/run_one.py <pipeline.json> <入口节点> [--session fix] [--max-try 2]
"""
import argparse
import json
import subprocess
import sys
import time

CLI = ["uvx", "--from", "maafw-cli==0.1.6", "maafw-cli"]

# 场景判据（全部取自实测，见 .tmp/约定.md）
# 「前往作战」是橙底白字，OCR 经常读不到，所以大厅用多个稳定锚点任一命中
LOBBY = ("前往作战", "对策协议", "游园街", "修正者", "仓库")
BATTLE = ("任务完成", "挑战成绩", "作战中", "战斗时长", "剩余时间", "挑战失败")
LOADING = ("正在加载", "加载中", "初始化")
SETTLE = ("任务完成", "挑战成绩", "挑战成功", "挑战失败")
STAMINA = ("补充吨吨值", "冷却剂", "移转之辉")
MAX_BACK = 3



class Dev:
    def __init__(self, session):
        self.session = session

    def cli(self, *args, timeout=300):
        cmd = CLI + ["--on", self.session, "--json", *args]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        out = (p.stdout or "").strip()
        try:
            return json.loads(out[out.index("{"):])
        except Exception:
            return {"error": "parse", "raw": out[:300]}

    def texts(self):
        return [r.get("text", "") for r in (self.cli("ocr").get("results") or [])]

    def scene(self, texts=None):
        t = texts if texts is not None else self.texts()
        joined = " ".join(t)
        if any(k in joined for k in SETTLE):
            return "settle"
        if any(k in joined for k in BATTLE):
            return "battle"
        if any(k in joined for k in LOBBY):
            return "lobby"
        if any(k in joined for k in LOADING):
            return "loading"
        return "other"

    def click(self, x, y, wait=2.0):
        self.cli("click", f"{x},{y}")
        time.sleep(wait)

    def back(self, wait=2.5):
        self.cli("key", "back")
        time.sleep(wait)


def drain_settle(dev, limit=300):
    """战斗/结算：只推进“任务完成/挑战成绩”这类确认，不做失败判定。"""
    t0 = time.time()
    while time.time() - t0 < limit:
        t = dev.texts()
        s = dev.scene(t)
        if s == "settle":
            print("  [结算] 点确认 (1078,667)")
            dev.click(1078, 667, wait=3.0)
            continue
        if s == "battle":
            print("  [战斗] 等待中，不操作 …")
            time.sleep(5)
            continue
        return s
    return "timeout"


def to_lobby(dev, limit=150):
    """回到大厅。战斗中不按 BACK，先等战斗结束。BACK 最多 3 次，防止在大厅里越按越乱。"""
    t0 = time.time()
    backs = 0
    while time.time() - t0 < limit:
        t = dev.texts()
        s = dev.scene(t)
        if s == "lobby":
            return True
        if s in ("battle", "settle"):
            drain_settle(dev, limit=180)
            continue
        if s == "loading":
            time.sleep(4)
            continue
        joined = " ".join(t)
        if any(k in joined for k in STAMINA):
            print("  [体力面板] 点空白关闭")
            dev.click(640, 700, wait=2.0)
            continue
        if backs >= MAX_BACK:
            print(f"  !! 已按 BACK {backs} 次仍非大厅，停止强退")
            break
        print(f"  [返回] BACK {backs + 1}/{MAX_BACK}")
        dev.back()
        backs += 1
    return dev.scene() == "lobby"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pipeline")
    ap.add_argument("entry")
    ap.add_argument("--session", default="fix")
    ap.add_argument("--max-try", type=int, default=2)
    a = ap.parse_args()

    dev = Dev(a.session)
    print(f"设备会话: {a.session}  目标: {a.pipeline} / {a.entry}", flush=True)

    print("① 判断场景 …", flush=True)
    s = dev.scene()
    print(f"   当前场景: {s}", flush=True)
    if s != "lobby":
        if s in ("battle", "settle"):
            print("   战斗中/结算中 → 先等结束", flush=True)
            drain_settle(dev)
        print("   回大厅 …", flush=True)
        if not to_lobby(dev):
            print("!! 无法回到大厅，停止（等指示）", flush=True)
            return 3

    for attempt in range(1, a.max_try + 1):
        print(f"\n② 第 {attempt}/{a.max_try} 次执行", flush=True)
        t0 = time.time()
        d = dev.cli("pipeline", "run", a.pipeline, a.entry, timeout=420)
        nodes = [n.get("name") for n in (d.get("nodes") or [])]
        print(f"   status={d.get('status')}  {time.time() - t0:.1f}s  节点={len(nodes)}", flush=True)
        for i, n in enumerate(nodes, 1):
            print(f"     {i:2d}. {n}", flush=True)
        if str(d.get("status")).lower() == "succeeded":
            print("\n=== 结果: succeeded ===", flush=True)
            return 0

        print("   失败 → 判断当前场景", flush=True)
        s = dev.scene()
        print(f"   当前场景: {s}", flush=True)
        if s in ("battle", "settle"):
            print("   处于战斗/结算 → 等它结束（不算失败）", flush=True)
            drain_settle(dev)
        if not to_lobby(dev):
            print("!! 无法回到大厅，停止（等指示）", flush=True)
            return 3

    print("\n=== 结果: 连续失败，已停止，等待指示 ===", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
