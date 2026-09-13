# -*- coding: utf-8 -*-
"""TL-D 端到端：独居石→选矿→溶剂萃取→铈/镧/钕→催化剂→升级设施(硫酸/催化裂化)。"""
import sys

sys.path.insert(0, ".")

from main import build_engine

eng = build_engine()
eng.registry.get("power").install_unbounded()   # 端点聚焦产线数值：电网上限与本测试无关（批次4）
eng.clock.resume()
ind = eng.registry.get("industry")
ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
rec = eng.registry.get("recovery")
env = eng.registry.get("environment")
if env is not None:
    # 锁定晴天：对照实验需确定速率，排除沙暴/电离风暴随机扰动
    env.current_id = "fair"
    env._until = 1e18
result = {}

eng.economy.set("scrap_alloy", 3000.0)
eng.economy.set("steel", 800.0)
eng.economy.set("electricity", 30000.0)
eng.economy.set("coal", 20000.0)
eng.economy.set("water", 30000.0)
for i in range(30):
    eng.units.add_unit(f"t{i + 1}")
# 前置知识（上游阶段已固化）：钢→硫酸、石油裂解/蒸馏、耐火冶金、炼焦
for eid in ("db_steel", "db_coking", "db_sulfuric", "db_petrol",
            "db_distill", "db_refractory"):
    rec.status[eid] = "permanent"


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


def mine(sub, g=50, r=300000):
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
    for _ in range(100):
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


# ---- 底座：电 + 焦化(供铬还原) + 硫磺(硫酸) + 石油(蒸馏煤油) + 铬(副产钒) + 独居石 ----
for _ in range(7):
    build_when_possible("power_plant", "power")
mine("coal", 82)
mine("coal", 82)
build_when_possible("cokery", "cokery")
mine("sulfur", 95)
build_when_possible("sulfuric_plant", "sulfuric")
mine("petroleum", 70)
build_when_possible("distill_tower", "distill")
mine("chrome_ore", 30)
build_when_possible("chrome_concentrator", "chrome_conc")
build_when_possible("chrome_arc_furnace", "chrome_arc")
mine("monazite", 6)
# 让硫酸/煤油/钒先攒起来
for _ in range(1500):
    eng.tick(0.5)
result["sulfuric_acid"] = eng.economy.get("sulfuric_acid")
result["kerosene"] = eng.economy.get("kerosene")
result["vanadium"] = eng.economy.get("vanadium")
assert eng.economy.get("sulfuric_acid") > 30, "前置硫酸不足"
assert eng.economy.get("kerosene") > 10, "前置煤油不足"
assert eng.economy.get("vanadium") > 5, "钒(铬副产)不足"

# ---- 1) db_rare_earth → 选矿 + 溶剂萃取 ----
recover_fixate("db_rare_earth")
build_when_possible("rare_mill", "rare_mill")
build_when_possible("solvent_extractor", "solvent")
for _ in range(1200):
    eng.tick(0.5)
result["cerium"] = eng.economy.get("cerium")
result["lanthanum"] = eng.economy.get("lanthanum")
result["neodymium"] = eng.economy.get("neodymium")
assert eng.economy.get("cerium") > 3, "铈不足"
assert eng.economy.get("lanthanum") > 2, "镧不足"
assert eng.economy.get("neodymium") > 1.5, "钕不足"

# ---- 2) db_rare_catalyst → 两条催化剂线 ----
recover_fixate("db_rare_catalyst")
build_when_possible("catalyst_v_line", "cat_v")
build_when_possible("catalyst_cc_line", "cat_cc")
for _ in range(600):
    eng.tick(0.5)
result["catalyst_v"] = eng.economy.get("catalyst_v")
result["catalyst_cc"] = eng.economy.get("catalyst_cc")
assert eng.economy.get("catalyst_v") > 1.5, "钒催化剂不足"
assert eng.economy.get("catalyst_cc") > 1.5, "分子筛催化剂不足"

# ---- 3) 升级设施对照：硫酸 v2 / 裂化 v2 ----
# 确保催化剂/酸源充足，排除建塔等待与原料瓶颈
eng.economy.set("catalyst_v", 20.0)
eng.economy.set("catalyst_cc", 20.0)
eng.economy.set("sulfur", 2000.0)
# 彻底停掉所有硫酸塔与萃取线，避免并发干扰对照
for f in list(ind.facilities.values()):
    if f.def_id in ("sulfuric_plant", "sulfuric_v2_plant",
                    "solvent_extractor", "petrol_cracker",
                    "cracker_v2_plant"):
        ind.unassign(eng, f.id)

def measure_acid(fac_def):
    eng.economy.set("sulfuric_acid", 0.0)
    build_when_possible(fac_def, fac_def)
    for _ in range(100):
        eng.tick(0.5)
    return eng.economy.get("sulfuric_acid")

old_acid = measure_acid("sulfuric_plant")
for f in list(ind.facilities.values()):
    if f.def_id == "sulfuric_plant":
        ind.unassign(eng, f.id)
v2_acid = measure_acid("sulfuric_v2_plant")
result["acid_old_50s"] = old_acid
result["acid_v2_50s"] = v2_acid
assert v2_acid > old_acid * 1.1, f"硫酸升级应更高产: v2={v2_acid} old={old_acid}"

def measure_crack(fac_def):
    eng.economy.set("petroleum", 3000.0)
    eng.economy.set("gasoline", 0.0)
    eng.economy.set("ethylene", 0.0)
    build_when_possible(fac_def, fac_def)
    for _ in range(100):
        eng.tick(0.5)
    return eng.economy.get("gasoline"), eng.economy.get("ethylene")

for f in list(ind.facilities.values()):
    if f.def_id == "sulfuric_v2_plant":
        ind.unassign(eng, f.id)
old_gas, old_eth = measure_crack("petrol_cracker")
for f in list(ind.facilities.values()):
    if f.def_id == "petrol_cracker":
        ind.unassign(eng, f.id)
v2_gas, v2_eth = measure_crack("cracker_v2_plant")
result["crack_old_50s"] = (old_gas, old_eth)
result["crack_v2_50s"] = (v2_gas, v2_eth)
assert v2_gas > old_gas * 1.05, f"裂化汽油应更高: v2={v2_gas} old={old_gas}"
assert v2_eth > old_eth * 1.05, f"裂化乙烯应更高: v2={v2_eth} old={old_eth}"

print("TLD-ENDPOINT-OK", result)
