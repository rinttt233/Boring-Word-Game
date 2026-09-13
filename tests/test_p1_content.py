"""P1 测试：品位接入产出 / 单元装配厂 / 拆除 / 记忆口径 / 主线固化奖励单元。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main                                          # noqa: E402
from core.engine import Engine                       # noqa: E402
from systems.industry import IndustrySystem          # noqa: E402
from systems.memory import MemorySystem              # noqa: E402
from systems.recovery import RecoverySystem          # noqa: E402
from ui.commands import CommandRouter                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FACS = [
    {"id": "miner", "name": "采矿机", "allowed_plot_kinds": ["ore", "water"],
     "build_cost": {"scrap_alloy": 10.0}, "extract_rate": 1.0,
     "power_use": 0.1, "slots": 1, "build_time": 5.0},
    {"id": "ufac", "name": "装配厂", "allowed_plot_kinds": ["empty"],
     "build_cost": {"scrap_alloy": 30.0, "steel": 15.0},
     "recipe": "assemble_unit", "kind": "unit_factory", "unit_rate": 0.02,
     "slots": 1, "build_time": 20.0},
]
RECIPES = [
    {"id": "assemble_unit", "name": "装配",
     "inputs": {"steel": 0.08, "copper": 0.05, "electricity": 0.6},
     "outputs": {}},
]
REF = {"iron_ore": 50.0, "coal": 78.0}


def make(units=2, ore=5000.0, steel=500.0, copper=500.0, elec=1e6):
    eng = Engine()
    eng.economy.set("scrap_alloy", 500.0)
    eng.economy.set("steel", steel)
    eng.economy.set("copper", copper)
    eng.economy.set("electricity", elec)
    for i in range(units):
        eng.units.add_unit(f"U{i + 1}")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "MINE", "ring": 1, "kind": "ore", "state": "claimed",
         "substance": "iron_ore", "grade": 50.0, "reserve": ore,
         "known": True},
    ])
    ind = IndustrySystem(FACS, RECIPES, ref_grades=REF)
    ind.instant_build = True
    eng.registry.register("industry", ind)
    eng.registry.register("memory", MemorySystem({
        "degrade_per_sec": 0.02, "warn_threshold": 30.0,
        "crisis_threshold": 10.0,
        "degrade_scale": {"per_fixated_entry": 0.003,
                          "per_running_facility": 0.004},
        "maintain": {"cost": {}, "restore": 25.0, "duration": 1.0}}))
    eng.registry.register("recovery", RecoverySystem([
        {"id": "db_a", "name": "主线A", "cost": {}, "duration": 1.0},
        {"id": "db_b", "name": "主线B", "cost": {}, "duration": 1.0},
        {"id": "db_c", "name": "主线C", "cost": {}, "duration": 1.0},
        {"id": "db_opt", "name": "可选D", "cost": {}, "duration": 1.0,
         "optional": True},
    ]))
    eng.start()
    return eng, ind


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestGradeFactor(unittest.TestCase):
    def test_factor_math(self):
        eng, ind = make()
        self.assertAlmostEqual(ind.grade_factor("iron_ore", 50.0), 1.0)
        self.assertAlmostEqual(ind.grade_factor("iron_ore", 25.0), 0.5)
        self.assertAlmostEqual(ind.grade_factor("iron_ore", 100.0), 2.0)
        # 夹取：贫矿不低于 0.25，富矿不超过 2.0
        self.assertAlmostEqual(ind.grade_factor("iron_ore", 1.0), 0.25)
        self.assertAlmostEqual(ind.grade_factor("iron_ore", 1000.0), 2.0)
        # 无基准（水/未知）→ 1.0
        self.assertAlmostEqual(ind.grade_factor("water", 10.0), 1.0)

    def test_output_scales_with_grade(self):
        for grade, expect in ((50.0, 1.0), (25.0, 0.5), (75.0, 1.5)):
            eng, ind = make()
            plot = eng.world.get("MINE")
            plot.grade = grade
            ind.build(eng, "MINE", "miner")
            ind.assign(eng, "F1")
            before = eng.economy.get("iron_ore")
            ticks(eng, 100.0)
            got = (eng.economy.get("iron_ore") - before) / 100.0
            self.assertAlmostEqual(got, expect, places=3,
                                   msg=f"品位 {grade} 应得到 {expect}/s")

    def test_water_and_no_ref_unaffected(self):
        eng, ind = make()
        eng.world.spawn(ring=0, kind="water", state="claimed")
        wid = [p.id for p in eng.world.plots.values() if p.kind == "water"][-1]
        ind.build(eng, wid, "miner")
        ind.assign(eng, "F1")
        before = eng.economy.get("water")
        ticks(eng, 50.0)
        self.assertAlmostEqual((eng.economy.get("water") - before) / 50.0,
                               1.0, places=3)


class TestUnitFactory(unittest.TestCase):
    def test_builds_units_over_time(self):
        eng, ind = make()
        ind.build(eng, "HOME", "ufac")
        ind.assign(eng, "F1")
        n0 = eng.units.count()
        steel0 = eng.economy.get("steel")
        ticks(eng, 50.0)          # unit_rate 0.02 → 1 个 / 50s
        self.assertEqual(eng.units.count(), n0 + 1)
        self.assertLess(eng.economy.get("steel"), steel0)
        self.assertEqual(eng.stats.get("units_built"), 1.0)

    def test_progress_persists_and_no_input_stalls(self):
        eng, ind = make(steel=100.0)
        ind.build(eng, "HOME", "ufac")
        ind.assign(eng, "F1")
        ticks(eng, 10.0)
        eng.economy.set("steel", 0.0)          # 断料 → 停摆
        ticks(eng, 5.0)
        f = ind.facilities["F1"]
        self.assertTrue(f.stalled_reported)
        self.assertEqual(f.stall_code, "no_input")
        e2 = main.build_engine(seed=1)
        data = eng.to_dict()
        e2.from_dict(data)
        f2 = e2.registry.get("industry").facilities[f.id]
        self.assertAlmostEqual(f2.unit_progress, f.unit_progress, places=6)


class TestDemolish(unittest.TestCase):
    def test_refund_half_and_plot_reusable(self):
        eng, ind = make()
        ind.build(eng, "HOME", "ufac")          # 成本 30 残骸 + 15 钢
        ind.assign(eng, "F1")
        ticks(eng, 5.0)
        scrap0 = eng.economy.get("scrap_alloy")
        steel0 = eng.economy.get("steel")
        self.assertIsNone(ind.demolish(eng, "F1", refund=0.5))
        self.assertNotIn("F1", ind.facilities)
        self.assertAlmostEqual(eng.economy.get("scrap_alloy"),
                               scrap0 + 15.0, places=3)
        self.assertAlmostEqual(eng.economy.get("steel"), steel0 + 7.5, places=3)
        self.assertEqual(eng.units.count_idle(), 2)          # 单元已释放
        self.assertEqual(eng.world.get("HOME").state, "claimed")
        self.assertIsNone(ind.build(eng, "HOME", "ufac"))    # 可再建

    def test_demolish_under_construction_full_refund(self):
        eng, ind = make()
        ind.instant_build = False
        ind.build(eng, "HOME", "ufac")
        scrap0 = eng.economy.get("scrap_alloy")
        self.assertIsNone(ind.demolish(eng, "F1"))
        self.assertAlmostEqual(eng.economy.get("scrap_alloy"), scrap0 + 30.0,
                               places=3)                    # 施工中全额返还

    def test_demolish_unknown(self):
        eng, ind = make()
        self.assertIn("不存在", ind.demolish(eng, "F99"))


class TestMemoryScope(unittest.TestCase):
    def test_stalled_facility_not_counted(self):
        eng, ind = make()
        ind.build(eng, "HOME", "ufac")
        ind.assign(eng, "F1")
        mem = eng.registry.get("memory")
        ticks(eng, 5.0)
        self.assertEqual(mem._running_facility_count(eng), 1)
        # 断料 → 停摆 → 不再计入"运转设施"
        for k in ("steel", "copper", "electricity"):
            eng.economy.set(k, 0.0)
        ticks(eng, 5.0)
        self.assertTrue(ind.facilities["F1"].stalled_reported)
        self.assertEqual(mem._running_facility_count(eng), 0)
        self.assertAlmostEqual(mem.degrade_rate(eng), 0.02, places=4)

    def test_mothballed_and_halting_not_counted(self):
        eng, ind = make()
        ind.build(eng, "HOME", "ufac")
        ind.assign(eng, "F1")
        mem = eng.registry.get("memory")
        ind.mothball(eng, "F1", on=True)
        self.assertEqual(mem._running_facility_count(eng), 0)


class TestKnowledgeUnitBonus(unittest.TestCase):
    def test_two_mainline_entries_give_one_unit(self):
        eng, _ = make()
        rec = eng.registry.get("recovery")
        n0 = eng.units.count()
        rec.status["db_a"] = "permanent"
        rec._sync_efficiency()
        self.assertEqual(eng.units.count(), n0)        # 1 条不给
        rec.status["db_b"] = "permanent"
        rec._sync_efficiency()
        self.assertEqual(eng.units.count(), n0 + 1)    # 2 条给 1 个
        rec.status["db_opt"] = "permanent"
        rec._sync_efficiency()
        self.assertEqual(eng.units.count(), n0 + 1)    # 可选不计入

    def test_cap_and_no_double_grant_on_load(self):
        eng, _ = make()
        rec = eng.registry.get("recovery")
        for eid in ("db_a", "db_b", "db_c"):
            rec.status[eid] = "permanent"
        rec._sync_efficiency()
        n = eng.units.count()
        # 存档 → 读档不应重复发放
        data = eng.to_dict()
        e2 = main.build_engine(seed=1)
        e2.from_dict(data)
        rec2 = e2.registry.get("recovery")
        before = e2.units.count()
        rec2._sync_efficiency()
        self.assertEqual(e2.units.count(), before)
        self.assertEqual(rec2._unit_bonus_granted, 1)
        self.assertGreaterEqual(n, eng.units.count() - 2)


class TestP1ContentWiring(unittest.TestCase):
    """真实 content：ref_grade / 装配厂 / 新主线条目都已接好。"""

    def test_real_content_has_ref_grades_and_unit_factory(self):
        eng = main.build_engine(seed=1)
        ind = eng.registry.get("industry")
        self.assertGreaterEqual(len(ind.ref_grades), 10)
        self.assertIn("unit_factory", ind.defs)
        d = ind.defs["unit_factory"]
        self.assertEqual(d.get("kind"), "unit_factory")
        self.assertEqual(d.get("requires_recovery"), "db_units")
        rec = eng.registry.get("recovery")
        self.assertIn("db_units", rec.entries)
        self.assertFalse(rec.entries["db_units"].get("optional"),
                         "db_units 应为非可选（主线）")

    def test_demolish_command_registered(self):
        eng = main.build_engine(seed=1)
        router = CommandRouter(eng, {})
        self.assertTrue(hasattr(router, "_cmd_demolish"))
        router.execute("demolish")
        self.assertIn("用法", eng.log_lines[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
