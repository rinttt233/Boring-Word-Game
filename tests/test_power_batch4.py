# -*- coding: utf-8 -*-
"""批次4 电力体系测试：电网容量 / 自放电 / 优先级降载 / 储能充放。

另含 A12（开局煤 120 + 圈1 首探保底出煤矿）与 A14（开局 4 个执行单元）的验收。
"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.engine import Engine                        # noqa: E402
from systems.industry import IndustrySystem           # noqa: E402
from systems.power import PowerSystem                 # noqa: E402
from systems.recovery import RecoverySystem           # noqa: E402
from systems.survey import SurveySystem               # noqa: E402

POWER_CFG = {
    "base_capacity": 200.0, "self_discharge_per_sec": 0.05,
    "priority_default": 1, "priority_names": {"0": "可选", "1": "生产", "2": "关键"},
    "charge_high_ratio": 0.75, "charge_low_ratio": 0.25,
    "grid_full_ratio": 0.02,
    "timeline_seconds": 10.0, "timeline_samples": 5,
}
# 分配类用例关掉自放电，才能精确算"预算"（自放电见 TestGridCapacity）
NO_LEAK = dict(POWER_CFG, self_discharge_per_sec=0.0)
FACS = [
    {"id": "plant", "name": "电站", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "burn_coal", "slots": 1, "power_priority": 2},
    {"id": "mine", "name": "采矿机", "allowed_plot_kinds": ["ore"],
     "build_cost": {}, "extract_rate": 0.8, "power_use": 0.1, "slots": 1,
     "power_priority": 2},
    {"id": "vent", "name": "放空塔", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "vent", "vent_substances": ["coalgas"],
     "vent_rate": 1.0, "power_use": 0.2, "slots": 1, "power_priority": 0},
    {"id": "batt", "name": "电池组", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "storage", "capacity": 100.0, "max_rate": 2.0,
     "charge_efficiency": 0.9, "slots": 1, "power_priority": 1},
    {"id": "plain", "name": "普通厂", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "use_power", "slots": 1},   # 未标优先级 → 默认 1
]
RECIPES = [
    {"id": "burn_coal", "name": "烧煤", "inputs": {"coal": 0.4},
     "outputs": {"electricity": 1.6}},
    {"id": "use_power", "name": "耗电", "inputs": {"electricity": 0.5},
     "outputs": {"coke": 0.1}},
]
HEAT = {"coal": 24.0}


def make_engine(power=True, elec=0.0, coal=1000.0, plots=None, cfg=None):
    eng = Engine()
    eng.economy.set("coal", coal)
    eng.economy.set("electricity", elec)
    for n in ("甲", "乙", "丙", "丁"):
        eng.units.add_unit("执行器-" + n)
    eng.bootstrap_world(plots or [
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "F1", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "F2", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "MINE", "ring": 0, "kind": "ore", "substance": "iron_ore",
         "grade": 51.0, "reserve": 9999.0, "state": "claimed"},
    ])
    eng.registry.register("recovery", RecoverySystem([]))
    if power:
        eng.registry.register("power", PowerSystem(cfg or POWER_CFG))
    ind = IndustrySystem(FACS, RECIPES, heat_values=HEAT,
                         ref_grades={"iron_ore": 51.0})
    ind.instant_build = True
    eng.registry.register("industry", ind)
    eng.start()
    eng.clock.resume()
    return eng, ind


def add(eng, ind, plot_id, def_id, assign=True):
    err = ind.install_instant(eng, plot_id, def_id)
    assert err is None, f"建成失败 {def_id}@{plot_id}: {err}"
    f = next(x for x in ind.facilities.values() if x.plot_id == plot_id)
    if assign:
        ind.assign(eng, f.id)
    return f


def ticks(eng, seconds, step=0.25):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestGridCapacity(unittest.TestCase):
    def test_capacity_from_config(self):
        eng, _ = make_engine()
        p = eng.registry.get("power")
        self.assertEqual(p.capacity(eng), 200.0)
        self.assertEqual(p.headroom(eng), 200.0)

    def test_self_discharge(self):
        eng, _ = make_engine(elec=100.0)
        p = eng.registry.get("power")
        ticks(eng, 100.0, step=1.0)
        self.assertAlmostEqual(p.stored(eng), 95.0, delta=0.5)
        self.assertAlmostEqual(p.total_self_discharged, 5.0, delta=0.5)

    def test_producer_curtailed_when_grid_full(self):
        """电网已满：发电设施降载且**不白烧燃料**（A13）。"""
        eng, ind = make_engine(elec=199.99)
        add(eng, ind, "HOME", "plant")
        coal0 = eng.economy.get("coal")
        eng.tick(1.0)
        f = next(x for x in ind.facilities.values() if x.plot_id == "HOME")
        self.assertEqual(f.stall_code, "grid_full")
        self.assertAlmostEqual(eng.economy.get("coal"), coal0, delta=0.05)
        self.assertLessEqual(eng.economy.get("electricity"), 200.0 + 1e-6)

    def test_storage_not_counted_as_grid_capacity(self):
        eng, ind = make_engine()
        p = eng.registry.get("power")
        add(eng, ind, "F1", "batt")
        self.assertEqual(p.capacity(eng), 200.0)
        self.assertEqual(p.storage_capacity(eng), 100.0)


class TestPriorityShedding(unittest.TestCase):
    def test_low_priority_sheds_first(self):
        """存量刚好够高优先级：档2 满供、档0 直接停（不再全厂一起半速）。"""
        eng, ind = make_engine(elec=0.1, cfg=NO_LEAK)
        add(eng, ind, "MINE", "mine")            # 档2，0.1/s
        add(eng, ind, "F1", "vent")              # 档0，0.2/s
        p = eng.registry.get("power")
        p.tick(eng, 1.0)
        self.assertAlmostEqual(p.factor(2), 1.0, delta=0.01)
        self.assertEqual(p.factor(0), 0.0)
        self.assertIn(0, p.shed_tiers())

    def test_priority_default_is_middle(self):
        p = PowerSystem(POWER_CFG)
        self.assertEqual(p.priority_of({}), 1)
        self.assertEqual(p.priority_of({"power_priority": 0}), 0)
        self.assertEqual(p.tier_name(2), "关键")

    def test_shed_facility_stalls_with_shed_code(self):
        eng, ind = make_engine(elec=0.1)
        add(eng, ind, "MINE", "mine")
        v = add(eng, ind, "F1", "vent")
        eng.economy.set("coalgas", 50.0)
        eng.tick(1.0)
        self.assertEqual(v.stall_code, "shed")
        self.assertIn("降载", v.stall_reason or "")

    def test_consume_without_power_system_is_unchanged(self):
        """没注册 power 模块时行为与旧版一致（向后兼容）。"""
        eng, ind = make_engine(power=False, elec=10.0)
        add(eng, ind, "MINE", "mine")
        eng.tick(1.0)
        self.assertGreater(eng.economy.get("iron_ore"), 0.0)

    def test_partial_tier_when_budget_tight(self):
        eng, ind = make_engine(elec=0.05, cfg=NO_LEAK)
        add(eng, ind, "MINE", "mine")
        p = eng.registry.get("power")
        p.tick(eng, 1.0)
        self.assertGreater(p.factor(2), 0.0)
        self.assertLess(p.factor(2), 1.0)


class TestStorage(unittest.TestCase):
    def test_charge_from_surplus(self):
        eng, ind = make_engine(elec=180.0)      # > 75% 容量 → 充电
        b = add(eng, ind, "F1", "batt")
        p = eng.registry.get("power")
        p.tick(eng, 1.0)
        # 受 max_rate 2.0/s 限制，且乘充电效率 0.9
        self.assertAlmostEqual(b.stored, 1.8, delta=0.05)
        self.assertAlmostEqual(eng.economy.get("electricity"), 178.0, delta=0.1)
        self.assertEqual(p.status(eng)["storage"][0]["state"], "charging")

    def test_discharge_when_short(self):
        eng, ind = make_engine(elec=10.0)       # < 25% 容量 → 放电
        b = add(eng, ind, "F1", "batt")
        b.stored = 50.0
        p = eng.registry.get("power")
        p.tick(eng, 1.0)
        self.assertAlmostEqual(b.stored, 48.0, delta=0.05)
        self.assertAlmostEqual(eng.economy.get("electricity"), 12.0, delta=0.1)
        self.assertEqual(p.status(eng)["storage"][0]["state"], "discharging")

    def test_charge_respects_headroom(self):
        """电网快满时只能吸进"余量"那么多（充放电都不许把电网顶穿）。"""
        eng, ind = make_engine(elec=199.5, cfg=NO_LEAK)
        b = add(eng, ind, "F1", "batt")
        eng.registry.get("power").tick(eng, 1.0)
        self.assertLessEqual(eng.economy.get("electricity"), 200.0 + 1e-6)
        self.assertAlmostEqual(b.stored, 0.5 * 0.9, delta=0.02)

    def test_discharge_never_overshoots_capacity(self):
        eng, ind = make_engine(elec=10.0, cfg=NO_LEAK)
        b = add(eng, ind, "F1", "batt")
        b.stored = 99.0
        for _ in range(60):                    # 连放 60 秒
            eng.registry.get("power").tick(eng, 1.0)
        self.assertLessEqual(eng.economy.get("electricity"), 200.0 + 1e-6)
        self.assertGreaterEqual(b.stored, 0.0)

    def test_charge_rate_limited(self):
        eng, ind = make_engine(elec=199.0)
        b = add(eng, ind, "F1", "batt")
        eng.registry.get("power").tick(eng, 0.25)   # 0.25s → 最多 0.5kWh
        self.assertLessEqual(b.stored, 0.5 * 0.9 + 1e-6)

    def test_idle_band(self):
        eng, ind = make_engine(elec=120.0)      # 25%~75% → 待机
        b = add(eng, ind, "F1", "batt")
        eng.registry.get("power").tick(eng, 1.0)
        self.assertAlmostEqual(b.stored, 0.0, delta=1e-6)
        self.assertEqual(eng.registry.get("power").status(eng)["storage"][0]["state"],
                         "idle")
        self.assertFalse(b.stalled_reported, "储能待机不该算停摆")

    def test_storage_survives_save_load(self):
        eng, ind = make_engine(elec=180.0)
        b = add(eng, ind, "F1", "batt")
        eng.registry.get("power").tick(eng, 1.0)
        data = ind.to_dict()
        bid = b.id
        f = next(x for x in data["facilities"] if x["id"] == bid)
        self.assertGreater(f["stored"], 0.0)


class TestA12A14Bootstrap(unittest.TestCase):
    """A12（煤 120 + 圈1 首探保底煤矿）与 A14（开局 4 单元）。"""

    def _content(self, name):
        with io.open(os.path.join(ROOT, "content", name),
                     encoding="utf-8") as f:
            return json.load(f)

    def test_bootstrap_coal_and_units(self):
        b = self._content("bootstrap.json")
        self.assertEqual(float(b["resources"]["coal"]), 120.0)
        self.assertEqual(len(b["units"]), 4, "A14：开局 4 个执行单元")

    def test_regions_declares_guarantee(self):
        r = self._content("regions.json")
        self.assertEqual(r.get("guarantee", {}).get("1"), "coal")

    def test_first_ring1_survey_guarantees_coal(self):
        eng = Engine()
        eng.economy.set("electricity", 100.0)
        for n in ("甲", "乙"):
            eng.units.add_unit("执行器-" + n)
        eng.bootstrap_world([
            {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}])
        srv = SurveySystem(self._content("regions.json"), seed=7)
        eng.registry.register("survey", srv)
        eng.start()
        eng.clock.resume()
        self.assertFalse([p for p in eng.world.plots.values()
                          if p.substance == "coal"], "初始不该有煤矿")
        srv.survey(eng, 1)
        t = 0.0
        while t < 30.0 and not [p for p in eng.world.plots.values()
                                if p.substance == "coal"]:
            eng.tick(0.5)
            t += 0.5
        coal = [p for p in eng.world.plots.values() if p.substance == "coal"]
        self.assertTrue(coal, "圈1 首次勘探必须保底出煤矿（A12 ③）")
        # 保底只给一次：再探一圈1 不该总是煤矿
        srv.survey(eng, 1)
        t = 0.0
        while t < 30.0:
            eng.tick(0.5)
            t += 0.5
        self.assertGreaterEqual(len(eng.world.plots), 3)


if __name__ == "__main__":
    unittest.main()
