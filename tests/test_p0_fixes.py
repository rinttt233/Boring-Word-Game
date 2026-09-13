"""P0 补丁回归测试：CRASH-1 / MIGRATED-1 / POWER-1 / PLOT_BUILD-1 / READLOCK-1。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main                                          # noqa: E402
from systems.industry import IndustrySystem          # noqa: E402
from ui.agent_api import build_report                # noqa: E402
from ui.commands import CommandRouter                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FACS = [
    {"id": "plant", "name": "燃煤电站", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "burner", "burn_rate": 0.4,
     "burn_efficiency": 0.4, "fuel_classes": ["solid"], "slots": 1,
     "build_time": 5.0},
    {"id": "miner", "name": "采矿机", "allowed_plot_kinds": ["ore"],
     "build_cost": {}, "extract_rate": 0.8, "power_use": 0.5, "slots": 1,
     "build_time": 5.0},
    {"id": "works", "name": "车间", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "make", "slots": 1, "build_time": 5.0},
]
RECIPES = [{"id": "make", "name": "加工", "inputs": {"ore": 1.0},
            "outputs": {"metal": 0.5}}]
HEAT = {"coal": 24.0}
FUEL_CLS = {"coal": "solid"}


def make(units=2, coal=100.0):
    from core.engine import Engine
    from systems.database import DatabaseSystem
    from systems.memory import MemorySystem
    from systems.recovery import RecoverySystem
    eng = Engine()
    eng.seed = 7
    eng.economy.set("coal", coal)
    eng.economy.set("electricity", 100.0)
    eng.economy.set("ore", 500.0)
    for i in range(units):
        eng.units.add_unit(f"U{i + 1}")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "P2", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "MINE", "ring": 1, "kind": "ore", "state": "claimed",
         "substance": "coal", "reserve": 5000.0, "known": True},
    ])
    ind = IndustrySystem(FACS, RECIPES, heat_values=HEAT,
                         fuel_classes=FUEL_CLS)
    ind.instant_build = True
    eng.registry.register("industry", ind)
    with open(os.path.join(ROOT, "content", "database.json"),
              encoding="utf-8") as f:
        projects = json.load(f)["projects"]
    eng.registry.register("database", DatabaseSystem(projects))
    eng.registry.register("memory", MemorySystem({
        "max_integrity": 100.0, "degrade_per_sec": 0.02,
        "warn_threshold": 30.0, "crisis_threshold": 10.0,
        "maintain": {"cost": {}, "restore": 25.0, "duration": 1.0}}))
    eng.registry.register("recovery", RecoverySystem([
        {"id": "db_main", "name": "主线条目", "cost": {}, "duration": 1.0},
    ]))
    eng.start()
    return eng, ind


class TestCrash1DatabaseCommand(unittest.TestCase):
    """CRASH-1：5/5 竣工但未迁移时，db 命令不得崩溃。"""

    def test_db_command_with_all_built_unmigrated(self):
        eng, _ = make()
        router = CommandRouter(eng, {})
        db = eng.registry.get("database")
        db.built = [p["id"] for p in db.projects]
        db.complete = False
        router.execute("db")                     # 修复前：TypeError
        joined = "\n".join(eng.log_lines[-8:])
        self.assertIn("全部子系统已竣工", joined)
        self.assertIn("migrate", joined)

    def test_db_command_before_any_build(self):
        eng, _ = make()
        router = CommandRouter(eng, {})
        router.execute("db")
        self.assertIn("▶当前", "\n".join(eng.log_lines[-10:]))


class TestMigrated1Fields(unittest.TestCase):
    """MIGRATED-1：migrated / complete / can_migrate 语义与 report 字段。"""

    def test_report_semantics_before_and_after_migrate(self):
        eng, _ = make()
        router = CommandRouter(eng, {})
        db = eng.registry.get("database")
        rec = eng.registry.get("recovery")
        for eid in rec.entries:
            rec.status[eid] = "permanent"
        db.built = [p["id"] for p in db.projects]
        rep = build_report(eng, router)
        self.assertTrue(rep["database"]["can_migrate"])       # 建完了
        self.assertFalse(rep["database"]["complete"])         # 还没迁移
        self.assertFalse(rep["database"]["migrated"])
        router.execute("migrate")
        eng.tick(0.5)
        rep = build_report(eng, router)
        self.assertTrue(rep["database"]["migrated"])
        self.assertTrue(rep["database"]["complete"])
        self.assertTrue(rep["victory"])                       # 显式终局信号
        self.assertEqual(rep["memory"]["degrade"], 0.0)       # 已终止 → 0
        self.assertGreater(rep["memory"]["degrade_rated"], 0.0)

    def test_migrated_persisted(self):
        eng, _ = make()
        db = eng.registry.get("database")
        db.migrated = True
        db.complete = True
        data = eng.to_dict()
        e2 = main.build_engine(seed=1)
        e2.from_dict(data)
        self.assertTrue(e2.registry.get("database").migrated)

    def test_old_save_without_migrated(self):
        eng, _ = make()
        data = eng.to_dict()
        systems = data["systems"]["database"]
        systems.pop("migrated", None)
        systems["complete"] = True
        e2 = main.build_engine(seed=1)
        e2.from_dict(data)
        self.assertTrue(e2.registry.get("database").migrated)  # 用 complete 兜底

    def test_report_has_seed_and_command_count(self):
        eng, _ = make()
        router = CommandRouter(eng, {})
        router.execute("status")
        rep = build_report(eng, router)
        self.assertEqual(rep["seed"], 7)
        self.assertGreaterEqual(rep["command_count"], 1)


class TestPowerActualVsRated(unittest.TestCase):
    """POWER-1：停摆的发电机不再计入实际产出。"""

    def test_stalled_burner_excluded_from_actual(self):
        eng, ind = make(coal=0.0)               # 没煤
        ind.build(eng, "HOME", "plant")
        ind.assign(eng, "F1")
        ind.set_fuel(eng, "F1", "coal")
        eng.tick(1.0)
        f = ind.facilities["F1"]
        self.assertTrue(f.stalled_reported)
        self.assertEqual(f.stall_code, "no_fuel")
        bal = ind.power_balance(eng)
        self.assertEqual(bal["produce"], 0.0)              # 实际 = 0
        self.assertGreater(bal["produce_rated"], 0.0)      # 额定仍可参考
        self.assertEqual(bal["producers"], [])

    def test_running_burner_counted(self):
        eng, ind = make(coal=100.0)
        ind.build(eng, "HOME", "plant")
        ind.assign(eng, "F1")
        ind.set_fuel(eng, "F1", "coal")
        eng.tick(1.0)
        bal = ind.power_balance(eng)
        self.assertGreater(bal["produce"], 0.0)
        self.assertEqual(len(bal["producers"]), 1)

    def test_status_bar_source_consistency(self):
        """面板不再自相矛盾：缺电停摆的设施其耗电也不计入实际。"""
        eng, ind = make(coal=0.0, units=2)
        ind.build(eng, "MINE", "miner")
        ind.assign(eng, "F1")                    # 采矿机没电
        eng.economy.set("electricity", 0.0)
        eng.tick(1.0)
        miner = ind.facilities["F1"]
        self.assertTrue(miner.stalled_reported)
        bal = ind.power_balance(eng)
        self.assertEqual(bal["consume"], 0.0)
        self.assertGreaterEqual(bal["consume_rated"], 0.0)


class TestPlotBuild(unittest.TestCase):
    """PLOT_BUILD-1：depleted 可重建；developed 有设施时报准确原因。"""

    def test_existing_facility_message(self):
        eng, ind = make()
        self.assertIsNone(ind.build(eng, "HOME", "plant"))
        err = ind.build(eng, "HOME", "works")
        self.assertIsNotNone(err)
        self.assertIn("已有设施", err)
        self.assertNotIn("尚未占领", err)

    def test_rebuild_on_depleted(self):
        eng, ind = make()
        ind.build(eng, "MINE", "miner")
        eng.world.get("MINE").state = "depleted"     # 模拟采空
        ind.facilities.clear()                       # 枯竭时设施已被移除
        self.assertIsNone(ind.build(eng, "MINE", "miner"),
                          "depleted 地块应允许重建")

    def test_known_plot_still_requires_claim(self):
        eng, ind = make()
        eng.world.get("P2").state = "known"
        err = ind.build(eng, "P2", "works")
        self.assertIn("尚未占领", err)


class TestReadlock(unittest.TestCase):
    """READLOCK-1：死锁体检 + 读档一次性应急启动。"""

    def test_deadlock_detected_and_reported(self):
        eng, ind = make(coal=0.0)
        ind.build(eng, "MINE", "miner")
        ind.assign(eng, "F1")
        eng.economy.set("electricity", 0.0)
        eng.tick(0.5)
        why = ind.power_deadlock(eng)
        self.assertIsNotNone(why)
        self.assertIn("死锁", why)

    def test_no_deadlock_when_fuel_available(self):
        eng, ind = make(coal=50.0)
        ind.build(eng, "HOME", "plant")
        ind.assign(eng, "F1")
        ind.set_fuel(eng, "F1", "coal")
        eng.tick(1.0)
        self.assertIsNone(ind.power_deadlock(eng))

    def test_load_triggers_emergency_coal_once(self):
        eng, _ = make(coal=0.0)
        router = CommandRouter(eng, {})
        # 先造一个死锁局并保存
        ind = eng.registry.get("industry")
        ind.instant_build = True
        ind.build(eng, "MINE", "miner")
        ind.assign(eng, "F1")
        eng.economy.set("electricity", 0.0)
        eng.tick(0.5)
        router.execute("save p0_deadlock")
        router.execute("load p0_deadlock")
        self.assertAlmostEqual(eng.economy.get("coal"), 30.0, places=1)
        self.assertEqual(eng.stats.get("emergency_starts"), 1.0)
        self.assertIn("应急启动", "\n".join(eng.log_lines[-6:]))
        # 第二次读档：存档里的煤仍是 0，且**不再重复发放**
        # （from_dict 会整体替换 log_lines，所以只看载入后新追加的尾部）
        router.execute("load p0_deadlock")
        self.assertAlmostEqual(eng.economy.get("coal"), 0.0, places=1)
        tail = "\n".join(eng.log_lines[-3:])
        self.assertIn("本局已用过一次", tail)
        self.assertNotIn("已发放应急启动煤", tail)
        self.assertTrue(getattr(router, "_emergency_used"))
        for name in ("p0_deadlock",):
            p = os.path.join(ROOT, "saves", name + ".json")
            if os.path.exists(p):
                os.remove(p)


class TestStallCode(unittest.TestCase):
    def test_no_input_code(self):
        eng, ind = make()
        ind.build(eng, "HOME", "works")
        ind.assign(eng, "F1")
        eng.economy.set("ore", 0.0)
        eng.tick(1.0)
        f = ind.facilities["F1"]
        self.assertTrue(f.stalled_reported)
        self.assertEqual(f.stall_code, "no_input")
        rep = build_report(eng, CommandRouter(eng, {}))
        row = next(x for x in rep["facilities"] if x["id"] == "F1")
        self.assertEqual(row["reason_code"], "no_input")


if __name__ == "__main__":
    unittest.main(verbosity=2)
