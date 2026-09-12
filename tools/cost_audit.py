#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成本/容量基线测量（批次0，**只读**，不接入运行时）。

用途：把恢复条目与设施成本折算成"设施·秒 + 单元·秒"，并评估
"执行单元槽位需求 vs 可用单元"，为批次1（效率科技）与批次2（维护压力）
选定系数提供依据。

用法:
    python -X utf8 tools/cost_audit.py              # 打印 Markdown 报告
    python -X utf8 tools/cost_audit.py --write      # 写入 docs/baseline_report.md
    python -X utf8 tools/cost_audit.py --max-depth 5

本脚本不修改任何游戏状态、不写存档；只读取 content/*.json。
"""
import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 效率档位：用于评估"1 个单元能顶几个槽位"
EF_TIERS = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
# 劣化增速系数候选：degrade = base + k × 固化条目数
K_CANDIDATES = [0.0, 0.002, 0.004, 0.006, 0.008, 0.01]
FIXATE_DURATION = 6.0     # systems/recovery.py 中固化作业固定 6s


def load(name):
    with open(os.path.join(ROOT, "content", name), "r", encoding="utf-8") as f:
        return json.load(f)


def is_mainline(entry):
    return not entry.get("optional", False)


class Audit:
    def __init__(self, max_depth=4):
        self.max_depth = max_depth
        self.subs = {s["id"]: s for s in load("substances.json")["substances"]}
        self.facs = {d["id"]: d for d in load("facilities.json")["facilities"]}
        self.recipes = load("recipes.json")["recipes"]
        self.entries = load("recovery.json")["entries"]
        self.regions = load("regions.json")
        self.memory = load("memory.json")
        self.bootstrap = load("bootstrap.json")
        # 物质 → 产出它的配方（取产率最高者，代表"主工艺路线"）
        self.producer = {}
        for r in self.recipes:
            for sid, rate in r.get("outputs", {}).items():
                cur = self.producer.get(sid)
                if cur is None or float(rate) > float(
                        cur.get("outputs", {}).get(sid, 0)):
                    self.producer[sid] = r
        # 提取设施代表（extract_rate 最大）
        self.extract_rate = 0.0
        self.extract_name = "-"
        for d in self.facs.values():
            er = float(d.get("extract_rate", 0.0))
            if er > self.extract_rate:
                self.extract_rate = er
                self.extract_name = d["name"]

    # ---- 折算 ------------------------------------------------------
    def facility_seconds(self, sub, amount, depth=0, seen=None):
        """把 amount 单位某物质折算为所需设施·秒（递归上游，深度封顶）。"""
        if amount <= 0:
            return 0.0
        seen = seen or frozenset()
        if depth > self.max_depth or sub in seen:
            return 0.0
        r = self.producer.get(sub)
        if r is not None:
            out_rate = float(r["outputs"][sub])
            if out_rate <= 0:
                return 0.0
            secs = amount / out_rate            # 本设施占用时间
            for iid, irate in r.get("inputs", {}).items():
                secs += self.facility_seconds(
                    iid, amount * float(irate) / out_rate,
                    depth + 1, seen | {sub})
            return secs
        if self.extract_rate > 0:
            return amount / self.extract_rate   # 视为采掘所得
        return 0.0

    def cost_seconds(self, cost):
        return sum(self.facility_seconds(sid, float(amt))
                   for sid, amt in cost.items())

    # ---- 报告各节 --------------------------------------------------
    def slot_stats(self):
        one = [d["id"] for d in self.facs.values() if int(d.get("slots", 1)) == 1]
        multi = [(d["id"], int(d.get("slots", 1))) for d in self.facs.values()
                 if int(d.get("slots", 1)) > 1]
        return one, multi

    def scale_overview(self):
        main = [e for e in self.entries if is_mainline(e)]
        one, multi = self.slot_stats()
        return [
            ("物质", len(self.subs)),
            ("设施", len(self.facs)),
            ("配方", len(self.recipes)),
            ("恢复条目", len(self.entries)),
            ("├ 主线（非 optional）", len(main)),
            ("└ 可选", len(self.entries) - len(main)),
            ("起始执行单元", len(self.bootstrap.get("units", []))),
            ("代表采掘设施", f"{self.extract_name} ({self.extract_rate:g}/s)"),
            ("单槽设施 / 多槽设施",
             f"{len(one)} / {len(multi)}"),
        ]

    def entry_rows(self):
        rows = []
        for e in self.entries:
            fs = self.cost_seconds(e.get("cost", {})) \
                + self.cost_seconds(e.get("fixate_cost", {}))
            us = float(e.get("duration", 10.0)) + FIXATE_DURATION
            deps = len(e.get("depends_on", []))
            rows.append({
                "id": e["id"], "name": e.get("name", ""),
                "main": is_mainline(e), "deps": deps,
                "facility_seconds": fs, "unit_seconds": us,
                "cost": e.get("cost", {}), "fixate": e.get("fixate_cost", {}),
            })
        rows.sort(key=lambda r: (-r["facility_seconds"],))
        return rows

    def stage_rows(self):
        """按主线依赖顺序累计"已解锁设施的槽位需求"。"""
        main = [e for e in self.entries if is_mainline(e)]
        order, placed = [], set()
        remaining = list(main)
        while remaining:
            progressed = False
            for e in list(remaining):
                deps = e.get("depends_on", [])
                if all(d in placed or d not in {x["id"] for x in main}
                       for d in deps):
                    order.append(e)
                    placed.add(e["id"])
                    remaining.remove(e)
                    progressed = True
            if not progressed:            # 依赖成环/乱序 → 余下按原序追加
                order.extend(remaining)
                break
        base_slots = sum(int(d.get("slots", 1)) for d in self.facs.values()
                         if not d.get("requires_recovery"))
        rows, cumulative, unlocked = [], base_slots, set()
        for e in order:
            unlocked.add(e["id"])
            slots = sum(int(d.get("slots", 1)) for d in self.facs.values()
                        if d.get("requires_recovery") in unlocked)
            cumulative = base_slots + slots
            rows.append({"id": e["id"], "name": e.get("name", ""),
                         "cum_slots": cumulative,
                         "new_facs": [d["id"] for d in self.facs.values()
                                      if d.get("requires_recovery") == e["id"]]})
        return rows, base_slots

    def unit_supply(self):
        """可用执行单元：起步 + 残骸回收期望（按圈层残骸占比）。"""
        boot = len(self.bootstrap.get("units", []))
        share = {}
        for reg in self.regions.get("regions", []):
            pools = reg.get("pools", [])
            total = sum(float(p.get("weight", 0)) for p in pools) or 1.0
            wr = sum(float(p.get("weight", 0)) for p in pools
                     if p.get("kind") == "wreck")
            share[reg.get("ring")] = wr / total
        return boot, share

    def memory_rows(self):
        """按 content/memory.json 的**实际**劣化模型给出场景表。"""
        m = self.memory
        base = float(m.get("degrade_per_sec", 0.02))
        scale = m.get("degrade_scale", {}) or {}
        kf = float(scale.get("per_fixated_entry", 0.0))
        kr = float(scale.get("per_running_facility", 0.0))
        mc = m.get("maintain", {})
        restore = float(mc.get("restore", 25.0))
        tiers = mc.get("restore_tiers", []) or []
        dur = float(mc.get("duration", 8.0))
        cost = mc.get("cost", {})
        max_int = float(m.get("max_integrity", 100.0))
        scenarios = [(0, 0), (3, 4), (8, 10), (13, 15), (13, 22)]
        rows = []
        for n, run in scenarios:
            deg = base + kf * n + kr * run
            per_hour = deg * 3600.0 / max(restore, 1e-9)
            rows.append({
                "fixated": n, "running": run, "degrade": deg,
                "minutes": max_int / deg / 60.0 if deg > 0 else float("inf"),
                "maintains_per_hour": per_hour,
                "unit_seconds_per_hour": per_hour * dur,
                "electricity_per_hour": per_hour * float(
                    cost.get("electricity", 0)),
                "alloy_per_hour": per_hour * float(cost.get("scrap_alloy", 0)),
            })
        return {"rows": rows, "base": base, "kf": kf, "kr": kr,
                "restore": restore, "tiers": tiers, "dur": dur,
                "cost": cost, "max_integrity": max_int}

    def upkeep_rows(self):
        """维护件收支（批次2b）：按运转设施数估算需求与所需维护站数。"""
        try:
            m = load("maintenance.json")
        except Exception:
            return None
        use = float(m.get("use_per_sec", 0.01))
        idle = float(m.get("idle_factor", 0.33))
        kit = m.get("kit", "maintenance_kit")
        # 维护站产能 = 配方产出速率 × 单元效能
        out_rate = 0.0
        inputs = {}
        for r in self.recipes:
            if kit in r.get("outputs", {}):
                out_rate = float(r["outputs"][kit])
                inputs = r.get("inputs", {})
        rows = []
        for n in (5, 10, 15, 20, 30):
            need = use * n
            depots = (need / out_rate) if out_rate > 0 else float("inf")
            rows.append({
                "facilities": n, "demand": need,
                "demand_hour": need * 3600.0,
                "depots": depots,
                "depots_ef2": (need / (out_rate * 2.0)) if out_rate else 0.0,
                "inputs_hour": {k: v / out_rate * need * 3600.0
                                for k, v in inputs.items()} if out_rate else {},
            })
        return {"use": use, "idle": idle, "out_rate": out_rate, "rows": rows,
                "breakdown": m.get("breakdown", {})}


def fmt(v, nd=1):
    return f"{v:,.{nd}f}"


def build_report(a: Audit) -> str:
    out = []
    out.append("# 基线报告（批次0 · 由 `tools/cost_audit.py` 自动生成）\n")
    out.append("> 只读统计：不修改游戏状态。用于批次1（效率科技）与批次2"
               "（维护压力）的系数选型。修改内容后请重新生成。\n")

    out.append("## 1. 规模总览\n")
    out.append("| 项目 | 数量 |")
    out.append("| --- | --- |")
    for k, v in a.scale_overview():
        out.append(f"| {k} | {v} |")

    rows = a.entry_rows()
    main_rows = [r for r in rows if r["main"]]
    out.append("\n## 2. 恢复成本折算（设施·秒）\n")
    out.append(f"主线条目 {len(main_rows)} 条，合计 "
               f"{fmt(sum(r['facility_seconds'] for r in main_rows))} 设施·秒；"
               f"全条目合计 "
               f"{fmt(sum(r['facility_seconds'] for r in rows))} 设施·秒。\n")
    out.append("| 条目 | 名称 | 主线 | 前置 | 设施·秒 | 单元·秒 |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        out.append(f"| `{r['id']}` | {r['name']} | "
                   f"{'✔' if r['main'] else '可选'} | {r['deps']} | "
                   f"{fmt(r['facility_seconds'])} | {fmt(r['unit_seconds'], 0)} |")

    stages, base_slots = a.stage_rows()
    boot, share = a.unit_supply()
    out.append("\n## 3. 单元槽位覆盖分析\n")
    out.append(f"- 基础（无需恢复即可建的）设施槽位合计：**{base_slots}**")
    out.append(f"- 起始执行单元：**{boot}**；残骸地块中的执行单元期望 "
               f"= 残骸地块数 × 1\n")
    out.append("主线各阶段累计槽位需求（假设此前的设施仍在运转 —— 上界）：\n")
    out.append("| 阶段（固化后解锁） | 新增设施 | 累计槽位 |")
    out.append("| --- | --- | --- |")
    for s in stages:
        new = ", ".join(f"`{x}`" for x in s["new_facs"]) or "-"
        out.append(f"| {s['name']} | {new} | {s['cum_slots']} |")

    caps = [s["cum_slots"] for s in stages] or [base_slots]
    one, multi = a.slot_stats()
    out.append(f"\n槽位折算实验（**仅对多槽设施产生合并效果**；当前 "
               f"单槽设施 {len(one)} 个、多槽设施 {len(multi)} 个 "
               f"{[f'{i}×{n}' for i, n in multi]}）：\n")
    out.append("| 单元效能 ef | 终局槽位需求（Σ⌈slots/ef⌉） | 相对无加成节省 |")
    out.append("| --- | --- | --- |")
    worst = max(caps)
    for ef in EF_TIERS:
        need = sum(math.ceil(c / ef) for c in caps)
        plain = sum(caps)
        out.append(f"| ×{ef:g} | {need} | "
                   f"{'—' if plain == 0 else f'{100 * (1 - need / plain):.0f}%'} |")
    out.append(f"\n> 结论：槽位折算只减少多槽设施的单元占用（终局上界 {worst} 槽位 → "
               "满加成时仍远超起始单元数）。因此效率科技对『单元瓶颈』的有效形式是"
               "**提升单位产能**（同样单元数产出更多），而不是字面意义上的槽位合并；"
               "若要保留槽位语义，需在 content 层为大型设施引入 `min_units` 字段。")
    out.append("残骸地块占比（每圈层勘探所得地块中的比例）："
               + "、".join(f"圈{k} {v * 100:.0f}%" for k, v in sorted(share.items()))
               + "。\n")

    mem = a.memory_rows()
    out.append("## 4. 记忆压力预算（批次2a 起随规模增长）\n")
    out.append(f"劣化 = {mem['base']:g}/s + {mem['kf']:g}/s × 已固化条目数 "
               f"+ {mem['kr']:g}/s × 运转设施数；"
               f"加固恢复分档 {[t.get('restore') for t in mem['tiers']] or mem['restore']}"
               f"（越接近满值越少），耗时 {mem['dur']:g}s，成本 {mem['cost']}。\n")
    out.append("| 已固化条目 | 运转设施 | 劣化/s | 满值归零 | 加固次数/h "
               "| 单元·秒/h | 电/h | 合金/h |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in mem["rows"]:
        out.append(f"| {r['fixated']} | {r['running']} | {r['degrade']:.3f} | "
                   f"{r['minutes']:.0f} 分 | {r['maintains_per_hour']:.2f} | "
                   f"{fmt(r['unit_seconds_per_hour'])} | "
                   f"{fmt(r['electricity_per_hour'])} | "
                   f"{fmt(r['alloy_per_hour'])} |")

    out.append("\n## 5. 维护件收支（批次2b）\n")
    up = a.upkeep_rows()
    if up is None:
        out.append("（未找到 content/maintenance.json）")
    else:
        out.append(f"维护件消耗 {up['use']:g}/s·台（停着的按 "
                   f"{up['idle'] * 100:.0f}% 计），维护站产能 "
                   f"{up['out_rate']:g}/s；故障阈值 "
                   f"{up['breakdown'].get('threshold')}、"
                   f"最大故障概率 {up['breakdown'].get('max_chance_per_min')}/分钟。\n")
        out.append("| 运转设施数 | 需求/s | 需求/小时 | 需维护站(×1.0) "
                   "| 需维护站(×2.0) | 维护站原料/小时 |")
        out.append("| --- | --- | --- | --- | --- | --- |")
        for r in up["rows"]:
            mats = "、".join(f"{k} {v:.0f}" for k, v in r["inputs_hour"].items())
            out.append(f"| {r['facilities']} | {r['demand']:.2f} | "
                       f"{r['demand_hour']:.0f} | {r['depots']:.1f} | "
                       f"{r['depots_ef2']:.1f} | {mats} |")
        out.append("\n> 结论：维护件把「设施数量」变成了持续流水支出 —— "
                   "每 10 台运转设施约需 0.1/s（360/小时），"
                   "一台维护站（含单元效能加成）可覆盖 15~30 台。")

    out.append("\n## 6. 结论\n")
    out.append(f"- 记忆压力已随规模增长：空厂 20.8 分钟/次加固，"
               f"终局（13 条固化 + 22 台运转）约 "
               f"{mem['rows'][-1]['minutes']:.0f} 分钟归零、"
               f"{mem['rows'][-1]['maintains_per_hour']:.1f} 次加固/小时 ——"
               "贯穿全局的持续压力。")
    out.append(f"- 单元供给：起始 {boot} + 残骸回收；效率科技（×1.0→×2.5）"
               "以「单位产能」而非「槽位合并」缓解瓶颈。")
    out.append("- 维护件把设施数量变成流水支出（见第 5 节）；"
               "任何系数改动都需重跑 `victory_test.py` 与 `gameplay_test.py`。")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="成本/容量基线测量（只读）")
    ap.add_argument("--write", action="store_true",
                    help="写入 docs/baseline_report.md")
    ap.add_argument("--max-depth", type=int, default=4,
                    help="上游折算递归深度上限（默认4）")
    args = ap.parse_args()
    rep = build_report(Audit(max_depth=args.max_depth))
    if args.write:
        path = os.path.join(ROOT, "docs", "baseline_report.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(rep)
        print(f"[基线] 已写入 {os.path.relpath(path, ROOT)}"
              f"（{len(rep.splitlines())} 行）")
    else:
        sys.stdout.write(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
