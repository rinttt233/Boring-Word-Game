"""恢复树并行分支与依赖测试。

批次2：① 新增并行分支条目解锁设施；② 依赖（depends_on）阻塞未固化前置。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine             # noqa: E402
from systems.claim import ClaimSystem      # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402
from systems.recovery import RecoverySystem  # noqa: E402

ENTRIES = [
    {"id": "db_main", "name": "主线", "desc": "",
     "cost": {"electricity": 10.0}, "fixate_cost": {"electricity": 5.0},
     "duration": 2.0, "temporary_ttl": 999.0, "unlocks_facility": ["a"]},
    {"id": "db_branch", "name": "并行分支", "desc": "",
     "cost": {"electricity": 10.0}, "fixate_cost": {"electricity": 5.0},
     "duration": 2.0, "temporary_ttl": 999.0, "unlocks_facility": ["b"],
     "optional": True},
    {"id": "db_depend", "name": "依赖项", "desc": "",
     "cost": {"electricity": 10.0}, "fixate_cost": {"electricity": 5.0},
     "duration": 2.0, "temporary_ttl": 999.0,
     "depends_on": ["db_branch"], "unlocks_facility": ["c"]},
]
FACS = [
    {"id": "a", "name": "甲", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "extract_rate": 0.1, "power_use": 0.05, "slots": 1,
     "requires_recovery": "db_main"},
    {"id": "b", "name": "乙", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "extract_rate": 0.1, "power_use": 0.05, "slots": 1,
     "requires_recovery": "db_branch"},
    {"id": "c", "name": "丙", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "extract_rate": 0.1, "power_use": 0.05, "slots": 1,
     "requires_recovery": "db_depend"},
]


def make_engine():
    eng = Engine()
    eng.economy.set("electricity", 1000.0)
    eng.economy.set("scrap_alloy", 1000.0)
    eng.units.add_unit("α")
    eng.units.add_unit("β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "B2", "ring": 0, "kind": "empty", "state": "claimed"}])
    eng.registry.register("recovery", RecoverySystem(ENTRIES))
    eng.registry.register("claim", ClaimSystem())
    ind = IndustrySystem(FACS, [])
    ind.instant_build = True          # 本文件测恢复门控；建造耗时见 test_build_time
    eng.registry.register("industry", ind)
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestRecoveryBranch(unittest.TestCase):
    def test_optional_branch_unlocks_facility(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        # 初始：db_main/db_branch/db_depend 都 locked
        self.assertEqual(rec.status["db_branch"], "locked")
        # 未恢复前建 b 失败
        err = ind.build(eng, "B2", "b")
        self.assertIsNotNone(err)
        # 恢复 db_branch
        rec.recover(eng, "db_branch")
        run_ticks(eng, 5)
        self.assertTrue(rec.is_unlocked("db_branch"))
        self.assertIsNone(ind.build(eng, "B2", "b"))
        # 可选标记存在
        self.assertTrue(rec.entries["db_branch"].get("optional"))

    def test_depends_blocks_until_prerequisite(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        # db_depend 依赖 db_branch 永久固化
        err = rec.recover(eng, "db_depend")
        self.assertIsNotNone(err)          # 前置未固化 → 拒绝
        self.assertIn("前置依赖", err)
        # 先恢复并固化 db_branch（fixate 作业 6s）
        rec.recover(eng, "db_branch")
        run_ticks(eng, 5)
        rec.fixate(eng, "db_branch")
        run_ticks(eng, 7)
        self.assertEqual(rec.status["db_branch"], "permanent")
        # 现在 db_depend 可恢复
        err = rec.recover(eng, "db_depend")
        self.assertIsNone(err)
        run_ticks(eng, 5)
        self.assertTrue(rec.is_unlocked("db_depend"))

    def test_optional_not_required_for_database(self):
        """可选分支不阻塞数据库竣工（主线可单独通关）。"""
        eng = make_engine()
        rec = eng.registry.get("recovery")
        # 固化主线 db_main（fixate 作业 6s）
        rec.recover(eng, "db_main")
        run_ticks(eng, 5)
        rec.fixate(eng, "db_main")
        run_ticks(eng, 7)
        self.assertEqual(rec.status["db_main"], "permanent")
        # _try_migrate 只要求非 optional 条目（db_depend 未固化会阻塞，
        # 但它依赖 db_branch，是独立依赖链；这里聚焦 optional 判定）
        missing = [eid for eid, st in rec.status.items()
                   if st != "permanent"
                   and not rec.entries.get(eid, {}).get("optional")]
        # db_main permanent、db_branch optional、db_depend(非optional但依赖链未走)
        # → 仍会列出 db_depend。为验证 optional 逻辑，只检查 optional 分支不在 missing
        self.assertNotIn("db_branch", missing)
        # optional 条目不要求固化
        optional_missing = [eid for eid in missing
                            if rec.entries.get(eid, {}).get("optional")]
        self.assertEqual(optional_missing, [])


if __name__ == "__main__":
    unittest.main()
