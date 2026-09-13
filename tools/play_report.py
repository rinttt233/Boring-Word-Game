#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""play_report.py —— 把试玩采样 CSV 变成"数值上的体感"报告。

用法:
    python -X utf8 tools/play_report.py [saves/ai_samples.csv]

输出：时间跨度、各关键量的区间与拐点、停摆占比、单元利用率、记忆曲线、
维护件断供区间，以及几条用的文本迷你图（无第三方依赖）。
"""
import os
import sys

SPARK = "▁▂▃▄▅▆▇█"
COLS = ["t", "electricity", "coal", "steel", "kit", "units", "idle", "facs",
        "stalled", "memory", "efficiency", "stall_seconds", "breakdowns"]
LABELS = {
    "electricity": "电力库存", "coal": "煤", "steel": "钢", "kit": "维护件",
    "units": "执行单元", "idle": "空闲单元", "facs": "设施数",
    "stalled": "停摆设施", "memory": "记忆完整度", "efficiency": "单元效能",
    "stall_seconds": "累计停摆(秒)", "breakdowns": "故障停机(次)",
}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    head = lines[0].split(",")
    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) != len(head):
            continue
        row = {}
        for k, v in zip(head, parts):
            try:
                row[k] = float(v) if v != "" else None
            except ValueError:
                row[k] = None
        rows.append(row)
    return rows


def spark(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(" ")
            continue
        idx = int((v - lo) / (hi - lo) * (len(SPARK) - 1))
        out.append(SPARK[idx])
    return "".join(out)


def fmt(v, nd=1):
    return "—" if v is None else f"{v:,.{nd}f}"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "saves", "ai_samples.csv")
    if not os.path.exists(path):
        print(f"[报告] 找不到采样文件: {path}")
        return 2
    rows = load(path)
    if len(rows) < 2:
        print("[报告] 采样点不足（至少 2 个）。")
        return 2
    t0, t1 = rows[0]["t"], rows[-1]["t"]
    print(f"# 试玩遥测报告  {os.path.relpath(path)}")
    print(f"采样 {len(rows)} 点，覆盖游戏时间 {t0 / 60:.1f} → {t1 / 60:.1f} 分钟"
          f"（{t1 - t0:.0f} 游戏秒）\n")

    print("## 关键量")
    print("| 指标 | 起点 | 最低 | 最高 | 终点 |")
    print("|---|---|---|---|---|")
    for c in COLS[1:]:
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if not vals:
            continue
        label = LABELS.get(c, c)
        if c in ("units", "facs", "stalled", "kit", "electricity", "coal",
                 "steel", "breakdowns"):
            unit = {"kit": " 件", "electricity": " 电", "coal": " t",
                    "steel": " t", "units": " 个", "facs": " 座",
                    "stalled": " 座", "breakdowns": " 次"}.get(c, "")
        else:
            unit = "%" if c == "memory" else ("" if c != "efficiency" else "×")
        v0, v1 = vals[0], vals[-1]
        unit_txt = unit if c != "efficiency" else ""
        print(f"| {label} | {fmt(v0)}{unit_txt} | {fmt(min(vals))} | "
              f"{fmt(max(vals))} | {fmt(v1)}{unit_txt} |")

    print("\n## 曲线（▁=最低 █=最高，每格一个采样点）")
    for c in ("electricity", "kit", "facs", "stalled", "memory",
              "efficiency", "idle"):
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if not vals:
            continue
        print(f"  {LABELS.get(c, c):<12} {spark(vals)}  "
              f"({fmt(min(vals))} → {fmt(max(vals))})")

    # 停摆占比：相邻采样点间的停摆设施数 × 时间
    stalls = [(r["t"], r.get("stalled") or 0) for r in rows]
    total_t = sum(stalls[i + 1][0] - stalls[i][0]
                  for i in range(len(stalls) - 1))
    stall_fac_s = sum(stalls[i][1] * (stalls[i + 1][0] - stalls[i][0])
                      for i in range(len(stalls) - 1))
    fac_avg = sum(r.get("facs") or 0 for r in rows) / len(rows)
    print("\n## 运转质量")
    print(f"  平均设施数 {fac_avg:.1f}｜停摆设施·时间占比 "
          f"{(stall_fac_s / (total_t * fac_avg) * 100) if total_t and fac_avg else 0:.1f}%")
    idle = [r.get("idle") or 0 for r in rows]
    units = [r.get("units") or 0 for r in rows]
    util = [1 - (i / u) for i, u in zip(idle, units) if u]
    if util:
        print(f"  执行单元平均利用率 {sum(util) / len(util) * 100:.1f}%"
              f"（最低 {min(util) * 100:.0f}%）")
    kits = [(r["t"], r.get("kit")) for r in rows if r.get("kit") is not None]
    short = [t for t, k in kits if k is not None and k <= 0.05]
    if kits and kits[0][1] is not None and (max(k for _t, k in kits) > 0):
        print(f"  维护件：最低 {min(k for _t, k in kits):.1f}，"
              f"断供采样点 {len(short)}/{len(kits)}")
    mem = [r.get("memory") for r in rows if r.get("memory") is not None]
    if mem:
        print(f"  记忆：最低 {min(mem):.1f}%，"
              f"低于告警阈值(30%)的采样点 {sum(1 for m in mem if m < 30)}/{len(mem)}")
    last = rows[-1]
    print(f"\n结束状态：设施 {fmt(last.get('facs'), 0)}｜单元 {fmt(last.get('units'), 0)}"
          f"（空闲 {fmt(last.get('idle'), 0)}）｜累计停摆 "
          f"{fmt(last.get('stall_seconds'), 0)} 设施·秒｜故障 "
          f"{fmt(last.get('breakdowns'), 0)} 次｜效能 ×{fmt(last.get('efficiency'), 2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
