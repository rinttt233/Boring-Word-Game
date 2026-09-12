# -*- coding: utf-8 -*-
"""TL-E 端到端：石英砂→工业硅→三氯氢硅→9N 多晶硅(西门子法)。
耦合：氯化氢由氯碱(氯+氢)合成；需 db_refractory(电弧炉) 与 db_chloralkali(氯碱) 前置。"""
import sys

sys.path.insert(0, ".")

from main import build_engine

eng = build_engine()
eng.clock.resume()
ind = eng.registry.get("industry")
ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
rec = eng.registry.get("recovery")
result = {}

eng.economy.set("scrap_alloy", 3000.0)
eng.economy.set("steel", 800.0)
eng.economy.set("electricity", 30000.0)
eng.economy.set("coal", 20000.0)
eng.economy.set("water", 30000.0)
eng.economy.set("salt", 5000.0)
eng.economy.set("stainless", 100.0)   # 硅炉/还原炉耐蚀容器(上游可选合金线已产)
for i in range(30):
    eng.units.add_unit(f"t{i + 1}")
# 前置：电弧炉(dv_refractory 上游)、炼焦供焦炭
for eid in ("db_refractory", "db_coking"):
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


# ---- 底座：电 + 煤(焦炭) + 盐(氯碱) + 石英砂 ----
for _ in range(6):
    build_when_possible("power_plant", "power")
mine("coal", 82)
build_when_possible("cokery", "cokery")
mine("salt", 90)
mine("silica", 88)

# ---- 0) 氯碱(上游可选) 产氯+氢 → 氯化氢源 ----
recover_fixate("db_chloralkali")
build_when_possible("chloralkali_cell", "chloralkali")
for _ in range(900):
    eng.tick(0.5)
result["chlorine"] = eng.economy.get("chlorine")
result["hydrogen"] = eng.economy.get("hydrogen")
assert eng.economy.get("chlorine") > 10, "氯不足"
assert eng.economy.get("hydrogen") > 3, "氢不足"

# ---- 1) db_silicon → 工业硅 ----
recover_fixate("db_silicon")
build_when_possible("silicon_furnace", "silicon_furnace")
for _ in range(900):
    eng.tick(0.5)
result["silicon"] = eng.economy.get("silicon")
assert eng.economy.get("silicon") > 3, "工业硅不足"

# ---- 2) 氯化氢塔 + 三氯氢硅 ----
build_when_possible("hcl_tower", "hcl_tower")
build_when_possible("tcs_reactor", "tcs_reactor")
for _ in range(900):
    eng.tick(0.5)
result["hcl"] = eng.economy.get("hcl")
result["tcs"] = eng.economy.get("tcs")
assert eng.economy.get("tcs") > 3, "三氯氢硅不足"

# ---- 3) 西门子还原炉 → 多晶硅 ----
build_when_possible("silicon_plant", "silicon_plant")
for _ in range(1500):
    eng.tick(0.5)
result["polysilicon"] = eng.economy.get("polysilicon")
assert eng.economy.get("polysilicon") > 1.0, f"多晶硅不足 {eng.economy.get('polysilicon')}"

print("TLE-ENDPOINT-OK", result)
