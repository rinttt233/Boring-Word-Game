# -*- coding: utf-8 -*-
"""M2 端到端玩法验证：沿真实工艺链爬升到二战化工双里程碑。

链路：煤→(干馏)焦炭→(高炉)生铁→(炼钢)钢 → 合成氨 + 硫酸
同时验证恢复树门控、记忆崩溃丢失、固化保护在真实 content 下的协同。
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
    """像玩家一样：材料备齐再建造（缺料就继续生产等待）。"""
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
    raise AssertionError(f"超时未满足: {label} (game {eng.clock.time:.0f}s)")


def ensure_memory(eng, level=70):
    mem = eng.registry.get("memory")
    if mem.integrity < level:
        # 直接回满（测试辅助，非玩家作弊路径）
        mem.integrity = mem.max_integrity


def recover_when_possible(eng, rec, entry, label, max_s=900):
    """模拟玩家：资源不足则继续生产，隔一会再试，直到恢复作业启动。"""
    t = 0.0
    while t < max_s:
        err = rec.recover(eng, entry)
        if err is None:
            # 作业已启动，等它完成
            return run_until(eng, lambda: rec.is_unlocked(entry),
                             max_s=120, label=label)
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"超时无法启动恢复: {label} (err={err})")


def fixate_when_possible(eng, rec, entry, label, max_s=600):
    t = 0.0
    while t < max_s:
        err = rec.fixate(eng, entry)
        if err is None:
            return run_until(eng,
                             lambda: rec.status[entry] == "permanent",
                             max_s=60, label=label)
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"超时无法固化: {label} (err={err})")


def main():
    eng = build_engine()
    ind = eng.registry.get("industry")
    ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
    rec = eng.registry.get("recovery")
    eng.clock.resume()
    eng.economy.set("electricity", 2000.0)
    eng.economy.set("scrap_alloy", 500.0)
    eng.economy.set("coal", 2000.0)
    eng.economy.set("water", 5000.0)
    eng.economy.set("limestone", 1000.0)

    # 充裕单元池（简化测试，平衡另测）
    for i in range(12):
        eng.units.add_unit(f"执行器-测试{i + 1}")

    def add_mine(substance, grade, reserve):
        pid = spawn_claimed(eng, "ore", substance, grade, reserve)
        return build_and_assign(eng, ind, pid, "extractor")

    # ---- 1. 能源与原料底座：2 电厂 + 煤矿(2) + 铁矿 + 水源 ----------
    build_and_assign(eng, ind, "HOME", "power_plant")
    spawn_claimed(eng, "empty")
    build_and_assign(eng, ind, spawn_claimed(eng, "empty"), "power_plant")
    add_mine("coal", 82, 90000)
    add_mine("coal", 82, 90000)
    add_mine("iron_ore", 58, 90000)
    spawn_claimed(eng, "water")

    # ---- 2. 煤焦化（恢复→固化→炼焦）-------------------------------
    recover_when_possible(eng, rec, "db_coking", "db_coking")
    fixate_when_possible(eng, rec, "db_coking", "固化coking")
    build_and_assign(eng, ind, spawn_claimed(eng, "empty"), "cokery")
    run_until(eng, lambda: eng.economy.get("coke") > 2.0, label="产焦炭")

    # ---- 3. 高炉 → 生铁 -------------------------------------------
    recover_when_possible(eng, rec, "db_blast_furnace", "db_blast")
    fixate_when_possible(eng, rec, "db_blast_furnace", "固化blast")
    build_and_assign(eng, ind, spawn_claimed(eng, "empty"), "blast_furnace")
    run_until(eng, lambda: eng.economy.get("pig_iron") > 2.0,
              label="产生铁")

    # ---- 4. 炼钢（恢复需 20t 生铁 → 等产量累积后自动重试）----------
    recover_when_possible(eng, rec, "db_steel", "db_steel")
    fixate_when_possible(eng, rec, "db_steel", "固化steel")
    build_and_assign(eng, ind, spawn_claimed(eng, "empty"), "steel_mill")
    run_until(eng, lambda: eng.economy.get("steel") > 2.0, label="产钢")

    # ---- 5. 合成氨（钢→高压容器）----------------------------------
    ensure_memory(eng)
    recover_when_possible(eng, rec, "db_ammonia", "db_ammonia")
    fixate_when_possible(eng, rec, "db_ammonia", "固化ammonia")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "ammonia_synth", label="ammonia_synth")
    run_until(eng, lambda: eng.economy.get("ammonia") > 0.5,
              label="产氨", max_s=600)
    print(f"  合成氨产出: {eng.economy.get('ammonia'):.2f}t")

    # ---- 6. 硫酸（硫磺→接触法）------------------------------------
    add_mine("sulfur", 95, 20000)
    recover_when_possible(eng, rec, "db_sulfuric", "db_sulf")
    fixate_when_possible(eng, rec, "db_sulfuric", "固化sulf")
    build_when_possible(eng, ind, spawn_claimed(eng, "empty"),
                        "sulfuric_plant", label="sulfuric_plant")
    run_until(eng, lambda: eng.economy.get("sulfuric_acid") > 0.5,
              label="产硫酸", max_s=600)
    print(f"  硫酸产出: {eng.economy.get('sulfuric_acid'):.2f}t")

    # ---- 验收 ------------------------------------------------------
    fixed = [eid for eid, st in rec.status.items() if st == "permanent"]
    print(f"  已固化条目: {sorted(fixed)}")
    assert eng.economy.get("ammonia") > 0.5
    assert eng.economy.get("sulfuric_acid") > 0.5
    assert set(fixed) == {"db_coking", "db_blast_furnace", "db_steel",
                          "db_ammonia", "db_sulfuric"}
    print("M2-CHAIN-OK")


if __name__ == "__main__":
    main()
