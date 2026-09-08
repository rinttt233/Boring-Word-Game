"""电力体系测试：昼夜循环 / 燃料类别白名单 / 可再生(光热/光伏/风电)事件调制。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine                 # noqa: E402
from systems.daylight import DaylightSystem    # noqa: E402
from systems.environment import EnvironmentSystem  # noqa: E402
from systems.industry import IndustrySystem    # noqa: E402
from systems.recovery import RecoverySystem    # noqa: E402

DL_CFG = {"period": 60.0, "day": 32.0, "dusk": 6.0}
ENV_CFG = {"events": [
    {"id": "fair", "name": "晴", "weight": 100, "duration": 300.0,
     "effects": {}},
    {"id": "dust", "name": "沙暴", "weight": 1, "duration": 300.0,
     "effects": {"solar": 0.15, "pv": 0.05, "wind": 1.5}},
]}
FACS = [
    {"id": "coal_stove", "name": "小炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "burner", "burn_rate": 0.2,
     "burn_efficiency": 0.5, "fuel_classes": ["solid"], "slots": 1},
    {"id": "fluid_plant", "name": "流电站", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "burner", "burn_rate": 0.2,
     "burn_efficiency": 0.5, "fuel_classes": ["liquid"], "slots": 1},
    {"id": "csp", "name": "光热", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "renewable", "power_kind": "solar",
     "capacity": 1.0, "slots": 1},
    {"id": "pv", "name": "光伏", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "renewable", "power_kind": "pv",
     "capacity": 1.3, "slots": 1},
    {"id": "wind", "name": "风电", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "renewable", "power_kind": "wind",
     "capacity": 1.0, "slots": 1},
]
HEAT = {"coal": 24.0, "gasoline": 44.0}
FUEL_CLS = {"coal": "solid", "gasoline": "liquid"}


def make_engine():
    eng = Engine()
    eng.economy.set("coal", 1000.0)
    eng.economy.set("gasoline", 1000.0)
    eng.economy.set("electricity", 100.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}])
    eng.registry.register("recovery", RecoverySystem([]))
    eng.registry.register("industry",
                          IndustrySystem(FACS, [], heat_values=HEAT,
                                         fuel_classes=FUEL_CLS))
    eng.registry.register("environment", EnvironmentSystem(ENV_CFG, seed=3))
    eng.registry.register("daylight", DaylightSystem(DL_CFG))
    eng.start()
    return eng


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestDaylight(unittest.TestCase):
    def test_phase_by_time(self):
        eng = make_engine()
        dl = eng.registry.get("daylight")
        eng.clock.time = 10.0     # < 32 白天
        self.assertGreater(dl.effect("solar"), 0.99)
        eng.clock.time = 34.0     # 32..38 黄昏中
        self.assertLess(dl.effect("solar"), 1.0)
        self.assertGreater(dl.effect("solar"), 0.0)
        eng.clock.time = 45.0     # 夜
        self.assertEqual(dl.effect("solar"), 0.0)

    def test_pure_derived_roundtrip(self):
        eng = make_engine()
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)      # daylight 无状态，不崩即可
        dl2 = eng2.registry.get("daylight")
        eng2.clock.time = 10.0
        self.assertGreater(dl2.effect("solar"), 0.99)


class TestFuelWhitelist(unittest.TestCase):
    def test_solid_rejects_liquid(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "coal_stove")
        self.assertIsNotNone(ind.set_fuel(eng, "F1", "gasoline"))
        self.assertIsNone(ind.set_fuel(eng, "F1", "coal"))

    def test_liquid_rejects_solid(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "fluid_plant")
        self.assertIsNotNone(ind.set_fuel(eng, "F1", "coal"))
        self.assertIsNone(ind.set_fuel(eng, "F1", "gasoline"))


class TestRenewable(unittest.TestCase):
    def _build(self, eng, def_id, fuel=None):
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", def_id)
        ind.assign(eng, "F1")
        if fuel:
            ind.set_fuel(eng, "F1", fuel)
        return ind.facilities["F1"]

    def _produce(self, eng, seconds=20):
        e0 = eng.economy.get("electricity")
        ticks(eng, seconds)
        return eng.economy.get("electricity") - e0

    def test_csp_day_night(self):
        eng = make_engine()
        self._build(eng, "csp")
        eng.clock.time = 5.0          # 白昼
        day = self._produce(eng)
        eng.clock.time = 45.0         # 深夜(38..60)，窗口别跨边界
        night = self._produce(eng, seconds=10)
        self.assertGreater(day, 10.0)
        self.assertLess(night, 0.5)

    def test_wind_day_night_same_and_dust_boost(self):
        eng = make_engine()
        self._build(eng, "wind")
        env = eng.registry.get("environment")
        env.current_id = "fair"
        env._until = 1e18
        eng.clock.time = 5.0
        day = self._produce(eng)
        eng.clock.time = 50.0
        night = self._produce(eng)
        self.assertAlmostEqual(day, night, delta=1.0)   # 不分昼夜
        env.current_id = "dust"
        eng.clock.time = 50.0
        dusty = self._produce(eng)
        self.assertGreater(dusty, night * 1.3)          # 沙暴增强

    def test_pv_higher_than_csp_but_dust_cuts_more(self):
        """晴天光伏出力高于光热，但沙暴下几乎归零(削减远比光热狠)。"""
        eng = make_engine()
        env = eng.registry.get("environment")
        env.current_id = "fair"
        env._until = 1e18
        eng.clock.time = 5.0
        self._build(eng, "csp")
        csp = self._produce(eng)
        eng2 = make_engine()
        env2 = eng2.registry.get("environment")
        env2.current_id = "fair"
        env2._until = 1e18
        eng2.clock.time = 5.0
        self._build(eng2, "pv")
        pv = self._produce(eng2)
        self.assertGreater(pv, csp)          # 晴天：光伏更高
        # 沙暴：pv 削减比 csp 更狠
        env.current_id = "dust"
        eng.clock.time = 5.0
        csp_dust = self._produce(eng)
        env2.current_id = "dust"
        eng2.clock.time = 5.0
        pv_dust = self._produce(eng2)
        self.assertGreater(csp_dust / csp,
                           pv_dust / pv + 0.1)


if __name__ == "__main__":
    unittest.main()
