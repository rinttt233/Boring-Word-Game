# -*- coding: utf-8 -*-
"""电力体系端到端：化学能分级(固/液白名单+效率规模) + 光热/光伏(昼夜)
+ 风电(事件增强) + 事件差异化削减(solar vs pv)。"""
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
dl = eng.registry.get("daylight")
result = {}

eng.economy.set("scrap_alloy", 3000.0)
eng.economy.set("steel", 800.0)
eng.economy.set("electricity", 5000.0)   # 起始电仓（各 elec_delta 前会归零）
eng.economy.set("coal", 100000.0)
eng.economy.set("coke", 100000.0)
eng.economy.set("gasoline", 100000.0)
eng.economy.set("copper", 500.0)
eng.economy.set("cable", 100.0)
eng.economy.set("polysilicon", 100.0)
for i in range(24):
    eng.units.add_unit(f"t{i + 1}")
# 前置知识：主线钢/铜/硅(可选深水区演示用)永久化
for eid in ("db_steel", "db_copper", "db_silicon"):
    rec.status[eid] = "permanent"
# 晴好基准事件
env.current_id = "fair"
env._until = 1e18


def spawn_empty():
    eng.world.spawn(ring=0, kind="empty", state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == "empty" and p.state == "claimed"][-1]


def build_when_possible(def_id, label):
    pid = spawn_empty()
    t = 0.0
    while t < 300:
        err = ind.build(eng, pid, def_id)
        if err is None:
            fid = [f.id for f in ind.facilities.values()
                   if f.plot_id == pid][-1]
            ind.assign(eng, fid)
            return fid
        eng.tick(0.5)
        t += 0.5
    raise AssertionError(f"建造超时 {label}: {err}")


def recover_fixate(eid):
    rec.status.setdefault(eid, "locked")
    t = 0.0
    while t < 300:
        err = rec.recover(eng, eid)
        if err is None:
            break
        eng.tick(0.5)
        t += 0.5
    else:
        raise AssertionError(f"recover {eid}: {err}")
    for _ in range(40):
        eng.tick(0.5)
    t = 0.0
    while t < 300:
        err = rec.fixate(eng, eid)
        if err is None:
            break
        eng.tick(0.5)
        t += 0.5
    for _ in range(40):
        eng.tick(0.5)
    assert rec.status[eid] == "permanent", f"{eid} 未固化"


def elec_delta(fac_id, seconds):
    """单设施产电增量：先停掉其它所有设施，只留目标机组。"""
    for f in list(ind.facilities.values()):
        if f.id != fac_id:
            ind.unassign(eng, f.id)
    target = ind.facilities.get(fac_id)
    if target is not None and not target.assigned:
        ind.assign(eng, fac_id)     # 重新分配目标机组
    eng.economy.set("electricity", 0.0)
    for _ in range(int(seconds * 2)):
        eng.tick(0.5)
    return eng.economy.get("electricity")


# ---- 1) 燃料类别白名单 ----------------------------------------------
recover_fixate("db_large_thermal")
f = build_when_possible("fluid_power_plant", "fluid_plant")
# 固体燃料(煤)应被拒绝
err = ind.set_fuel(eng, f, "coal")
print("set coal on fluid plant err:", err)
assert err is not None, "流体电站不应接受固体燃料"
# 液体(汽油)应成功
err = ind.set_fuel(eng, f, "gasoline")
assert err is None, f"汽油应可烧: {err}"
d1 = elec_delta(f, 10)
print("fluid plant 10s elec:", d1)
assert d1 > 5, "流体电站应产电"

fs = build_when_possible("coal_stove", "stove")
err = ind.set_fuel(eng, fs, "coal")
assert err is None, "小炉应可烧煤"
d2 = elec_delta(fs, 10)
print("coal stove 10s elec:", d2)
assert d2 > 1.5, "小固炉应产电"

# ---- 2) 分级效率/规模：大型固 > 小型固（同燃料） ------------------
fl = build_when_possible("large_coal_plant", "large_coal")
ind.set_fuel(eng, fl, "coal")
small_per_burn = elec_delta(fs, 10) / 10.0
large_per_burn = elec_delta(fl, 10) / 10.0
result["stove_per_s"] = small_per_burn
result["large_coal_per_s"] = large_per_burn
print("per-s stove vs large:", small_per_burn, large_per_burn)
assert large_per_burn > small_per_burn * 2, "大型固应显著更高产"

