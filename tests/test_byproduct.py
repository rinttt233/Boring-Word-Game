"""TL-0 副产物 + 燃烧发电测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine           # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402

HEAT = {"coal": 24.0, "coalgas": 18.0, "coke": 30.0, "bfgas": 3.5,
        "hydrogen": 120.0}
FACS = [
    {"id": "cokery", "name": "炼焦炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "coke", "slots": 1},
    {"id": "gas_gen", "name": "燃气发电机", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "burner", "burn_rate": 0.5,
     "burn_efficiency": 0.5, "slots": 1},
]
RECIPES = [
    {"id": "coke", "name": "炼焦", "inputs": {"coal": 0.5},
     "outputs": {"coke": 0.35},
     "byproducts": {"coalgas": 0.25, "coaltar": 0.05}},
]


def make_engine():
    eng = Engine()
    eng.economy.set("coal", 1000.0)
    eng.units.add_unit("α")
    eng.bootstrap_world([
        {"id": "H", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "H2", "ring": 0, "kind": "empty", "state": "claimed"}])
    eng.registry.register("industry", IndustrySystem(FACS, RECIPES, HEAT))
    eng.start()
    return eng


def run(eng, s, step=0.5):
    t = 0.0
    while t < s:
        eng.tick(step)
        t += step


class TestByproduct(unittest.TestCase):
    def test_byproduct_output(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "H", "cokery")
        ind.assign(eng, "F1")
        run(eng, 20)   # 0.35 coke/s → ~7t; 0.25 coalgas/s, 0.05 coaltar/s
        self.assertGreater(eng.economy.get("coke"), 5.0)
        self.assertGreater(eng.economy.get("coalgas"), 3.0)   # 副产物产出
        self.assertGreater(eng.economy.get("coaltar"), 0.5)


class TestBurner(unittest.TestCase):
    def test_burner_no_fuel_stalls(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "H", "gas_gen")
        ind.assign(eng, "F1")
        run(eng, 5)
        self.assertLess(eng.economy.get("electricity"), 1e-6)  # 无燃料零产

    def test_set_fuel_and_burn(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        eng.economy.set("coalgas", 100.0)
        ind.build(eng, "H", "gas_gen")
        ind.assign(eng, "F1")
        err = ind.set_fuel(eng, "F1", "coalgas")
        self.assertIsNone(err)
        e0 = eng.economy.get("electricity")
        run(eng, 10)   # 0.5 coalgas/s × 18 × 0.5 = 4.5 电/s
        self.assertGreater(eng.economy.get("electricity"), e0 + 30.0)
        # 燃料被消耗
        self.assertLess(eng.economy.get("coalgas"), 100.0)

    def test_set_fuel_rejects_nonburnable(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "H", "gas_gen")
        err = ind.set_fuel(eng, "F1", "coaltar")   # 无 heat_value
        self.assertIsNotNone(err)
        err2 = ind.set_fuel(eng, "F1", "nonexist")
        self.assertIsNotNone(err2)

    def test_burner_fuel_persists(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        eng.economy.set("coalgas", 50.0)
        ind.build(eng, "H", "gas_gen")
        ind.assign(eng, "F1")
        ind.set_fuel(eng, "F1", "coalgas")
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)
        f2 = list(eng2.registry.get("industry").facilities.values())[0]
        self.assertEqual(f2.fuel, "coalgas")


if __name__ == "__main__":
    unittest.main()
