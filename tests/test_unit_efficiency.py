"""单元效能（效率科技）测试：效能汇总 / 产能与耗料缩放 / 槽位折算 / 作业提速 / 存档兼容。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine                    # noqa: E402
from core.units import UnitPool                   # noqa: E402
from systems.industry import IndustrySystem       # noqa: E402
from systems.recovery import RecoverySystem       # noqa: E402

FACS = [
    {"id": "smelter", "name": "冶炼炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "smelt", "slots": 1},
    {"id": "big_smelter", "name": "大型冶炼炉",
     "allowed_plot_kinds": ["empty"], "build_cost": {}, "recipe": "smelt",
     "slots": 2},
    {"id": "miner", "name": "矿机", "allowed_plot_kinds": ["ore"],
     "build_cost": {}, "extract_rate": 1.0, "power_use": 0.5, "slots": 1},
]
RECIPES = [
    {"id": "smelt", "name": "冶炼", "inputs": {"ore": 1.0, "electricity": 0.5},
     "outputs": {"metal": 0.5}, "byproducts": {"slag": 0.1}},
]
ENTRIES = [
    {"id": "db_bus", "name": "总线复用", "cost": {"electricity": 5.0},
     "fixate_cost": {"electricity": 5.0}, "duration": 10.0,
     "temporary_ttl": 30.0, "grants": {"unit_efficiency": 0.25}},
    {"id": "db_prefetch", "name": "指令预取", "cost": {"electricity": 5.0},
     "duration": 20.0, "temporary_ttl": 30.0,
     "grants": {"unit_efficiency": 0.25}, "optional": True},
]


def make_engine():
    eng = Engine()
    eng.economy.set("ore", 1000.0)
    eng.economy.set("electricity", 1000.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "P2", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "MINE", "ring": 1, "kind": "ore", "state": "claimed",
         "substance": "ore", "reserve": 100.0, "known": True},
    ])
    eng.registry.register("recovery", RecoverySystem(ENTRIES))
    ind = IndustrySystem(FACS, RECIPES)
    ind.instant_build = True          # 本文件测效能数值；建造耗时见 test_build_time
    eng.registry.register("industry", ind)
    eng.start()
    return eng


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def staff(eng, ind, plot_id, def_id):
    """建好并分配一个单元，返回设施对象。"""
    err = ind.build(eng, plot_id, def_id)
    assert err is None, err
    f = [x for x in ind.facilities.values() if x.plot_id == plot_id][0]
    err = ind.assign(eng, f.id)
    assert err is None, err
    return f


class TestUnitPool(unittest.TestCase):
    def test_default_efficiency_is_one(self):
        p = UnitPool()
        self.assertEqual(p.efficiency, 1.0)
        self.assertEqual(p.staff_factor(1, 1), 1.0)
        self.assertEqual(p.staff_factor(1, 2), 1.0)   # ef=1 → 恒 1.0（保基线）

    def test_staff_factor_scales_with_ratio_and_eff(self):
        p = UnitPool()
        p.set_efficiency(2.0)
        self.assertAlmostEqual(p.staff_factor(1, 1), 2.0)
        self.assertAlmostEqual(p.staff_factor(1, 2), 1.5)   # 半员只拿一半加成
        self.assertAlmostEqual(p.staff_factor(2, 2), 2.0)
        self.assertEqual(p.staff_factor(0, 1), 0.0)
        self.assertEqual(p.staff_factor(3, 2), 2.0)         # 超额派员不再叠加

    def test_effective_slots(self):
        p = UnitPool()
        self.assertEqual(p.effective_slots(2), 2)
        p.set_efficiency(2.0)
        self.assertEqual(p.effective_slots(2), 1)
        self.assertEqual(p.effective_slots(3), 2)
        self.assertGreaterEqual(p.effective_slots(1), 1)

    def test_roundtrip_and_legacy(self):
        p = UnitPool()
        p.add_unit("a")
        p.set_efficiency(1.75)
        q = UnitPool.from_dict(p.to_dict())
        self.assertAlmostEqual(q.efficiency, 1.75)
        old = UnitPool.from_dict({"units": [], "seq": 2})   # 旧档无该字段
        self.assertEqual(old.efficiency, 1.0)

    def test_set_efficiency_clamps(self):
        p = UnitPool()
        p.set_efficiency(-3.0)
        self.assertGreater(p.efficiency, 0.0)


class TestGrantsSync(unittest.TestCase):
    def test_grants_count_only_when_usable(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        self.assertEqual(rec.grant_total("unit_efficiency"), 0.0)
        rec.status["db_bus"] = "active"           # 临时即可生效
        self.assertAlmostEqual(rec.unit_efficiency(), 1.25)
        rec.status["db_prefetch"] = "permanent"
        self.assertAlmostEqual(rec.unit_efficiency(), 1.5)

    def test_sync_updates_pool(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        rec.status["db_bus"] = "permanent"
        rec._sync_efficiency()
        self.assertAlmostEqual(eng.units.efficiency, 1.25)

    def test_ttl_expiry_drops_efficiency(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        rec.status["db_bus"] = "active"
        rec.active_until["db_bus"] = eng.clock.time - 1.0   # 已过期
        rec.tick(eng, 0.1)
        self.assertEqual(rec.status["db_bus"], "locked")
        self.assertAlmostEqual(eng.units.efficiency, 1.0)

    def test_memory_crash_drops_efficiency(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        rec.status["db_bus"] = "active"
        rec._sync_efficiency()
        self.assertAlmostEqual(eng.units.efficiency, 1.25)
        eng.bus.emit("memory_crash", {"integrity": 0.0})
        self.assertAlmostEqual(eng.units.efficiency, 1.0)


class TestProductionScaling(unittest.TestCase):
    def test_recipe_output_and_input_scale(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        staff(eng, ind, "HOME", "smelter")
        ticks(eng, 20.0)
        base = {"metal": eng.economy.get("metal"),
                "ore": 1000.0 - eng.economy.get("ore"),
                "slag": eng.economy.get("slag")}
        self.assertAlmostEqual(base["metal"], 10.0, places=3)   # 0.5/s × 20
        rec.status["db_bus"] = "permanent"           # ef = 1.25
        rec._sync_efficiency()
        e0 = eng.economy.get("metal")
        o0 = eng.economy.get("ore")
        s0 = eng.economy.get("slag")
        ticks(eng, 20.0)
        self.assertAlmostEqual(eng.economy.get("metal") - e0,
                               10.0 * 1.25, places=3)
        # 化学配比不变：耗料同乘
        self.assertAlmostEqual(o0 - eng.economy.get("ore"), 20.0 * 1.25,
                               places=3)
        self.assertAlmostEqual(eng.economy.get("slag") - s0,
                               base["slag"] * 1.25, places=3)

    def test_extractor_power_scales(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        staff(eng, ind, "MINE", "miner")
        ticks(eng, 10.0)
        used = 1000.0 - eng.economy.get("electricity")
        self.assertAlmostEqual(used, 5.0, places=3)   # 0.5/s × 10
        rec.status["db_bus"] = "permanent"            # 1.25
        rec._sync_efficiency()
        e0 = eng.economy.get("electricity")
        ticks(eng, 10.0)
        self.assertAlmostEqual(e0 - eng.economy.get("electricity"),
                               used * 1.25, places=3)

    def test_assign_cap_uses_effective_slots(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        f = staff(eng, ind, "HOME", "big_smelter")     # slots=2，已派 1 个单元
        eng.units.set_efficiency(2.0)                  # ef=2 → 只需 1 个单元
        self.assertEqual(eng.units.effective_slots(2), 1)
        err = ind.assign(eng, f.id)
        self.assertIsNotNone(err)
        self.assertIn("效能", err)

    def test_no_unit_no_output(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "smelter")
        ticks(eng, 10.0)
        self.assertEqual(eng.economy.get("metal"), 0.0)
        self.assertGreaterEqual(eng.stats.get("stall_seconds"), 9.0)


class TestJobSpeed(unittest.TestCase):
    def test_recover_duration_divided_by_eff(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        self.assertIsNone(rec.recover(eng, "db_prefetch"))   # duration 20
        job = eng.jobs.list_jobs()[0]
        self.assertAlmostEqual(job.remaining, 20.0, places=3)
        job.remaining = 0.0
        eng.jobs.tick(eng, 0.1)
        self.assertIsNone(rec.recover(eng, "db_bus"))        # duration 10

    def test_faster_with_efficiency(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        rec.status["db_bus"] = "permanent"                   # ef = 1.25
        rec._sync_efficiency()
        self.assertIsNone(rec.recover(eng, "db_prefetch"))    # duration 20
        job = eng.jobs.list_jobs()[0]
        self.assertAlmostEqual(job.remaining, 20.0 / 1.25, places=3)


class TestSaveCompat(unittest.TestCase):
    def test_old_save_without_efficiency_or_stats(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        staff(eng, ind, "HOME", "smelter")
        data = eng.to_dict()
        data["units"].pop("efficiency", None)
        data.pop("stats", None)
        e2 = Engine()
        e2.from_dict(data)                                   # 不应抛异常
        self.assertEqual(e2.units.efficiency, 1.0)
        self.assertGreaterEqual(len(e2.stats.snapshot()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
