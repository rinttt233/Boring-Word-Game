"""M3 终局线测试：可靠数据库建造 → 记忆劣化终止。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine             # noqa: E402
from systems.database import DatabaseSystem  # noqa: E402
from systems.memory import MemorySystem    # noqa: E402
from systems.recovery import RecoverySystem  # noqa: E402

PROJECTS = [
    {"id": "p1", "name": "子系统一", "desc": "", "cost": {"steel": 5.0},
     "duration": 3.0},
    {"id": "p2", "name": "子系统二", "desc": "", "cost": {"steel": 5.0},
     "duration": 3.0},
    {"id": "p3", "name": "子系统三", "desc": "", "cost": {"steel": 5.0},
     "duration": 3.0},
]
ENTRIES = [
    {"id": "db_a", "name": "条目A", "desc": "", "cost": {},
     "fixate_cost": {}, "duration": 1.0, "temporary_ttl": 999.0,
     "unlocks_facility": []},
    {"id": "db_b", "name": "条目B", "desc": "", "cost": {},
     "fixate_cost": {}, "duration": 1.0, "temporary_ttl": 999.0,
     "unlocks_facility": []},
]
MEM_CFG = {"max_integrity": 100.0, "degrade_per_sec": 1.0,
           "warn_threshold": 30.0, "crisis_threshold": 10.0,
           "maintain": {"cost": {}, "restore": 100.0, "duration": 1.0}}


def make_engine():
    eng = Engine()
    eng.economy.set("steel", 1000.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.registry.register("memory", MemorySystem(MEM_CFG))
    eng.registry.register("recovery", RecoverySystem(ENTRIES))
    eng.registry.register("database", DatabaseSystem(PROJECTS))
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def fixate_all(eng):
    rec = eng.registry.get("recovery")
    for e in ENTRIES:
        rec.status[e["id"]] = "permanent"


class TestDatabase(unittest.TestCase):
    def test_sequential_build(self):
        eng = make_engine()
        db = eng.registry.get("database")
        self.assertEqual(db.next_project()["id"], "p1")
        # 逐项建造
        for i, p in enumerate(PROJECTS):
            self.assertIsNone(db.construct(eng))
            run_ticks(eng, 5)
            self.assertIn(p["id"], db.built)
        self.assertEqual(len(db.built), 3)

    def test_migrate_requires_all_fixated(self):
        eng = make_engine()
        db = eng.registry.get("database")
        fixate_all(eng)
        for p in PROJECTS:
            db.construct(eng)
            run_ticks(eng, 5)
        # 全部竣工 + 已固化 → migrate 自动完成
        self.assertTrue(db.is_complete())
        mem = eng.registry.get("memory")
        # database_complete 事件已把劣化关掉
        i0 = mem.integrity
        run_ticks(eng, 20)
        self.assertAlmostEqual(mem.integrity, i0)     # 不再劣化
        self.assertGreaterEqual(mem.integrity, 99.0)

    def test_migrate_blocked_if_not_fixated(self):
        eng = make_engine()
        db = eng.registry.get("database")
        # 不固化任何条目，直接建完
        for p in PROJECTS:
            db.construct(eng)
            run_ticks(eng, 5)
        self.assertFalse(db.is_complete())
        # migrate 手动尝试仍失败
        err = db.migrate(eng)
        self.assertIsNotNone(err)
        self.assertFalse(db.is_complete())
        self.assertTrue(any("尚未永久固化" in l for l in eng.log_lines))
        # 补固化后 migrate 成功
        fixate_all(eng)
        db.migrate(eng)
        self.assertTrue(db.is_complete())

    def test_roundtrip(self):
        eng = make_engine()
        db = eng.registry.get("database")
        db.construct(eng)
        run_ticks(eng, 5)
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)
        db2 = eng2.registry.get("database")
        self.assertIn("p1", db2.built)
        self.assertEqual(db2.next_project()["id"], "p2")


if __name__ == "__main__":
    unittest.main()
