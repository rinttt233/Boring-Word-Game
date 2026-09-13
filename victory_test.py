# -*- coding: utf-8 -*-
"""M3 完整通关链路：从出生 → 工艺链 → 可靠数据库竣工 → 劣化终止。

这是 v1 的"一局完整游戏"自动化验证（等价于一次顺利通关）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import build_engine          # noqa: E402


def spawn_claimed(eng, kind, substance=None, grade=0, reserve=0):
    eng.world.spawn(ring=0, kind=kind, substance=substance,
                    grade=grade, reserve=reserve, state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == kind and (substance is None or p.substance == substance)
            and p.state == "claimed"][-1]


def build_and_assign(eng, ind, plot_id, def_id):
    err = ind.build(eng, plot_id, def_id)
    assert err is None, f"build {def_id}@{plot_id} 失败: {err}"
    fid = [f.id for f in ind.facilities.values()
           if f.plot_id == plot_id][-1]
    ind.assign(eng, fid)
    return fid


def build_when_possible(eng, ind, plot_id, def_id, label, cap=600):
    """像玩家一样：等材料备齐再建造（材料不足则持续生产等待）。"""
    t = 0.0
    while t < cap:
        err = ind.build(eng, plot_id, def_id)
        if err is None:
            fid = [f.id for f in ind.facilities.values()
                   if f.plot_id == plot_id][-1]
            ind.assign(eng, fid)
            return fid
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"建造超时 {label}: {err}")


def run_until(eng, cond, max_s=300, step=0.5, label="条件"):
    t = 0.0
    while t < max_s:
        eng.tick(step)
        t += step
        if cond():
            return True
    raise AssertionError(f"超时: {label}")


def recover_when_possible(eng, rec, entry, label):
    t = 0.0
    while t < 900:
        err = rec.recover(eng, entry)
        if err is None:
            return run_until(eng, lambda: rec.is_unlocked(entry),
                             max_s=120, label=label)
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"无法恢复 {label}")


def fixate_when_possible(eng, rec, entry, label):
    t = 0.0
    while t < 600:
        err = rec.fixate(eng, entry)
        if err is None:
            return run_until(eng, lambda: rec.status[entry] == "permanent",
                             max_s=60, label=label)
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"无法固化 {label}")


def main():
    eng = build_engine()
    ind = eng.registry.get("industry")
    ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
    rec = eng.registry.get("recovery")
    db = eng.registry.get("database")
    mem = eng.registry.get("memory")
    eng.clock.resume()
    # 锁定晴天：本测试验证"能否通关"，不应被随机天气造成的收入波动干扰
    # （曾经出现过"最后一项数据库子系统缺 40 残骸合金"的偶发失败）
    env = eng.registry.get("environment")
    if env is not None:
        env.current_id = "fair"
        env._until = 1e18
    eng.economy.set("electricity", 5000.0)
    eng.economy.set("scrap_alloy", 1200.0)
    eng.economy.set("coal", 5000.0)
    eng.economy.set("water", 20000.0)
    eng.economy.set("limestone", 3000.0)
    for i in range(30):
        eng.units.add_unit(f"执行器-测试{i + 1}")

    def add_mine(sub, g, r):
        return build_and_assign(eng, ind,
                                spawn_claimed(eng, "ore", sub, g, r),
                                "extractor")

    # ---- 底座 ------------------------------------------------------
    build_and_assign(eng, ind, "HOME", "power_plant")
    spawn_claimed(eng, "empty")
    build_and_assign(eng, ind, spawn_claimed(eng, "empty"), "power_plant")
    add_mine("coal", 82, 200000)
    add_mine("coal", 82, 200000)
    add_mine("iron_ore", 58, 200000)
    add_mine("sulfur", 95, 50000)
    add_mine("copper_ore", 2.0, 50000)
    add_mine("petroleum", 70, 100000)
    add_mine("petroleum", 70, 100000)

    # ---- 完整工艺链爬升 + 全部固化 ---------------------------------
    # 主线（非 optional）：煤焦化→炼铁→炼钢→合成氨→硫酸→
    # 石油裂解→常压蒸馏→电解精铜。全部须永久固化才能烧录迁移。
    chain = [("db_coking", "cokery"), ("db_blast_furnace", "blast_furnace"),
             ("db_steel", "steel_mill"), ("db_ammonia", "ammonia_synth"),
             ("db_sulfuric", "sulfuric_plant")]
    for entry, fac in chain:
        recover_when_possible(eng, rec, entry, entry)
        fixate_when_possible(eng, rec, entry, f"fix:{entry}")
        build_when_possible(eng, ind, spawn_claimed(eng, "empty"), fac,
                            label=fac)

    # ---- 设备维护（db_upkeep 非可选，批次2b）----------------------
    # 恢复出"机械会磨损"这件事之后，所有设施开始消耗维护件；
    # 先建维护站把维护件产起来，否则设备状态下滑 → 故障停机。
    recover_when_possible(eng, rec, "db_upkeep", "db_upkeep")
    fixate_when_possible(eng, rec, "db_upkeep", "fix:db_upkeep")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "maintenance_depot", label="maintenance_depot")
    maint = eng.registry.get("maintenance")
    assert maint is not None and maint.enabled(eng), "维护体系应已启用"
    run_until(eng, lambda: eng.economy.get("maintenance_kit") > 5.0,
              max_s=1200, label="产维护件")
    print("  维护件产出:", round(eng.economy.get("maintenance_kit"), 2),
          "| 需求/s:", round(maint.demand_per_sec(eng), 3))

    # 电网扩容：裂解炉/蒸馏塔/电解精铜都是电老虎，玩家需加建电站。
    for _ in range(3):
        build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                            "power_plant", label="power_plant")

    tail = [("db_petrol", "petrol_cracker"),
            ("db_distill", "distill_tower"),
            ("db_copper", "copper_refiner")]
    for entry, fac in tail:
        recover_when_possible(eng, rec, entry, entry)
        fixate_when_possible(eng, rec, entry, f"fix:{entry}")
        build_when_possible(eng, ind, spawn_claimed(eng, "empty"), fac,
                            label=fac)

    # ---- 主线尾部：耐火冶金（db_refractory 非可选）-----------------
    # 镍矿→选矿机→电弧炉，证明稀有金属线在主线上可用
    add_mine("nickel_ore", 2.0, 100000)
    recover_when_possible(eng, rec, "db_refractory", "db_refractory")
    fixate_when_possible(eng, rec, "db_refractory", "fix:db_refractory")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "nickel_concentrator", label="nickel_conc")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "nickel_arc_furnace", label="nickel_arc")
    run_until(eng, lambda: eng.economy.get("nickel") > 2,
              max_s=1500, label="产镍")

    # ---- 主线尾部：稀土分离（db_rare_earth 非可选）-----------------
    # 独居石→选矿机→溶剂萃取(煤油/硫酸)，出铈证明主线可用
    add_mine("monazite", 6.0, 80000)
    recover_when_possible(eng, rec, "db_rare_earth", "db_rare_earth")
    fixate_when_possible(eng, rec, "db_rare_earth", "fix:db_rare_earth")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "rare_mill", label="rare_mill")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "solvent_extractor", label="solvent")
    run_until(eng, lambda: eng.economy.get("cerium") > 1,
              max_s=1500, label="产铈")

    # ---- 主线：调度知识（db_unit_bus / db_unit_parallel 非可选）------
    # 批次1 起"效率科技"纳入主线：单元效能提升产能与作业速度。
    for entry in ("db_unit_bus", "db_unit_parallel"):
        recover_when_possible(eng, rec, entry, entry)
        fixate_when_possible(eng, rec, entry, f"fix:{entry}")
    assert eng.units.efficiency >= 1.75, \
        f"主线调度知识应把效能推到 ≥1.75，当前 {eng.units.efficiency}"

    # ---- 主线：执行单元装配（db_units 非可选，P1 ②）------------------
    # 单元不再是固定资源：装配厂 + 主线固化奖励是本作后期扩张的正路。
    recover_when_possible(eng, rec, "db_units", "db_units")
    fixate_when_possible(eng, rec, "db_units", "fix:db_units")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "unit_factory", label="unit_factory")

    # ---- 副产物处置（批次3）：不处理会限产，拖垮整条链 ----------------
    # 放空塔：把焦炉煤气/煤焦油/高炉煤气/裂解干气按 1.5/s 销毁（材料浪费，
    # 但立刻解除限产）——这是"玩家必须给副产物找出路"的最小演示。
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "vent_tower", label="vent_tower")
    # 水煤气变换炉：把焦炉煤气转成氢，比放空更划算
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "gas_shift", label="gas_shift")
    run_until(eng, lambda: all(not v["over"] for v in
                               ind.backlog_status(eng).values()),
              max_s=1800, label="消化副产物积压")
    print("  副产物积压:", {k: (v["stock"], v["limit"])
                           for k, v in ind.backlog_status(eng).items()
                           if v["over"]} or "无")

    # 萃取线吃硫酸，先撤下单元让酸池攒给数据库工程（玩家取舍）
    for f in list(ind.facilities.values()):
        if f.def_id == "solvent_extractor":
            ind.unassign(eng, f.id)

    # 等化工产出起来（喂数据库工程）。耐心如玩家：等库存积累。
    run_until(eng, lambda: eng.economy.get("sulfuric_acid") > 60,
              max_s=1800, label="攒硫酸")
    run_until(eng, lambda: eng.economy.get("ammonia") > 40,
              max_s=1800, label="攒氨")
    run_until(eng, lambda: eng.economy.get("steel") > 220,
              max_s=1800, label="攒钢")

    # ---- 残骸回收供料（合金是建造与数据库工程的主要消耗）-------------
    def ensure_scrap(need=200.0, max_s=2400):
        """像玩家一样：残骸拆解供合金（这是本作合金的主要来源）。"""
        t = 0.0
        while eng.economy.get("scrap_alloy") < need and t < max_s:
            wreck_busy = any(
                ind.defs[f.def_id].get("extract_rate")
                and (lambda p: p is not None and p.kind == "wreck")(
                    eng.world.get(f.plot_id))
                for f in ind.facilities.values())
            if not wreck_busy:
                pid = spawn_claimed(eng, "wreck", "scrap_alloy", 60, 600)
                build_when_possible(eng, ind, pid, "salvager", label="salvager")
            eng.tick(0.5)
            t += 0.5
        return eng.economy.get("scrap_alloy")

    ensure_scrap(220.0)

    # 像玩家一样维护记忆：多次加固直到完整度回到安全区。
    # 单元不够时，像玩家一样"先撤掉最不关键的设施"腾一个出来
    # （放空塔/萃取线/冗余采矿机）。
    def free_one_unit():
        for defid in ("vent_tower", "gas_shift", "solvent_extractor",
                      "extractor"):
            for f in list(ind.facilities.values()):
                if f.def_id == defid and f.assigned:
                    ind.unassign(eng, f.id)
                    return True
        return False

    def top_up_mem():
        while mem.integrity < 70:
            base = mem.integrity
            first = mem.maintain(eng)
            if first is not None:
                print(f"   [诊断] maintain: {first} | 空闲单元 "
                      f"{eng.units.count_idle()}/{eng.units.count()} | "
                      f"残骸合金 {eng.economy.get('scrap_alloy'):.0f} | "
                      f"电力 {eng.economy.get('electricity'):.0f}")
            for _ in range(6):
                err = mem.maintain(eng)
                if err is None:
                    break
                if "空闲" in str(err) and free_one_unit():
                    continue                # 腾出单元后重试
                run_until(eng, lambda: mem.maintain(eng) is None,
                          max_s=120, label="等待加固可用")
                break
            # 等本次加固作业完成（完整度上升即作业结束）
            run_until(eng, lambda: mem.integrity > base + 1,
                      max_s=120, label="记忆加固")
    top_up_mem()

    # ---- 终局：五子系统逐项建造 ------------------------------------
    for p in db.projects:
        err = db.construct(eng)
        assert err is None, f"construct {p['id']} 失败: {err}"
        run_until(eng, lambda p=p: p["id"] in db.built, max_s=200,
                  label=f"竣工 {p['id']}")
    assert len(db.built) == len(db.projects)
    assert db.is_complete()
    assert mem.status_text().find("劣化已终止") >= 0

    # ---- 胜利后：劣化不再发生 --------------------------------------
    i0 = mem.integrity
    for _ in range(400):
        eng.tick(0.5)
    assert abs(mem.integrity - i0) < 1e-6, f"劣化未终止: {mem.integrity}"
    print(f"  [胜利] 可靠数据库竣工，记忆完整度锁定 {mem.integrity:.1f}%")
    print(f"  已固化条目: {sorted(e for e, s in rec.status.items() if s == 'permanent')}")
    win_logs = [l for l in eng.log_lines if "劣化永久终止" in l]
    assert win_logs
    print("M3-VICTORY-OK")


if __name__ == "__main__":
    main()