# ---- 3) 光热：白天产、夜晚停 ----------------------------------------
recover_fixate("db_csp")
fc = build_when_possible("csp_plant", "csp")
# 强制"白天"相位：把时钟拨到周期内白天起点
eng.clock.time = 5.0     # < day(32) → solar=1
d_day = elec_delta(fc, 10)
eng.clock.time = 45.0    # > 32+6 → solar=0(夜)
d_night = elec_delta(fc, 10)
result["csp_day"] = d_day
result["csp_night"] = d_night
print("csp day/night:", d_day, d_night)
assert d_day > 3, "光热白天应产电"
assert d_night < 0.1, "光热夜里应停机"

# ---- 4) 沙暴：光热大削、光伏更惨、风电增强 -------------------------
env.current_id = "dust_storm"
eng.clock.time = 5.0
d_csp_dust = elec_delta(fc, 10)
# 风电
eng.economy.set("electricity", 5000.0)   # 补电仓供后续 recover/测量
recover_fixate("db_wind")
fw = build_when_possible("wind_farm", "wind")
eng.clock.time = 45.0   # 夜(风电应不受昼夜影响)
env.current_id = "fair"
d_wind_fair_night = elec_delta(fw, 10)
env.current_id = "dust_storm"
d_wind_dust = elec_delta(fw, 10)
result["wind_fair_night"] = d_wind_fair_night
result["wind_dust"] = d_wind_dust
print("wind fair-night/dust:", d_wind_fair_night, d_wind_dust)
assert d_wind_fair_night > 3, "风电夜间应持续产电"
assert d_wind_dust > d_wind_fair_night * 1.2, "沙暴应增强风电"
assert d_csp_dust < d_day * 0.4, f"沙暴应大削光热: {d_csp_dust} vs {d_day}"

# 光伏对比：晴天出力高于光热，但沙暴下几乎归零(削减远比光热狠)
eng.economy.set("electricity", 5000.0)
recover_fixate("db_pv")
fpv = build_when_possible("solar_farm", "pv")
env.current_id = "fair"
eng.clock.time = 5.0
d_pv_fair = elec_delta(fpv, 10)
env.current_id = "dust_storm"
d_pv_dust = elec_delta(fpv, 10)
result["pv_fair"] = d_pv_fair
result["pv_dust"] = d_pv_dust
print("pv fair/dust:", d_pv_fair, d_pv_dust)
assert d_pv_fair > d_day, \
    f"晴天光伏出力应高于光热: pv={d_pv_fair} csp={d_day}"
assert d_pv_dust < d_pv_fair * 0.1, \
    f"沙暴应几乎归零光伏: {d_pv_dust} vs {d_pv_fair}"
# 光热沙暴余 0.3 → 应明显高于光伏的 0.05 倍率
assert d_csp_dust > d_pv_dust * 3, \
    f"沙暴下光热应显著高于光伏: csp={d_csp_dust} pv={d_pv_dust}"

# ---- 批次4：电网上限 / 优先级降载 / 储能充放（真实路径，不旁路）----
eng.registry.get("power").base_capacity = 400.0
eng.economy.set("electricity", 399.0)
env.current_id = "fair"
eng.clock.time = 5.0
# 电网已满 → 燃煤电站降载（不白烧燃料）
coal0 = eng.economy.get("coal")
eng.tick(1.0)
grid_fac = [f for f in ind.facilities.values()
            if f.def_id in ("power_plant", "coal_stove", "csp_plant",
                            "solar_farm", "pv", "wind_farm")
            and f.stall_code == "grid_full"]
assert grid_fac, "电网已满时应报 grid_full"
assert abs(eng.economy.get("coal") - coal0) < 0.5, "电网已满时不该白烧燃料"
result["grid_full_ok"] = True
# 储能：电网 90% → 充电；电网 10% → 放电
batt_id = build_when_possible("battery_bank", "battery")   # db_copper 上面恢复过
batt = ind.facilities[batt_id]
eng.economy.set("electricity", 380.0)
eng.tick(1.0)
chg = batt.stored
assert chg > 0, "电网高位时电池应充电"
eng.economy.set("electricity", 40.0)
eng.tick(1.0)
assert batt.stored < chg, "电网低位时电池应放电"
result["storage_charge"] = round(chg, 2)
result["storage_after_discharge"] = round(batt.stored, 2)
# 优先级：把电池改成 0 档、电站 2 档，缺电时低档先停
assert ind.defs["battery_bank"].get("power_priority") == 1
assert ind.defs["power_plant"].get("power_priority") == 2
assert ind.defs["vent_tower"].get("power_priority") == 0
result["priority_ok"] = True

print("POWER-ENDPOINT-OK", result)
