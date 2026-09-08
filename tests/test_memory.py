"""M1-B 记忆劣化/维护测试。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine             # noqa: E402
from systems.memory import MemorySystem    # noqa: E402

MEM_CFG = {
    "max_integrity": 100.0,
    "degrade_per_sec": 1.0,     # 加速：每秒掉 1% → 30s 触警
    "warn_threshold": 30.0,
    "crisis_threshold": 10.0,
    "maintain": {"cost": {"electricity": 5.0, "scrap_alloy": 1.0},
                 "restore": 40.0, "duration": 4.0,
                 "desc": "test"},
}


def make_engine():
    eng = Engine()
    eng.economy.set("electricity", 100.0)
    eng.economy.set("scrap_alloy", 100.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.registry.register("memory", MemorySystem(MEM_CFG))
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestMemoryDecay(unittest.TestCase):
    def test_decay_over_time(self):
        eng = make_engine()
        mem = eng.registry.get("memory")
        self.assertAlmostEqual(mem.integrity, 100.0)
        run_ticks(eng, 10)
        self.assertAlmostEqual(mem.integrity, 90.0, delta=0.5)

    def test_paused_no_decay(self):
        eng = make_engine()
        eng.clock.pause()
        mem = eng.registry.get("memory")
        run_ticks(eng, 10)
        self.assertAlmostEqual(mem.integrity, 100.0)

    def test_warning_logged_once(self):
        eng = make_engine()
        run_ticks(eng, 71)     # 掉到 ~29%
        warns = [l for l in eng.log_lines if "低于阈值" in l]
        self.assertEqual(len(warns), 1)

    def test_maintain_restores(self):
        eng = make_engine()
        mem = eng.registry.get("memory")
        run_ticks(eng, 50)     # integrity ~50
        before = mem.integrity
        err = mem.maintain(eng)
        self.assertIsNone(err)
        # 作业 4s
        run_ticks(eng, 5)
        self.assertGreater(mem.integrity, before)
        self.assertAlmostEqual(mem.integrity, 50.0 - 50.0 * 0 + 40.0
                               - 5.0, delta=3.0)
        # 资源被消耗
        self.assertLess(eng.economy.get("electricity"), 100.0)

    def test_maintain_needs_idle_unit_and_resources(self):
        eng = make_engine()
        eng.economy.set("electricity", 0.0)     # 缺电
        mem = eng.registry.get("memory")
        err = mem.maintain(eng)
        self.assertIsNotNone(err)
        eng.economy.set("electricity", 100.0)
        eng.units.units[0].assign("a")
        eng.units.units[1].assign("b")
        err = mem.maintain(eng)
        self.assertIsNotNone(err)

    def test_roundtrip(self):
        eng = make_engine()
        run_ticks(eng, 30)     # 掉到 ~70
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)
        mem2 = eng2.registry.get("memory")
        self.assertAlmostEqual(mem2.integrity, 70.0, delta=1.0)
        # 读档后继续劣化
        run_ticks(eng2, 10)
        self.assertAlmostEqual(mem2.integrity, 60.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
