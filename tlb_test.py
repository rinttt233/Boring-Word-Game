# -*- coding: utf-8 -*-
"""TL-B 端到端：蒸馏多馏分 → 聚合聚乙烯 + 副产物生态衔接。"""
import sys
import json

sys.path.insert(0, ".")

from main import build_engine

eng = build_engine()
eng.clock.resume()
ind = eng.registry.get("industry")
ind.instant_build = True     # 端点脚本聚焦产线数值；建造耗时见 tests/test_build_time.py
rec = eng.registry.get("recovery")
result = {}

eng.economy.set("scrap_alloy", 1000.0)
eng.economy.set("steel", 500.0)
eng.economy.set("petroleum", 2000.0)
eng.economy.set("electricity", 8000.0)
for i in range(5):
    eng.units.add_unit(f"t{i + 1}")
# 解锁前置：petrol(裂解→乙烯/汽油) 是 db_distill/db_polymer 的依赖
rec.status["db_petrol"] = "permanent"


def spawn_empty():
    eng.world.spawn(ring=0, kind="empty", state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == "empty" and p.state == "claimed"][-1]


def recover_fixate(eid):
    rec.status.setdefault(eid, "locked")
    err = rec.recover(eng, eid)
    assert err is None, f"recover {eid}: {err}"
    for _ in range(80):
        eng.tick(0.5)
    rec.fixate(eng, eid)
    for _ in range(40):
        eng.tick(0.5)
    assert rec.status[eid] == "permanent", f"{eid} 未固化"


# 1) db_distill → 蒸馏塔多馏分
recover_fixate("db_distill")
pid = spawn_empty()
ind.build(eng, pid, "distill_tower")
ind.assign(eng, "F1")
for _ in range(300):
    eng.tick(0.5)
for s in ("gasoline", "kerosene", "diesel", "fuel_oil"):
    result[s] = eng.economy.get(s)
assert eng.economy.get("gasoline") > 5, "应产汽油"
assert eng.economy.get("kerosene") > 3, "应产煤油"
assert eng.economy.get("diesel") > 5, "应产柴油"
assert eng.economy.get("fuel_oil") > 5, "应产重油"

# 2) 重油可烧（衔接 TL-0 burner）：建燃气发电机烧 fuel_oil
eng.world.spawn(ring=0, kind="empty", state="claimed")
pid2 = [p.id for p in eng.world.plots.values()
        if p.kind == "empty" and p.state == "claimed"][-1]
ind.build(eng, pid2, "gas_generator")
ind.assign(eng, "F2")
ind.set_fuel(eng, "F2", "fuel_oil")
e0 = eng.economy.get("electricity")
for _ in range(60):
    eng.tick(0.5)
result["elec_from_fueloil"] = eng.economy.get("electricity") - e0
assert eng.economy.get("electricity") - e0 > 20, "重油应可发电"

# 3) 先建裂解炉产乙烯，再恢复 db_polymer → 聚乙烯
eng.world.spawn(ring=0, kind="empty", state="claimed")
pid3 = [p.id for p in eng.world.plots.values()
        if p.kind == "empty" and p.state == "claimed"][-1]
ind.build(eng, pid3, "petrol_cracker")   # 产乙烯+汽油
ind.assign(eng, "F3")
# 攒乙烯到 db_polymer 恢复所需(12t)
for _ in range(600):
    eng.tick(0.5)
result["ethylene"] = eng.economy.get("ethylene")
assert eng.economy.get("ethylene") > 12, f"乙烯不足 {eng.economy.get('ethylene')}"
recover_fixate("db_polymer")
eng.world.spawn(ring=0, kind="empty", state="claimed")
pid4 = [p.id for p in eng.world.plots.values()
        if p.kind == "empty" and p.state == "claimed"][-1]
ind.build(eng, pid4, "polymer_plant")
ind.assign(eng, "F4")
for _ in range(300):
    eng.tick(0.5)
result["polyethyl"] = eng.economy.get("polyethyl")
assert eng.economy.get("polyethyl") > 5, "应产聚乙烯"

print("TLB-ENDPOINT-OK", result)
