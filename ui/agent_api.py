"""agent_api —— 给"盲玩 AI"的三件套：可解析状态、下一步建议、内容覆盖清单。

设计原则（见 docs/blindtest_plan.md）：
- **只读**：本模块不改变任何游戏状态，只把状态整理成机器可读形式；
- **不依赖界面**：不 import tkinter，控制台/试玩回路/盲测脚本都能用；
- `report` 的 JSON 契约稳定（带 report_version，字段只增不改）；
- `suggest` 只给建议，不自动执行 —— 决策权留给 AI/玩家。
"""
import io
import json
import os
from typing import Dict, List, Optional

REPORT_VERSION = 1
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_content(name: str) -> dict:
    path = os.path.join(ROOT, "content", name)
    if not os.path.exists(path):
        return {}
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ===================== 1) 可解析状态 =====================
def build_report(engine, router=None) -> dict:
    """把引擎状态整理为固定 schema 的 dict（JSON 可序列化）。"""
    reg = engine.registry
    get = reg.get
    ind = get("industry")
    mem = get("memory")
    rec = get("recovery")
    db = get("database")
    maint = get("maintenance")
    env = get("environment")
    dl = get("daylight")
    ep = engine.economy

    def rname(x):
        return router.rname(x) if router is not None else x

    fac_list = []
    if ind is not None:
        for f in sorted(ind.facilities.values(), key=lambda x: x.id):
            d = ind.defs.get(f.def_id, {})
            state = "running"
            if f.under_construction:
                state = "building"
            elif f.mothballed:
                state = "mothballed"
            elif f.halt_until > engine.clock.time:
                state = "halted"
            elif f.stalled_reported:
                state = "stalled"
            elif not f.assigned:
                state = "unstaffed"
            fac_list.append({
                "id": f.id, "def": f.def_id, "name": f.name,
                "plot": f.plot_id, "assigned": list(f.assigned),
                "state": state, "reason": f.stall_reason,
                "reason_code": f.stall_code,
                "upkeep": round(float(f.upkeep), 1),
                "build_left": (round(ind.build_progress(engine, f.id), 1)
                               if f.under_construction else None),
                "fuel": f.fuel,
                "slots": int(d.get("slots", 1)),
                "stored": round(float(getattr(f, "stored", 0.0)), 1),
                "backlog_over": getattr(f, "backlog_over", None),
                "unit_progress": round(float(getattr(f, "unit_progress", 0.0)),
                                       3),
                "policy": (getattr(ind, "backlog_policy", None)
                           if ind is not None else None),
            })

    plot_list = []
    for p in sorted(engine.world.visible_plots(), key=lambda x: (x.ring, x.id)):
        fac = next((f for f in (ind.facilities.values() if ind else [])
                    if f.plot_id == p.id), None)
        plot_list.append({
            "id": p.id, "ring": p.ring, "kind": p.kind,
            "substance": p.substance,
            "substance_name": rname(p.substance) if p.substance else None,
            "state": p.state,
            "grade_true": round(p.grade, 3) if p.grade else 0,
            "grade_est": (round(p.known_grade, 2)
                          if p.surveyed and p.known_grade is not None else None),
            "grade_err": round(p.grade_err, 3),
            "reserve": round(p.reserve, 1),
            "reserve_est": (round(p.known_reserve, 1)
                            if p.surveyed and p.known_reserve is not None
                            else None),
            "logistics": p.logistics_multiplier(),
            "facility": fac.id if fac else None,
        })

    entries = {"locked": [], "active": [], "permanent": [], "optional": []}
    if rec is not None:
        for eid, st in rec.status.items():
            e = rec.entries.get(eid, {})
            item = {"id": eid, "name": e.get("name", eid),
                    "optional": bool(e.get("optional")),
                    "cost": e.get("cost", {}),
                    "fixate_cost": e.get("fixate_cost", {}),
                    "depends_on": e.get("depends_on", []),
                    "unlocks_facility": e.get("unlocks_facility", []),
                    "grants": e.get("grants", {})}
            if st == "active":
                # 临时窗口倒计时（游戏秒；BUG-5）+ 是否已排队等空闲单元
                left = (rec.expires_in(engine, eid)
                        if hasattr(rec, "expires_in") else None)
                item["expires_in"] = round(left, 1) if left is not None else None
                item["fixate_queued"] = bool(
                    hasattr(rec, "is_queued") and rec.is_queued(eid))
            entries[{"locked": "locked", "active": "active",
                     "permanent": "permanent"}[st]].append(item)
            if item["optional"]:
                entries["optional"].append(eid)

    power = {"produce": 0.0, "consume": 0.0, "net": 0.0,
             "stored": round(ep.get("electricity"), 2),
             "consumers": [], "producers": []}
    if ind is not None:
        pb = ind.power_balance(engine)
        power.update({"produce": round(pb["produce"], 3),
                      "consume": round(pb["consume"], 3),
                      "net": round(pb["produce"] - pb["consume"], 3),
                      "consumers": [[n, round(v, 3)] for n, v in pb["consumers"]],
                      "producers": [[n, round(v, 3)] for n, v in pb["producers"]]})

    maint_info = {"enabled": False}
    if maint is not None:
        enabled = maint.enabled(engine)
        worst = maint.worst_upkeep(engine) if enabled else None
        demand = maint.demand_per_sec(engine) if enabled else 0.0
        kit = ep.get(maint.kit)
        maint_info = {
            "enabled": enabled,
            "requires": maint.requires,
            "kit": round(kit, 2),
            "kit_name": rname(maint.kit),
            "demand": round(demand, 4),
            "worst_upkeep": (round(worst, 1) if worst is not None else None),
            "seconds_left": (round(kit / demand, 1)
                             if enabled and demand > 0 else None),
        }

    unknown = [p.id for p in engine.world.plots.values()
               if p.state == "unknown"]
    report = {
        "report_version": REPORT_VERSION,
        "seed": getattr(engine, "seed", None),
        "command_count": int(getattr(router, "exec_count", 0) or 0),
        "t": round(engine.clock.time, 1),
        "paused": bool(engine.clock.paused),
        "memory": ({
            "integrity": round(mem.integrity, 2),
            # disabled（劣化已终止）时实际劣化 = 0；rated 保留原公式值供分析
            "degrade": 0.0 if getattr(mem, "_degradation_disabled", False)
            else round(mem.degrade_rate(engine), 4),
            "degrade_rated": round(mem.degrade_rate(engine), 4),
            "warn": mem.warn_threshold, "crisis": mem.crisis_threshold,
            "disabled": bool(getattr(mem, "_degradation_disabled", False)),
        } if mem else None),
        "units": {
            "total": engine.units.count(),
            "idle": engine.units.count_idle(),
            "efficiency": round(engine.units.efficiency, 3),
            "list": [{"id": u.id, "task": u.task, "status": u.status}
                     for u in engine.units.units],
        },
        "power": power,
        "resources": {k: round(v, 2) for k, v in
                      sorted(ep.snapshot().items(), key=lambda kv: -kv[1])},
        "facilities": fac_list,
        "plots": plot_list,
        "plots_unknown": unknown,
        "entries": entries,
        "database": ({
            "built": len(db.built), "projects": len(db.projects),
            "list": list(db.built),
            # can_migrate = 子系统建完（条件），complete/migrated = 已迁移
            "can_migrate": bool(len(db.built) >= len(db.projects)),
            "complete": bool(db.is_complete()),
            "migrated": bool(getattr(db, "migrated", False)
                             or db.is_complete()),
        } if db else None),
        # 显式终局信号：劣化终止（= 烧录迁移成功）即为通关
        "victory": bool(mem and getattr(mem, "_degradation_disabled", False)),
        "deadlock": (lambda s: s)(
            (ind.power_deadlock(engine)
             if ind is not None and hasattr(ind, "power_deadlock") else None)),
        "maintenance": maint_info,
        "backlog": (ind.backlog_status(engine)
                    if ind is not None and hasattr(ind, "backlog_status")
                    else {}),
        "jobs": [{"kind": j.kind, "target": j.target_id,
                  "unit": j.unit_id, "left": round(j.remaining, 1)}
                 for j in engine.jobs.list_jobs()],
        "environment": ({"event": env.current_id,
                         "name": (env.current() or {}).get("name")}
                        if env is not None and env.current() else None),
        "daylight": (dl.phase_text() if dl is not None else None),
        "stats": {k: round(v, 2) for k, v in engine.stats.snapshot().items()},
    }
    return report


