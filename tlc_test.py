# -*- coding: utf-8 -*-
"""TL-C 端到端：镍/铬/钨 → 选矿 → 电弧炉 → 金属 → 不锈钢/高温合金 + 升级氨塔。"""
import sys

sys.path.insert(0, ".")

from main import build_engine

eng = build_engine()
eng.clock.resume()
ind = eng.registry.get("industry")
ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
rec = eng.registry.get("recovery")
env = eng.registry.get("environment")
if env is not None:
    # 锁定晴天：升级氨塔对照需确定速率
    env.current_id = "fair"
    env._until = 1e18
result = {}

eng.economy.set("scrap_alloy", 3000.0)
eng.economy.set("steel", 600.0)
eng.economy.set("electricity", 20000.0)
eng.economy.set("coal", 20000.0)
eng.economy.set("water", 20000.0)
for i in range(24):
    eng.units.add_unit(f"t{i + 1}")
# 前置知识：本链依赖 db_steel/db_coking(炼焦供焦炭)/db_ammonia(对照塔)
rec.status["db_steel"] = "permanent"
rec.status["db_coking"] = "permanent"
rec.status["db_ammonia"] = "permanent"


def spawn_empty():
    eng.world.spawn(ring=0, kind="empty", state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == "empty" and p.state == "claimed"][-1]


def spawn_ore(sub, g, r):
    eng.world.spawn(ring=0, kind="ore", substance=sub,
                    grade=g, reserve=r, state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == "ore" and p.substance == sub
            and p.state == "claimed"][-1]


def build_when_possible(def_id, label):
    pid = spawn_empty()
    t = 0.0
    while t < 600:
        err = ind.build(eng, pid, def_id)
        if err is None:
            fid = [f.id for f in ind.facilities.values()
                   if f.plot_id == pid][-1]
            ind.assign(eng, fid)
            return fid
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"建造超时 {label}: {err}")


def mine(sub, g=50, r=200000):
    pid = spawn_ore(sub, g, r)
    ind.build(eng, pid, "extractor")
    fid = [f.id for f in ind.facilities.values()
           if f.plot_id == pid][-1]
    ind.assign(eng, fid)
    return fid


def recover_fixate(eid):
    rec.status.setdefault(eid, "locked")
    t = 0.0
    while t < 600:
        err = rec.recover(eng, eid)
        if err is None:
            break
        eng.tick(0.5)
        t += 0.5
    else:
        raise AssertionError(f"recover {eid}: {err}")
    for _ in range(80):
        eng.tick(0.5)
    t = 0.0
    while t < 600:
        err = rec.fixate(eng, eid)
        if err is None:
            break
        eng.tick(0.5)
        t += 0.5
    else:
        raise AssertionError(f"fixate {eid}: {err}")
    for _ in range(60):
        eng.tick(0.5)
    assert rec.status[eid] == "permanent", f"{eid} 未固化"


# ---- 底座：电厂 x6(电弧炉电老虎) + 煤/焦 + 三矿 ----
for _ in range(6):
    build_when_possible("power_plant", "power")
mine("coal", 82)
mine("coal", 82)
build_when_possible("cokery", "cokery")
mine("nickel_ore", 2.0)
mine("chrome_ore", 30)
mine("tungsten_ore", 1.5)

# ---- 1) db_refractory → 选矿机 → 电弧炉 ----
recover_fixate("db_refractory")
for _ in range(80):
    eng.tick(0.5)
build_when_possible("nickel_concentrator", "nickel_conc")
build_when_possible("nickel_arc_furnace", "nickel_arc")
build_when_possible("chrome_concentrator", "chrome_conc")
build_when_possible("chrome_arc_furnace", "chrome_arc")
build_when_possible("tungsten_concentrator", "tungsten_conc")
build_when_possible("tungsten_arc_furnace", "tungsten_arc")
for _ in range(1200):
    eng.tick(0.5)
result["nickel"] = eng.economy.get("nickel")
result["chrome"] = eng.economy.get("chrome")
result["tungsten"] = eng.economy.get("tungsten")
assert eng.economy.get("nickel") > 1.5, f"镍不足 {eng.economy.get('nickel')}"
assert eng.economy.get("chrome") > 1.0, f"铬不足 {eng.economy.get('chrome')}"
assert eng.economy.get("tungsten") > 1.0, f"钨不足 {eng.economy.get('tungsten')}"

# ---- 2) db_alloy → 不锈钢 + 高温合金 ----
recover_fixate("db_alloy")
build_when_possible("stainless_plant", "stainless")
build_when_possible("superalloy_plant", "superalloy")
for _ in range(800):
    eng.tick(0.5)
result["stainless"] = eng.economy.get("stainless")
result["superalloy"] = eng.economy.get("superalloy")
assert eng.economy.get("stainless") > 1.0, "不锈钢不足"
assert eng.economy.get("superalloy") > 1.0, "高温合金不足"

# ---- 3) 升级氨塔对照：同一氨库存池，分别只开一台塔测产 ----
eng.economy.set("ammonia", 0.0)
old_fid = build_when_possible("ammonia_synth", "ammonia_old")
for _ in range(100):
    eng.tick(0.5)
old_amm = eng.economy.get("ammonia")
ind.unassign(eng, old_fid)
eng.economy.set("ammonia", 0.0)
build_when_possible("ammonia_v2_plant", "ammonia_v2")
for _ in range(100):
    eng.tick(0.5)
v2_amm = eng.economy.get("ammonia")
result["ammonia_old"] = old_amm
result["ammonia_v2"] = v2_amm
assert v2_amm > old_amm, f"升级塔应更高产: v2={v2_amm} old={old_amm}"

print("TLC-ENDPOINT-OK", result)
