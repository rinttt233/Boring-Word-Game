"""压力层测试（批次2）：维护件消耗/设备状态/故障停机/封存 + 记忆劣化随规模增长。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine                       # noqa: E402
from systems.industry import IndustrySystem          # noqa: E402
from systems.maintenance import MaintenanceSystem    # noqa: E402
from systems.memory import MemorySystem              # noqa: E402
from systems.recovery import RecoverySystem          # noqa: E402

FACS = [
    {"id": "works", "name": "车间", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "make", "slots": 1, "build_time": 5.0},
]
RECIPES = [
    {"id": "make", "name": "加工", "inputs": {"ore": 1.0},
     "outputs": {"metal": 0.5}},
]
ENTRIES = [
    {"id": "db_upkeep", "name": "设备维护", "cost": {}, "duration": 1.0},
    {"id": "db_other", "name": "其它条目", "cost": {}, "duration": 1.0},
]
MCFG = {
    "requires_recovery": "db_upkeep",
    "kit": "maintenance_kit",
    "use_per_sec": 0.1,
    "idle_factor": 0.33,
    "repair_per_sec": 0.5,
    "neglect_decay_per_sec": 0.03,
    "max_upkeep": 100.0,
    "breakdown": {"threshold": 75.0, "max_chance_per_min": 0.5,
                  "downtime": 25.0, "on_restart_upkeep": 40.0},
    "mothball_restart_sec": 10.0,
    "warn_kit_seconds": 60.0,
}
MEM_CFG = {
    "max_integrity": 100.0,
    "degrade_per_sec": 0.02,
    "degrade_scale": {"per_fixated_entry": 0.002,
                      "per_running_facility": 0.004},
    "warn_threshold": 30.0,
    "crisis_threshold": 10.0,
    "maintain": {"cost": {}, "restore": 25.0, "duration": 1.0},
}


def make_engine(kits=100.0, units=2, upkeep=True, memory=False,
                plots=("HOME", "P2")):
    eng = Engine()
    eng.economy.set("maintenance_kit", kits)
    eng.economy.set("ore", 500.0)
    for i in range(units):
        eng.units.add_unit(f"执行器-{i + 1}")
    eng.bootstrap_world([{"id": p, "ring": 0, "kind": "empty",
                          "state": "claimed"} for p in plots])
    rec = RecoverySystem(ENTRIES)
    ind = IndustrySystem(FACS, RECIPES, mothball_restart_sec=10.0)
    ind.instant_build = True          # 本文件测维护压力，建造耗时另测
    eng.registry.register("recovery", rec)
    eng.registry.register("industry", ind)
    eng.registry.register("maintenance", MaintenanceSystem(MCFG, seed=1))
    if memory:
        eng.registry.register("memory", MemorySystem(MEM_CFG))
    eng.start()
    if upkeep:
        rec.status["db_upkeep"] = "permanent"
    return eng


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def build_one(eng, plot="HOME"):
    ind = eng.registry.get("industry")
    ind.build(eng, plot, "works")
    fid = [f.id for f in ind.facilities.values() if f.plot_id == plot][0]
    return ind.facilities[fid]


class TestMaintenanceGate(unittest.TestCase):
    def test_inactive_until_knowledge_recovered(self):
        eng = make_engine(kits=0.0, upkeep=False)
        f = build_one(eng)
        eng.registry.get("industry").assign(eng, f.id)
        maint = eng.registry.get("maintenance")
        self.assertFalse(maint.enabled(eng))
        self.assertEqual(maint.demand_per_sec(eng), 0.0)
        ticks(eng, 40.0)
        self.assertEqual(f.upkeep, 100.0)         # 未恢复知识 → 无维护压力
        self.assertEqual(f.halt_reason, None)

    def test_active_after_recovery(self):
        eng = make_engine()
        self.assertTrue(eng.registry.get("maintenance").enabled(eng))


class TestConsumption(unittest.TestCase):
    def test_running_consumes_full_rate(self):
        eng = make_engine(kits=100.0)
        f = build_one(eng)
        eng.registry.get("industry").assign(eng, f.id)
        ticks(eng, 5.0)
        self.assertAlmostEqual(100.0 - eng.economy.get("maintenance_kit"),
                               0.5, places=3)     # 0.1/s × 5s
        self.assertEqual(f.upkeep, 100.0)

    def test_idle_consumes_third(self):
        eng = make_engine(kits=100.0)
        build_one(eng)                            # 不分配单元 = 停着
        ticks(eng, 5.0)
        self.assertAlmostEqual(100.0 - eng.economy.get("maintenance_kit"),
                               0.1 * 0.33 * 5.0, places=3)

    def test_mothballed_consumes_nothing(self):
        eng = make_engine(kits=100.0)
        ind = eng.registry.get("industry")
        f = build_one(eng)
        ind.assign(eng, f.id)
        self.assertIsNone(ind.mothball(eng, f.id, on=True))
        self.assertTrue(f.mothballed)
        self.assertEqual(f.assigned, [])
        self.assertEqual(eng.units.count_idle(), 2)
        ticks(eng, 10.0)
        self.assertAlmostEqual(eng.economy.get("maintenance_kit"), 100.0,
                               places=3)
        self.assertEqual(eng.registry.get("maintenance")
                         .demand_per_sec(eng), 0.0)

    def test_unmothball_requires_unit_and_restart(self):
        eng = make_engine(kits=100.0, units=1)
        ind = eng.registry.get("industry")
        f = build_one(eng)
        ind.mothball(eng, f.id, on=True)
        self.assertIsNone(ind.mothball(eng, f.id, on=False))
        self.assertFalse(f.mothballed)
        self.assertAlmostEqual(f.halt_until, eng.clock.time + 10.0, places=3)
        self.assertEqual(f.halt_reason, "封存重启中")
        ticks(eng, 12.0)
        self.assertIsNone(f.halt_reason)          # 重启完成，提示清除
        metal0 = eng.economy.get("metal")
        ticks(eng, 6.0)
        self.assertGreater(eng.economy.get("metal"), metal0)   # 重启后复产


class TestShortageAndBreakdown(unittest.TestCase):
    def test_upkeep_decays_without_kits(self):
        eng = make_engine(kits=0.0)
        f = build_one(eng)
        eng.registry.get("industry").assign(eng, f.id)
        ticks(eng, 10.0)
        self.assertAlmostEqual(f.upkeep, 100.0 - 0.03 * 10.0, places=2)
        self.assertEqual(eng.stats.get("kits_used"), 0.0)

    def test_upkeep_recovers_with_kits(self):
        eng = make_engine(kits=100.0)
        f = build_one(eng)
        eng.registry.get("industry").assign(eng, f.id)
        f.upkeep = 50.0
        ticks(eng, 10.0)
        self.assertAlmostEqual(f.upkeep, 55.0, places=2)   # 0.5/s

    def test_breakdown_halts_and_repairs(self):
        eng = make_engine(kits=0.0)
        ind = eng.registry.get("industry")
        maint = eng.registry.get("maintenance")
        f = build_one(eng)
        ind.assign(eng, f.id)
        f.upkeep = 0.0
        maint.bd_max_chance = 100.0               # 确定性触发
        ticks(eng, 5.0)
        self.assertGreater(f.halt_until, 0.0)
        self.assertEqual(f.halt_reason, "维护失效停机")
        self.assertGreaterEqual(eng.stats.get("breakdowns"), 1.0)
        metal0 = eng.economy.get("metal")
        ticks(eng, 10.0)
        self.assertEqual(eng.economy.get("metal"), metal0)   # 停机期间不产出
        maint.bd_max_chance = 0.0                 # 停掉人工触发，观察抢修
        ticks(eng, 20.0)                                      # 停机结束
        self.assertGreaterEqual(f.upkeep, 38.0)               # 抢修到 40 附近
        self.assertLessEqual(f.upkeep, 40.5)
        self.assertNotEqual(f.halt_reason, "维护失效停机")

    def test_low_supply_warning_logged(self):
        eng = make_engine(kits=1.0)
        f = build_one(eng)
        eng.registry.get("industry").assign(eng, f.id)
        ticks(eng, 5.0)          # 需求 0.1/s，1 件只够 10s < 60s 阈值
        self.assertTrue(any("维护件库存偏低" in l for l in eng.log_lines))


class TestMaintenanceSave(unittest.TestCase):
    def test_fields_roundtrip(self):
        eng = make_engine(kits=100.0)
        ind = eng.registry.get("industry")
        f = build_one(eng)
        ind.assign(eng, f.id)
        f.upkeep = 61.5
        ind.mothball(eng, f.id, on=True)
        data = eng.to_dict()
        e2 = make_engine()
        e2.from_dict(data)
        f2 = e2.registry.get("industry").facilities[f.id]
        self.assertAlmostEqual(f2.upkeep, 61.5, places=3)
        self.assertTrue(f2.mothballed)

    def test_old_save_without_fields(self):
        eng = make_engine(kits=100.0)
        build_one(eng)
        data = eng.to_dict()
        for fd in data["systems"]["industry"]["facilities"]:
            for k in ("upkeep", "halt_until", "halt_reason", "mothballed"):
                fd.pop(k, None)
        e2 = make_engine()
        e2.from_dict(data)
        f2 = list(e2.registry.get("industry").facilities.values())[0]
        self.assertEqual(f2.upkeep, 100.0)
        self.assertFalse(f2.mothballed)


class TestMemoryPressure(unittest.TestCase):
    def test_rate_scales_with_scale_and_facilities(self):
        eng = make_engine(units=2, memory=True, upkeep=False)
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        mem = eng.registry.get("memory")
        # 基线：无固化条目、无运转设施
        self.assertAlmostEqual(mem.degrade_rate(eng), 0.02, places=6)
        rec.status["db_upkeep"] = "permanent"
        rec.status["db_other"] = "permanent"
        f = build_one(eng)
        ind.assign(eng, f.id)
        # 0.02 + 2×0.002 + 1×0.004 = 0.028
        self.assertAlmostEqual(mem.degrade_rate(eng), 0.028, places=6)
        self.assertEqual(mem._fixated_count(eng), 2)
        self.assertEqual(mem._running_facility_count(eng), 1)
        # 设施封存后不再计入"运转设施"
        ind.mothball(eng, f.id, on=True)
        self.assertEqual(mem._running_facility_count(eng), 0)
        self.assertAlmostEqual(mem.degrade_rate(eng), 0.024, places=6)

    def test_constant_when_no_scale_config(self):
        eng = make_engine(memory=False)
        mem = MemorySystem({"degrade_per_sec": 0.02})
        eng.registry.register("memory", mem)      # 覆盖注册
        mem.start(eng)
        self.assertAlmostEqual(mem.degrade_rate(eng), 0.02, places=6)

    def test_restore_tiers_by_integrity(self):
        mem = MemorySystem({
            "maintain": {"restore": 25.0, "restore_tiers": [
                {"below": 40.0, "restore": 32.0},
                {"below": 70.0, "restore": 25.0},
                {"below": 100.1, "restore": 16.0}]}})
        mem.integrity = 20.0
        self.assertEqual(mem.restore_amount(), 32.0)
        mem.integrity = 50.0
        self.assertEqual(mem.restore_amount(), 25.0)
        mem.integrity = 95.0
        self.assertEqual(mem.restore_amount(), 16.0)

    def test_flat_restore_without_tiers(self):
        mem = MemorySystem({"maintain": {"restore": 42.0}})
        mem.integrity = 10.0
        self.assertEqual(mem.restore_amount(), 42.0)

    def test_faster_decay_than_baseline(self):
        eng = make_engine(memory=True, upkeep=False)
        rec = eng.registry.get("recovery")
        mem = eng.registry.get("memory")
        ind = eng.registry.get("industry")
        rec.status["db_other"] = "permanent"       # 1 条固化
        f = build_one(eng)
        ind.assign(eng, f.id)                      # 1 台运转
        ticks(eng, 100.0)
        expect = 100.0 - (0.02 + 0.002 + 0.004) * 100.0
        self.assertAlmostEqual(mem.integrity, expect, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