# ===================== 2) 下一步建议 =====================
def _affordable(engine, cost: Dict[str, float]) -> bool:
    return all(engine.economy.get(k) >= float(v) - 1e-9
               for k, v in (cost or {}).items())


def _missing(engine, cost: Dict[str, float], router=None) -> str:
    out = []
    for k, v in (cost or {}).items():
        have = engine.economy.get(k)
        if have < float(v) - 1e-9:
            name = router.rname(k) if router is not None else k
            out.append(f"{name} 缺 {float(v) - have:.0f}")
    return "、".join(out)


def _unit_block(idle: int) -> str:
    return "" if idle > 0 else "需要 1 个空闲执行单元"


def suggest_actions(engine, router=None, limit: int = 5) -> List[dict]:
    """按优先级给出"现在最该做"的动作；只建议不执行。"""
    reg = engine.registry
    ind = reg.get("industry")
    rec = reg.get("recovery")
    db = reg.get("database")
    mem = reg.get("memory")
    maint = reg.get("maintenance")
    out: List[dict] = []

    def rname(x):
        return router.rname(x) if router is not None else x

    def add(cmd, why, blocked="", wait=False):
        out.append({"cmd": cmd, "why": why, "blocked": blocked, "wait": wait})

    if ind is None:
        return out
    facs = list(ind.facilities.values())
    idle = engine.units.count_idle()
    unknown = [p for p in engine.world.plots.values() if p.state == "unknown"]

    def _is_power(f) -> bool:
        d = ind.defs.get(f.def_id, {})
        return (d.get("kind") in ("burner", "renewable")
                or "electricity" in ind.recipes.get(
                    d.get("recipe", ""), {}).get("outputs", {}))

    def _burns_coal(f) -> bool:
        d = ind.defs.get(f.def_id, {})
        if d.get("kind") == "burner":
            return True
        return "coal" in ind.recipes.get(d.get("recipe", ""),
                                         {}).get("inputs", {})

    def _free_empty():
        return next((p for p in engine.world.plots.values()
                     if p.state in ("claimed", "developed", "depleted")
                     and p.kind == "empty"
                     and not any(f.plot_id == p.id for f in facs)), None)

    # 0) BUG-5：临时条目即将过期 → 最高优先级，先保住知识
    if rec is not None and hasattr(rec, "expires_in"):
        urgent = []
        for eid, st in rec.status.items():
            if st != "active":
                continue
            left = rec.expires_in(engine, eid)
            if left is None or left > 40.0:
                continue
            urgent.append({"id": eid,
                           "name": rec.entries.get(eid, {}).get("name", eid),
                           "expires_in": left,
                           "fixate_cost": rec.entries.get(eid, {}).get(
                               "fixate_cost", {}),
                           "fixate_queued": rec.is_queued(eid)})
        if urgent:
            urgent.sort(key=lambda e: e["expires_in"])
            e = urgent[0]
            queued = "（已排队等单元）" if e["fixate_queued"] else ""
            add(f"fixate {e['id']}",
                f"临时条目「{e['name']}」只剩 {e['expires_in']:.0f}s{queued}，"
                "过期就丢（要重付恢复材料）",
                _missing(engine, e["fixate_cost"], router)
                or _unit_block(idle))

    # 1) 还没电：第一优先
    has_power = any(_is_power(f) for f in facs if not f.under_construction)
    if not has_power:
        home = engine.world.get("HOME")
        if home is not None and home.state == "claimed":
            add("build HOME power_plant", "尚无发电设施，所有产线都会停摆",
                "" if idle > 0 else "需要 1 个空闲执行单元")
        elif home is not None and home.state == "developed":
            add("assign F1", "发电站已建好但没派单元，它不会运转")
        elif home is not None and home.state in ("known", "unknown"):
            add("claim HOME", "先把 HOME 占下来才能建电站")

    # 1.5) 电力节流：电量够、煤快没了 → 先停一台烧煤电站省煤（要用电再 assign）
    #      （盲测阶段机实测：早期煤只有 60t 时，这一手能把"150 秒断煤"变成悠闲找矿）
    if has_power:
        stored = engine.economy.get("electricity")
        coal_now = engine.economy.get("coal")
        if coal_now < 200 and stored > 120:
            burners = [f for f in facs
                       if f.assigned and not f.under_construction
                       and not f.mothballed and _is_power(f) and _burns_coal(f)]
            if burners:
                add(f"unassign {burners[0].id}",
                    f"电量已存 {stored:.0f}kWh 而煤只剩 {coal_now:.0f}："
                    f"先停「{burners[0].name}」省煤，缺电时再 assign")

    # 2) 施工中：等完工
    building = [f for f in facs if f.under_construction]
    if building:
        left = ind.build_progress(engine, building[0].id) or 10.0
        add(f"@tick {int(left) + 3}", f"{building[0].name} 施工中（剩约 "
                                      f"{left:.0f}s）", wait=True)

    # 3) 有设施没派员且有闲单元
    if idle > 0:
        for f in facs:
            if not f.assigned and not f.under_construction and not f.mothballed:
                add(f"assign {f.id}", f"{f.name} 已建成但没有执行单元")
                break
    else:
        # 没有空闲单元 → 建造/研究/加固全被卡住：腾一个出来
        extractors = [f for f in facs if f.assigned and not f.under_construction
                      and not f.mothballed
                      and ind.defs[f.def_id].get("extract_rate")]
        if len(extractors) >= 3:
            def _plot_score(f):
                p = engine.world.get(f.plot_id)
                grade = p.grade if p is not None else 0.0
                reserve = p.reserve if p is not None else 0.0
                return (grade, reserve)

            worst = min(extractors, key=_plot_score)
            add(f"unassign {worst.id}",
                f"没有空闲执行单元（建造/研究/加固都会被卡住）："
                f"先从品位最低的 {worst.name} 调离一个")

    # 4) 燃烧设施没燃料 / 燃料告急
    for f in facs:
        d = ind.defs[f.def_id]
        if d.get("kind") == "burner" and not f.fuel and not f.under_construction:
            opts = ind.fuel_options_for(f.def_id)
            have = [o for o in opts if engine.economy.get(o) > 5.0]
            if have:
                best = max(have, key=lambda o: ind.heat_values.get(o, 0))
                add(f"fuel {f.id} {best}", f"{f.name} 未设燃料",
                    f"（建议烧 {rname(best)}，热值最高）")
            else:
                add("", f"{f.name} 没有可用燃料", "库存里没有该类可燃物")

    # 6) 能源/原料告急 → 找矿
    coal = engine.economy.get("coal")
    if coal < 120 and any(p.substance == "coal" for p in engine.world.plots.values()):
        targets = [p for p in engine.world.plots.values()
                   if p.substance == "coal" and p.state == "known"]
        claimed = [p for p in engine.world.plots.values()
                   if p.substance == "coal" and p.state == "claimed"]
        if claimed:
            add(f"build {claimed[0].id} extractor",
                f"煤只剩 {coal:.0f}，已占领的煤矿还没开采",
                "" if idle > 0 else "需要 1 个空闲执行单元")
        elif targets:
            add(f"claim {targets[0].id}",
                f"煤只剩 {coal:.0f}（电站 0.4/s 会烧完），占领煤矿",
                "" if idle > 0 else "需要 1 个空闲执行单元")

    # 6.5) 合金告急：残骸回收站**免建造费**，是建造与固化材料的主来源
    scrap = engine.economy.get("scrap_alloy")
    if scrap < 40:
        wrecks = [p for p in engine.world.plots.values()
                  if p.kind == "wreck" and p.reserve > 0
                  and not any(f.plot_id == p.id for f in facs)]
        claimed_w = [p for p in wrecks
                     if p.state in ("claimed", "developed", "depleted")]
        known_w = [p for p in wrecks if p.state == "known"]
        if claimed_w:
            add(f"build {claimed_w[0].id} salvager",
                f"合金只剩 {scrap:.0f}（建造/固化都要它）：回收站免建造费，"
                "先拆残骸", _unit_block(idle))
        elif known_w:
            add(f"claim {known_w[0].id}",
                f"合金只剩 {scrap:.0f}：占领残骸地块后回收（拆完还可能返还一个单元）",
                _unit_block(idle))

    # 6) 记忆告急（没有空闲单元时，先腾一个出来）
    if mem is not None and not getattr(mem, "_degradation_disabled", False) \
            and mem.integrity < 45:
        if idle > 0:
            add("maintain", f"记忆完整度仅 {mem.integrity:.0f}%，"
                            f"劣化 {mem.degrade_rate(engine):.3f}/s")
        else:
            producers = [f for f in facs if f.assigned
                         and not f.under_construction
                         and (ind.defs[f.def_id].get("kind")
                              in ("burner", "renewable")
                              or "electricity" in ind.recipes.get(
                                  ind.defs[f.def_id].get("recipe", ""),
                                  {}).get("outputs", {}))]
            cand = next((f for f in facs if f.assigned
                         and not f.under_construction
                         and not f.mothballed
                         and f not in producers), None)
            if cand is not None:
                add(f"unassign {cand.id}",
                    f"记忆只剩 {mem.integrity:.0f}% 却没有空闲单元："
                    f"先从 {cand.name} 调离一个单元去 maintain")

    # 7) 恢复/固化（先固化临时条目，再解锁新条目）
    if rec is not None:
        active = [eid for eid, st in rec.status.items() if st == "active"]
        if active:
            add(f"fixate {active[0]}",
                f"条目「{rec.entries[active[0]].get('name', active[0])}」是临时的，"
                "记忆崩溃/超时会丢，尽快固化",
                _missing(engine, rec.entries[active[0]].get("fixate_cost", {}),
                         router))
        for eid in sorted(
                [e for e, st in rec.status.items() if st == "locked"],
                key=lambda e: (1 if rec.entries[e].get("optional") else 0)):
            e = rec.entries[eid]
            deps = e.get("depends_on", [])
            if any(rec.status.get(d) != "permanent" for d in deps):
                continue                     # 前置未固化，先做前置
            cost = e.get("cost", {})
            miss = _missing(engine, cost, router)
            # 恢复只是"临时可用"（约 90s）：固化材料没凑齐就先别 recover，
            # 否则窗口一过条目照样丢、材料白烧。
            fix_miss = _missing(engine, e.get("fixate_cost", {}), router)
            warn = (f"｜固化还需 {fix_miss}：先攒够再 recover（临时窗口只有 90s）"
                    if not miss and fix_miss else "")
            add(f"recover {eid}", f"可恢复知识「{e.get('name', eid)}」"
                                  + (f"（解锁 {'、'.join(e.get('unlocks_facility', []))}）"
                                     if e.get("unlocks_facility") else
                                     "（提升单元效能）" if e.get("grants") else "")
                                  + warn,
                miss or _unit_block(idle))

    # 8) 建造已解锁但还没建的设施
    if idle > 0:
        claimed = [p for p in engine.world.plots.values()
                   if p.state == "claimed"]
        built_defs = {f.def_id for f in facs}
        for d in ind.defs.values():
            did = d["id"]
            if did in built_defs:
                continue
            need = d.get("requires_recovery")
            if need and (rec is None or rec.status.get(need) != "permanent"):
                continue
            kinds = d.get("allowed_plot_kinds", [])
            plot = next((p for p in claimed
                         if p.kind in kinds and not any(
                             f.plot_id == p.id for f in facs)), None)
            if plot is None:
                continue
            add(f"build {plot.id} {did}",
                f"可建「{d.get('name', did)}」"
                + (f"（提供 {d.get('recipe', '')}）" if d.get("recipe") else ""),
                _missing(engine, d.get("build_cost", {}), router)
                or _unit_block(idle))
            break

    # 8.5) 执行单元是核心瓶颈：钢有富余而单元还少 → 建「执行单元装配厂」扩产
    if idle > 0 and rec is not None \
            and rec.status.get("db_units") == "permanent" \
            and not any(f.def_id == "unit_factory" for f in facs):
        total_u = engine.units.count()
        steel_now = engine.economy.get("steel")
        if total_u < 12 and steel_now > 60:
            empty = _free_empty()
            if empty is not None:
                add(f"build {empty.id} unit_factory",
                    f"只有 {total_u} 个执行单元而钢有 {steel_now:.0f}：装配厂把钢+铜"
                    "变成「能同时开更多产线」",
                    _missing(engine, ind.defs["unit_factory"].get("build_cost", {}),
                             router) or _unit_block(idle))

    # 9) 维护件
    if maint is not None and maint.enabled(engine):
        demand = maint.demand_per_sec(engine)
        kit = engine.economy.get(maint.kit)
        if demand > 0 and kit < demand * 60 and idle > 0:
            empty = next((p for p in engine.world.plots.values()
                          if p.state == "claimed" and p.kind == "empty"
                          and not any(f.plot_id == p.id for f in facs)), None)
            has_depot = any(f.def_id == "maintenance_depot" for f in facs)
            if empty is not None and not has_depot:
                add(f"build {empty.id} maintenance_depot",
                    f"维护件只剩 {kit:.1f}（需求 {demand:.2f}/s），需要维护站",
                    _missing(engine, ind.defs["maintenance_depot"]
                             .get("build_cost", {}), router))

    # 10) 数据库工程 / 迁移
    if db is not None:
        if len(db.built) >= len(db.projects):
            add("migrate", "数据库已全部竣工 —— 烧录迁移以通关")
        else:
            nxt = next((p for p in db.projects if p.get("id") not in db.built),
                       None)
            cost = (nxt or {}).get("cost", {})
            add("construct", f"建造数据库子系统「{(nxt or {}).get('name', '?')}」"
                             f"（{len(db.built)}/{len(db.projects)}）",
                _missing(engine, cost, router))

    # 11) 已占领的矿脉/残骸还没开采 → 建采掘设施（产线底座）
    if idle > 0:
        for p in engine.world.plots.values():
            if p.state != "claimed" or p.kind not in ("ore", "wreck", "water"):
                continue
            if any(f.plot_id == p.id for f in facs):
                continue
            fac_def = "salvager" if p.kind == "wreck" else "extractor"
            add(f"build {p.id} {fac_def}",
                f"{p.substance or p.kind} 已占领但还没开采",
                _missing(engine, ind.defs[fac_def].get("build_cost", {}), router)
                or _unit_block(idle))
            break

    # 12) 已知但未占领的地块 → 占领（先矿脉/残骸，再空地：工厂需要空地）
    if idle > 0:
        def _claim_rank(p):
            return (0 if p.kind in ("ore", "wreck") else 1, p.ring)

        for p in sorted(engine.world.plots.values(), key=_claim_rank):
            if p.state != "known":
                continue
            if p.kind in ("ore", "wreck"):
                add(f"claim {p.id}", f"已知 {p.substance or p.kind}"
                                     f"（圈{p.ring}）尚未占领")
                break
            if p.kind == "empty":
                add(f"claim {p.id}", f"已知空地（圈{p.ring}）尚未占领，"
                                     "建厂需要空地")
                break

    # 12) 副产物积压（批次3）：优先给超限副产物找出路，而不是继续扩产
    if ind is not None and hasattr(ind, "backlog_status"):
        over = [(k, v) for k, v in ind.backlog_status(engine).items()
                if v["over"]]
        if over:
            rid, info = max(over, key=lambda kv: kv[1]["stock"] / kv[1]["limit"])
            name = router.rname(rid) if router is not None else rid
            built = {f.def_id for f in facs}
            claimed_empty = [p for p in engine.world.plots.values()
                             if p.state in ("claimed", "depleted")
                             and p.kind == "empty"
                             and not any(f.plot_id == p.id for f in facs)]
            for cand, why in (("gas_shift", "转化为氢（水煤气变换）"),
                              ("vent_tower", "放空塔直接烧掉（浪费但最快）"),
                              ("injection_well", "回注井压进地下（占容量）")):
                d = ind.defs.get(cand)
                if d is None or cand in built:
                    continue
                need = d.get("requires_recovery")
                if need and (rec is None or rec.status.get(need) not in
                             ("permanent", "active")):
                    continue
                if not claimed_empty:
                    break
                add(f"build {claimed_empty[0].id} {cand}",
                    f"{name} 已积压 {info['stock']:g}/{info['limit']:g}"
                    f"（限产系数 ×{info['factor']:g}）：{why}",
                    _missing(engine, d.get("build_cost", {}), router)
                    or _unit_block(idle))
                break
            else:
                add("policy backlog ignore",
                    f"{name} 积压 {info['stock']:g}/{info['limit']:g}，"
                    "暂时没有可用出路（可先关闭限产政策自救）")

    # 13) 继续勘探（注意：这条永远在最后，作为兜底推进手段）
    if idle > 0:
        add("survey 1", "继续勘探寻找矿脉（煤/铁/石灰石等）")

    # 14) 兜底：暂时没有可执行动作 → 明确地"等"
    add("@tick 300", "暂时没有可执行的动作（等产出攒够 / 等作业完成 / 等天气）",
        wait=True)

    # 去重 + 截断（去掉建议里的空 cmd）
    seen = set()
    final = []
    for s in out:
        key = (s["cmd"], s["why"])
        if key in seen:
            continue
        seen.add(key)
        final.append(s)
        if len(final) >= limit:
            break
    return final


