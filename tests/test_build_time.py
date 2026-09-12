"""建造耗时作业（A11）测试：占单元、建造中状态、完工、撤销、存档与即时建造旁路。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine                  # noqa: E402
from core.world import Plot                     # noqa: E402
from systems.industry import IndustrySystem     # noqa: E402

FACS = [
    {"id": "works", "name": "车间", "allowed_plot_kinds": ["empty"],
     "build_cost": {"scrap_alloy": 10.0}, "recipe": "make", "slots": 1,
     "build_time": 10.0},
    {"id": "quick", "name": "快建车间", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "make", "slots": 1},     # 无 build_time
]
RECIPES = [
    {"id": "make", "name": "加工",
     "inputs": {"ore": 1.0, "electricity": 0.2}, "outputs": {"metal": 0.5}},
]


def make_engine(units=2, scrap=100.0, ore=500.0, elec=500.0):
    eng = Engine()
    eng.economy.set("scrap_alloy", scrap)
    eng.economy.set("ore", ore)
    eng.economy.set("electricity", elec)
    for i in range(units):
        eng.units.add_unit(f"执行器-{i + 1}")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "P2", "ring": 0, "kind": "empty", "state": "claimed"},
    ])
    eng.registry.register("industry", IndustrySystem(FACS, RECIPES))
    eng.start()
    return eng


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def build_jobs(eng):
    return [j for j in eng.jobs.list_jobs() if j.kind == "build"]


class TestBuildJob(unittest.TestCase):
    def test_build_creates_job_and_occupies_unit(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        self.assertIsNone(ind.build(eng, "HOME", "works"))
        f = ind.facilities["F1"]
        self.assertTrue(f.under_construction)
        self.assertEqual(len(build_jobs(eng)), 1)
        self.assertAlmostEqual(build_jobs(eng)[0].remaining, 10.0, places=3)
        u = eng.units.units[0]
        self.assertEqual(u.status, "busy")
        self.assertEqual(u.task, "build")
        self.assertEqual(eng.units.count_idle(), 1)

    def test_assign_refused_while_building(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        err = ind.assign(eng, "F1")
        self.assertIsNotNone(err)
        self.assertIn("建造中", err)

    def test_no_idle_unit_refuses_without_cost(self):
        eng = make_engine(units=0)
        ind = eng.registry.get("industry")
        err = ind.build(eng, "HOME", "works")
        self.assertIsNotNone(err)
        self.assertIn("没有空闲执行单元", err)
        self.assertEqual(eng.economy.get("scrap_alloy"), 100.0)
        self.assertEqual(len(ind.facilities), 0)

    def test_insufficient_resource_releases_unit(self):
        eng = make_engine(scrap=1.0)
        ind = eng.registry.get("industry")
        err = ind.build(eng, "HOME", "works")
        self.assertIsNotNone(err)
        self.assertIn("资源不足", err)
        self.assertEqual(len(ind.facilities), 0)
        self.assertEqual(eng.units.count_idle(), 2)      # 单元已归还
        self.assertEqual(build_jobs(eng), [])

    def test_completion_enables_production(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        ticks(eng, 6.0)
        self.assertTrue(ind.facilities["F1"].under_construction,
                        "6s 时仍在施工")
        self.assertEqual(eng.economy.get("metal"), 0.0)
        ticks(eng, 6.0)
        f = ind.facilities["F1"]
        self.assertFalse(f.under_construction)
        self.assertEqual(build_jobs(eng), [])
        self.assertEqual(eng.units.count_idle(), 2)      # 施工单元已释放
        self.assertIsNone(ind.assign(eng, "F1"))
        ticks(eng, 10.0)
        self.assertGreater(eng.economy.get("metal"), 0.0)

    def test_no_power_draw_while_building(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        bal = ind.power_balance(eng)
        self.assertEqual(bal["consume"], 0.0)
        self.assertEqual(bal["consumers"], [])
        ticks(eng, 11.0)
        ind.assign(eng, "F1")
        bal2 = ind.power_balance(eng)
        self.assertAlmostEqual(bal2["consume"], 0.2, places=3)

    def test_undo_during_construction(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        ticks(eng, 2.0)
        self.assertIsNone(ind.undo_build(eng))
        self.assertEqual(len(ind.facilities), 0)
        self.assertEqual(build_jobs(eng), [])
        self.assertEqual(eng.units.count_idle(), 2)
        self.assertEqual(eng.economy.get("scrap_alloy"), 100.0)   # 全额返还
        self.assertEqual(eng.world.get("HOME").state, Plot.STATE_CLAIMED)

    def test_default_build_time_when_missing(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "quick")
        self.assertAlmostEqual(build_jobs(eng)[0].remaining,
                               ind.default_build_time, places=3)

    def test_instant_build_bypass(self):
        eng = make_engine(units=0)                 # 无单元也能建
        ind = eng.registry.get("industry")
        ind.instant_build = True
        self.assertIsNone(ind.build(eng, "HOME", "works"))
        f = ind.facilities["F1"]
        self.assertFalse(f.under_construction)
        self.assertEqual(build_jobs(eng), [])

    def test_install_instant_helper(self):
        eng = make_engine(units=0)
        ind = eng.registry.get("industry")
        self.assertIsNone(ind.install_instant(eng, "HOME", "works"))
        self.assertFalse(ind.facilities["F1"].under_construction)
        self.assertFalse(ind.instant_build)        # 开关已还原


class TestBuildSaveAndSelfHeal(unittest.TestCase):
    def test_save_load_keeps_construction(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        data = eng.to_dict()
        e2 = make_engine()                         # 同内容引擎再读档
        e2.from_dict(data)
        ind2 = e2.registry.get("industry")
        self.assertTrue(ind2.facilities["F1"].under_construction)
        self.assertEqual(len(build_jobs(e2)), 1)
        ticks(e2, 11.0)
        self.assertFalse(ind2.facilities["F1"].under_construction)

    def test_self_heal_when_job_missing(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "works")
        for j in build_jobs(eng):                  # 模拟作业丢失（异常读档）
            eng.jobs.cancel(j.id)
        ticks(eng, 1.0)
        self.assertFalse(ind.facilities["F1"].under_construction)
        self.assertIsNone(ind.assign(eng, "F1"))

    def test_old_save_without_field(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.instant_build = True
        ind.build(eng, "HOME", "works")
        data = eng.to_dict()
        for fd in data["systems"]["industry"]["facilities"]:
            fd.pop("under_construction", None)
        e2 = make_engine()
        e2.from_dict(data)
        self.assertFalse(e2.registry.get("industry")
                         .facilities["F1"].under_construction)


if __name__ == "__main__":
    unittest.main(verbosity=2)
