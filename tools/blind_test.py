#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""blind_test.py —— 按策略自动"盲玩"本作，输出覆盖度/用时/卡点。

"盲"的含义：策略**只看文档化接口**（`ui.agent_api` 的 report / suggest / cover），
不读源码、不碰内部字段，就像一个新会话的 AI 只会照 AGENTS.md 玩。

用法:
    python -X utf8 tools/blind_test.py --runs 3 --seed-base 100
    python -X utf8 tools/blind_test.py --policy none --max-minutes 30   # 只推进时间做对照
    python -X utf8 tools/blind_test.py --out docs/blindtest_report.md

策略:
    suggest  跟着 `suggest` 的第一条可行建议走（默认，等价于"照向导玩的 AI"）
    random   在可行建议里随机选（对照组：验证"不是随便点也能过"）
    none     不行动，只推进时间（对照组：验证压力确实会杀死你）
"""
import argparse
import io
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                       # noqa: E402
from ui.agent_api import (CoverageTracker, build_report,   # noqa: E402
                          suggest_actions)
from ui.commands import CommandRouter             # noqa: E402
from ui.commands import fmt_time                  # noqa: E402

STEP = 0.5


def load_subs():
    with io.open(os.path.join(ROOT, "content", "substances.json"),
                 encoding="utf-8") as f:
        return {s["id"]: s for s in json.load(f)["substances"]}


class BlindRun:
    def __init__(self, seed, policy="suggest", max_minutes=600,
                 stuck_seconds=900, verbose=False, rng=None) -> None:
        self.seed = seed
        self.policy = policy
        self.max_seconds = max_minutes * 60.0
        self.stuck_seconds = stuck_seconds
        self.verbose = verbose
        self.rng = rng or random.Random(seed)
        self.eng = main.build_engine(seed=seed)
        self.router = CommandRouter(self.eng, load_subs())
        self.tracker = CoverageTracker()
        self.history = []
        self._skip = {}

    # ---- 基础操作 --------------------------------------------------
    def advance(self, seconds: float) -> None:
        t = 0.0
        was_paused = self.eng.clock.paused
        self.eng.clock.resume()
        while t < seconds:
            self.eng.tick(min(STEP, seconds - t))
            t += STEP
        if was_paused:
            self.eng.clock.pause()

    def exe(self, cmd: str) -> bool:
        before = len(self.eng.log_lines)
        self.router.execute(cmd)
        new = self.eng.log_lines[before:]
        bad = any(("参数错误" in l) or ("未知指令" in l) for l in new)
        if self.verbose:
            tag = "✗" if bad else "✓"
            print(f"   {tag} {cmd}"
                  + (f"   [{new[-1]}]" if new and bad else ""))
        return not bad

    def signature(self, rep: dict):
        """进展签名：只取"结构性进展"，避免资源波动与派员/调离被当成进展。"""
        return (
            len(rep["facilities"]),
            sum(1 for f in rep["facilities"] if f["state"] == "running"),
            rep["units"]["total"],
            rep["units"]["efficiency"],
            len(rep["entries"]["permanent"]),
            (rep["database"] or {}).get("built", 0),
            sum(1 for p in rep["plots"] if p["state"] in ("claimed",
                                                          "developed")),
        )

    # ---- 主循环 ----------------------------------------------------
    def run(self) -> dict:
        started = time.time()
        outcome = "timeout"
        last_sig = None
        last_change = 0.0
        decisions = 0
        while True:
            rep = build_report(self.eng, self.router)
            self.tracker.update(rep)
            db = rep["database"] or {}
            mem = rep["memory"] or {}
            if db.get("built", 0) >= db.get("projects", 5) and mem.get("disabled"):
                outcome = "victory"
                break
            if self.eng.clock.time > self.max_seconds:
                outcome = "timeout"
                break
            if db.get("built", 0) >= db.get("projects", 5) and not mem.get("disabled"):
                self.exe("migrate")
                self.advance(60)
                continue
            if self.policy == "none":
                self.advance(300)
                if self.eng.clock.time - last_change > self.stuck_seconds \
                        and last_sig is not None:
                    outcome = "idle"
                    break
                last_sig = self.signature(rep)
                continue
            sugs = [s for s in suggest_actions(self.eng, self.router, limit=12)
                    if s["cmd"] and not s["blocked"]]
            recent = [c for _t, c, _w in self.history[-3:]]
            sugs = [s for s in sugs
                    if self._skip.get(s["cmd"], 0) < 2
                    and s["cmd"] not in recent]
            if not sugs:
                self.advance(300)          # 真的没有可执行动作 → 空转
                continue
            pick = (sugs[0] if self.policy == "suggest"
                    else self.rng.choice(sugs))
            cmd = pick["cmd"]
            decisions += 1
            if cmd.startswith("@tick"):
                secs = float(cmd.split()[1])
                self.advance(secs)
            else:
                ok = self.exe(cmd)
                self.advance(10)
                if not ok:
                    # 命令被拒（材料/条件不足）→ 降低它的优先级
                    self._skip[cmd] = self._skip.get(cmd, 0) + 1
            self.history.append((round(self.eng.clock.time, 1), cmd,
                                 pick["why"]))
            new_sig = self.signature(build_report(self.eng, self.router))
            if new_sig != last_sig:
                last_sig = new_sig
                last_change = self.eng.clock.time
                self._skip.clear()
            else:
                # 这条建议没带来结构性进展 → 下次少给它机会，避免死循环。
                # 注意：仅"攒资源/等作业"不算卡死，所以这里不判 stuck，
                # 而是把它记成 plateau（无结构性进展的最长时长）供报告用。
                self._skip[cmd] = self._skip.get(cmd, 0) + 1
                self.plateau = max(getattr(self, "plateau", 0.0),
                                   self.eng.clock.time - last_change)
        rep = build_report(self.eng, self.router)
        self.tracker.update(rep)
        prog = self.tracker.progress()
        return {
            "seed": self.seed, "policy": self.policy, "outcome": outcome,
            "t": rep["t"], "minutes": round(rep["t"] / 60.0, 1),
            "coverage": f"{prog['done']}/{prog['total']}",
            "coverage_missing": [m["id"] for m in prog["missing"]],
            "facilities": len(rep["facilities"]),
            "units": rep["units"]["total"],
            "efficiency": rep["units"]["efficiency"],
            "permanent": len(rep["entries"]["permanent"]),
            "db": ((rep["database"] or {}).get("built", 0),
                   (rep["database"] or {}).get("projects", 5)),
            "memory": (rep["memory"] or {}).get("integrity"),
            "stall_seconds": rep["stats"]["stall_seconds"],
            "breakdowns": rep["stats"]["breakdowns"],
            "decisions": decisions,
            "plateau_minutes": round(getattr(self, "plateau", 0.0) / 60.0, 1),
            "wall": round(time.time() - started, 1),
            "history_tail": self.history[-12:],
        }


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="盲玩自动化：覆盖度/用时/卡点")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--seed-base", type=int, default=100)
    ap.add_argument("--policy", default="suggest",
                    choices=("suggest", "random", "none"))
    ap.add_argument("--max-minutes", type=float, default=600)
    ap.add_argument("--stuck-seconds", type=float, default=900)
    ap.add_argument("--out", default="", help="汇总写入 Markdown 文件")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    results = []
    print(f"[说明] 本工具用于测「覆盖度/用时/停摆」等可量化指标；"
          f"内置 policy={args.policy} 是固定阶梯式策略，"
          f"**不代表 AI/人的水平上限**（真人或 LLM 可按 AGENTS.md 规划得更好）。")
    for i in range(args.runs):
        seed = args.seed_base + i
        print(f"\n=== 第 {i + 1}/{args.runs} 局（seed={seed}, "
              f"policy={args.policy}）===")
        r = BlindRun(seed, args.policy, args.max_minutes,
                     args.stuck_seconds, verbose=args.verbose).run()
        results.append(r)
        print(f"   结果 {r['outcome']}｜游戏 {fmt_time(r['t'])}"
              f"（{r['minutes']} 分钟）｜覆盖 {r['coverage']}"
              f"｜设施 {r['facilities']}｜单元 {r['units']}"
              f"（效能 ×{r['efficiency']}）｜固化 {r['permanent']}"
              f"｜DB {r['db'][0]}/{r['db'][1]}｜记忆 {r['memory']}"
              f"｜墙钟 {r['wall']}s")

    wins = [r for r in results if r["outcome"] == "victory"]
    print("\n=== 汇总 ===")
    print(f"  局数 {len(results)}｜通关 {len(wins)}"
          f"｜卡死/超时 {sum(1 for r in results if r['outcome'] in ('stuck', 'timeout', 'idle'))}")
    if results:
        cove = [int(r["coverage"].split("/")[0]) for r in results]
        tot = results[0]["coverage"].split("/")[1]
        print(f"  覆盖度 平均 {sum(cove) / len(cove):.1f}/{tot}"
              f"（最好 {max(cove)}，最差 {min(cove)}）")
        mins = sorted(r["minutes"] for r in results)
        print(f"  用时中位 {mins[len(mins) // 2]:.1f} 分钟"
              f"（最短 {mins[0]:.1f}，最长 {mins[-1]:.1f}）")
        miss = {}
        for r in results:
            for m in r["coverage_missing"]:
                miss[m] = miss.get(m, 0) + 1
        if miss:
            top = sorted(miss.items(), key=lambda kv: -kv[1])[:5]
            print("  最常见未覆盖：" + "、".join(f"{k}×{v}" for k, v in top))
        stuck = [r for r in results if r["outcome"] != "victory"]
        if stuck:
            print(f"  未通关示例（seed={stuck[0]['seed']}）最后几步：")
            for t, cmd, why in stuck[0]["history_tail"][-6:]:
                print(f"    [{fmt_time(t)}] {cmd}   ← {why}")

    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f"# 盲玩测试汇总（policy={args.policy}，"
                    f"seed {args.seed_base}~{args.seed_base + args.runs - 1}）\n\n")
            f.write("| seed | 结果 | 用时(分) | 覆盖 | 设施 | 单元(效能) | 固化 | DB "
                    "| 记忆 | 停摆(设施·秒) | 墙钟(s) |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
            for r in results:
                f.write(f"| {r['seed']} | {r['outcome']} | {r['minutes']} "
                        f"| {r['coverage']} | {r['facilities']} "
                        f"| {r['units']}(×{r['efficiency']}) | {r['permanent']} "
                        f"| {r['db'][0]}/{r['db'][1]} | {r['memory']} "
                        f"| {r['stall_seconds']} | {r['wall']} |\n")
            f.write("\n## 未覆盖项\n\n")
            for r in results:
                f.write(f"- seed {r['seed']}："
                        + ("、".join(r["coverage_missing"]) or "（全覆盖）") + "\n")
        print(f"  汇总已写入 {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