# ===================== 3) 覆盖清单 =====================
class CoverageTracker:
    """按清单判定"一局体验到了哪些内容"（粘性：一旦为真就永久记下）。"""

    def __init__(self, cfg: Optional[dict] = None) -> None:
        cfg = cfg if cfg is not None else _load_content(
            "blindtest_coverage.json")
        self.items = cfg.get("items", [])
        self.done: Dict[str, bool] = {it["id"]: False for it in self.items}

    def update(self, report: dict) -> None:
        scope = {"r": report, "any": any, "all": all, "len": len, "sum": sum,
                 "min": min, "max": max, "sorted": sorted, "set": set,
                 "float": float, "int": int, "str": str, "abs": abs,
                 # bool 必须给：清单里有 `bool(r.get('database')) and ...` 这类断言，
                 # 缺了会让该条目永远判不通过（且被 except 静默吞掉）。
                 "bool": bool, "list": list, "dict": dict, "round": round}
        for it in self.items:
            if self.done.get(it["id"]):
                continue
            try:
                if bool(eval(it["assert"], {"__builtins__": {}}, scope)):  # noqa: S307
                    self.done[it["id"]] = True
            except Exception:
                continue

    def progress(self) -> dict:
        total = len(self.items)
        done = [i for i in self.items if self.done.get(i["id"])]
        missing = [i for i in self.items if not self.done.get(i["id"])]
        return {"total": total, "done": len(done),
                "done_ids": [i["id"] for i in done],
                "missing": [{"id": i["id"], "desc": i.get("desc", ""),
                             "hint": i.get("hint", "")} for i in missing]}


# ===================== 4) 向导 =====================
def guide_text(section_id: str = "") -> str:
    """渲染向导（与 AGENTS.md 同源，见 content/guide.json）。"""
    import sys
    sys.path.insert(0, ROOT)
    from tools.make_guide import load_guide, render_console  # noqa: E402
    return render_console(load_guide(), section_id)
