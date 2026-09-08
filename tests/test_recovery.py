"""M2 恢复树/门控测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine             # noqa: E402
from core.world import Plot                # noqa: E402
from systems.claim import ClaimSystem      # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402
from systems.memory import MemorySystem    # noqa: E402
from systems.recovery import RecoverySystem  # noqa: E402

ENTRIES = [
    {"id": "db_coking", "name": "煤焦化", "desc": "d",
     "cost": {"electricity": 10.0}, "fixate_cost": {"electricity": 5.0},
     "duration": 4.0, "temporary_ttl": 20.0,
     "unlocks_facility": ["cokery"]},
]
FACS = [
    {"id": "power_plant", "name": "电厂", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "burn_coal", "slots": 1},
    {"id": "cokery", "name": "炼焦炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "coke", "slots": 1,
     "requires_recovery": "db_coking"},
]
RECIPES = [
    {"id": "burn_coal", "name": "发电",
     "inputs": {"coal": 0.1}, "outputs": {"electricity": 0.5}},
    {"id": "coke", "name": "炼焦",
     "inputs": {"coal": 0.2}, "outputs": {"coke": 0.1}},
]
MEM_CFG = {"max_integrity": 100.0, "degrade_per_sec": 10.0,
           "warn_threshold": 30.0, "crisis_threshold": 10.0,
           "maintain": {"cost": {}, "restore": 100.0, "duration": 1.0}}


def make_engine():
    eng = Engine()
    eng.economy.set("electricity", 100.0)
    eng.economy.set("coal", 100.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}])
    eng.registry.register("memory", MemorySystem(MEM_CFG))
    eng.registry.register("recovery", RecoverySystem(ENTRIES))
    eng.registry.register("claim", ClaimSystem())
    eng.registry.register("industry", IndustrySystem(FACS, RECIPES))
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def spawn_claimed_empty(eng):
    eng.world.spawn(ring=0, kind="empty", state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == "empty"][-1]


class TestRecovery(unittest.TestCase):
    def test_gated_build_before_recovery(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        err = ind.build(eng, "HOME", "cokery")
        self.assertIsNotNone(err)          # 未恢复 → 禁止建造
        self.assertIn("知识缺失", err)
        # 普通设施不受门控
        self.assertIsNone(ind.build(eng, "HOME", "power_plant"))

    def test_recover_unlocks_then_expires(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        # 恢复作业
        err = rec.recover(eng, "db_coking")
        self.assertIsNone(err)
        run_ticks(eng, 5)                  # duration 4s
        self.assertTrue(rec.is_unlocked("db_coking"))
        # 恢复后可建（换一块地，HOME 已被电厂占）
        eng.world.spawn(ring=0, kind="empty", state="claimed")
        pid = [p.id for p in eng.world.plots.values()
               if p.kind == "empty"][-1]
        self.assertIsNone(ind.build(eng, pid, "cokery"))
        # TTL 20s → 过期丢失
        run_ticks(eng, 25)
        self.assertFalse(rec.is_unlocked("db_coking"))

    def test_fixate_permanent_survives_expiry(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        err = rec.recover(eng, "db_coking")
        self.assertIsNone(err)
        run_ticks(eng, 5)                  # active
        err = rec.fixate(eng, "db_coking")
        self.assertIsNone(err)
        run_ticks(eng, 7)                  # fixate 6s
        self.assertTrue(rec.is_unlocked("db_coking"))
        st = rec.status["db_coking"]
        self.assertEqual(st, "permanent")
        run_ticks(eng, 30)                 # 远超 TTL 仍保留
        self.assertTrue(rec.is_unlocked("db_coking"))

    def test_memory_crash_loses_unfixed_only(self):
        eng = make_engine()
        rec = eng.registry.get("recovery")
        # 恢复两个不同条目
        rec.recover(eng, "db_coking")
        run_ticks(eng, 5)
        # 固化它
        rec.fixate(eng, "db_coking")
        run_ticks(eng, 7)                  # 固化完成
        # 再造一个临时条目（简易注入第二条目）
        rec.entries["db_x"] = {"id": "db_x", "name": "临时条目", "desc": "",
                               "cost": {}, "fixate_cost": {},
                               "duration": 1.0, "temporary_ttl": 999.0}
        rec.status["db_x"] = "active"
        # 记忆崩溃（degrade 10/s → 10s 归零）
        run_ticks(eng, 12)
        # 固化条目安然，临时条目丢失
        self.assertEqual(rec.status["db_coking"], "permanent")
        self.assertEqual(rec.status["db_x"], "locked")
        self.assertIn("记忆崩溃", "".join(eng.log_lines))

    def test_roundtrip(self):
        # 用慢劣化引擎避免读档后立刻记忆崩溃干扰条目状态断言
        eng = Engine()
        eng.economy.set("electricity", 100.0)
        eng.units.add_unit("执行器-α")
        eng.bootstrap_world([
            {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}])
        slow_mem = {"max_integrity": 100.0, "degrade_per_sec": 0.001,
                    "warn_threshold": 30.0, "crisis_threshold": 10.0,
                    "maintain": {"cost": {}, "restore": 100.0,
                                 "duration": 1.0}}
        eng.registry.register("memory", MemorySystem(slow_mem))
        eng.registry.register("recovery", RecoverySystem(ENTRIES))
        eng.registry.register("claim", ClaimSystem())
        eng.registry.register("industry", IndustrySystem(FACS, RECIPES))
        eng.start()
        rec = eng.registry.get("recovery")
        rec.recover(eng, "db_coking")
        run_ticks(eng, 5)
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)
        rec2 = eng2.registry.get("recovery")
        self.assertEqual(rec2.status["db_coking"], "active")
        # 恢复后设施可用状态存档可继续运行
        run_ticks(eng2, 5)
        self.assertTrue(rec2.is_unlocked("db_coking"))

    def test_unfixed_entry_halts_facility(self):
        """设施建成后若条目(临时)丢失，该设施应停摆。"""
        eng = make_engine()
        rec = eng.registry.get("recovery")
        ind = eng.registry.get("industry")
        # 恢复→建炼焦炉→分配
        rec.recover(eng, "db_coking")
        run_ticks(eng, 5)
        eng.economy.set("coal", 500.0)
        pid = spawn_claimed_empty(eng)
        ind.build(eng, pid, "cokery")
        fid = [f.id for f in ind.facilities.values()
               if f.plot_id == pid][0]
        ind.assign(eng, fid)
        self.assertEqual(len(ind.facilities[fid].assigned), 1)
        # 产量上升
        run_ticks(eng, 10)
        self.assertGreater(eng.economy.get("coke"), 0.0)
        # 条目过期（未固化）
        rec.active_until["db_coking"] = 0.0   # 强制过期
        run_ticks(eng, 2)
        self.assertEqual(rec.status["db_coking"], "locked")
        # 设施停摆：焦炭不再增加
        c0 = eng.economy.get("coke")
        run_ticks(eng, 10)
        self.assertLessEqual(eng.economy.get("coke"), c0 + 1e-9)


if __name__ == "__main__":
    unittest.main()
