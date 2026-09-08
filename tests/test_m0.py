"""M0 内核单元测试：python -m unittest discover -s tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bus import EventBus            # noqa: E402
from core.clock import GameClock         # noqa: E402
from core.economy import Economy         # noqa: E402
from core.engine import Engine           # noqa: E402
from core.units import UnitPool          # noqa: E402
from core.world import World, Plot       # noqa: E402


class TestBus(unittest.TestCase):
    def test_sub_and_emit(self):
        bus = EventBus()
        got = []
        bus.on("boom", lambda p: got.append(p))
        bus.emit("boom", {"x": 1})
        self.assertEqual(got, [{"x": 1}])

    def test_off_and_on_any(self):
        bus = EventBus()
        got = []
        def h(p): got.append(("e", p))
        bus.on("a", h)
        bus.off("a", h)
        bus.emit("a", {})
        bus.on_any(lambda ev, p: got.append((ev, p)))
        bus.emit("b", {})
        self.assertEqual(got, [("b", {})])


class TestClock(unittest.TestCase):
    def test_advance_and_pause(self):
        c = GameClock()
        c.advance(5)
        self.assertAlmostEqual(c.time, 5)
        c.pause()
        c.advance(100)
        self.assertAlmostEqual(c.time, 5)
        c.resume()
        c.advance(1)
        self.assertAlmostEqual(c.time, 6)

    def test_cruise_auto_stop(self):
        c = GameClock()
        c.start_cruise(10)
        self.assertTrue(c.cruising)
        # 分步推进，跨过终点
        finished = False
        for _ in range(50):
            if c.advance(0.3):
                finished = True
                break
        self.assertTrue(finished)
        self.assertFalse(c.cruising)
        self.assertGreaterEqual(c.time, 10)

    def test_cruise_cancel(self):
        c = GameClock()
        c.start_cruise(10)
        c.cancel_cruise()
        self.assertFalse(c.cruising)
        self.assertFalse(c.advance(20))

    def test_roundtrip(self):
        c = GameClock()
        c.advance(42)
        c.pause()
        c2 = GameClock.from_dict(c.to_dict())
        self.assertAlmostEqual(c2.time, 42)
        self.assertTrue(c2.paused)


class TestEconomy(unittest.TestCase):
    def test_add_take(self):
        e = Economy()
        e.add("coal", 10)
        self.assertTrue(e.take("coal", 4))
        self.assertAlmostEqual(e.get("coal"), 6)
        self.assertFalse(e.take("coal", 999))   # 不足整体失败
        self.assertAlmostEqual(e.get("coal"), 6)

    def test_take_available(self):
        e = Economy()
        e.add("water", 5)
        got = e.take_available("water", 100)
        self.assertAlmostEqual(got, 5)
        self.assertAlmostEqual(e.get("water"), 0)
        # 断供即停
        self.assertAlmostEqual(e.take_available("water", 3), 0)

    def test_roundtrip(self):
        e = Economy()
        e.add("a", 1.5)
        e.add("b", 2)
        e2 = Economy.from_dict(e.to_dict())
        self.assertEqual(e2.snapshot(), e.snapshot())


class TestWorld(unittest.TestCase):
    def test_spawn_and_logistics(self):
        w = World()
        p0 = w.spawn(ring=0, kind=Plot.KIND_EMPTY, state=Plot.STATE_KNOWN)
        p1 = w.spawn(ring=1, kind=Plot.KIND_ORE, substance="iron_ore",
                     grade=58.0, reserve=1000, state=Plot.STATE_KNOWN)
        self.assertEqual(w.get(p0.id), p0)
        self.assertEqual(len(w.visible_plots()), 2)
        self.assertAlmostEqual(p0.logistics_multiplier(), 1.0)
        self.assertAlmostEqual(p1.logistics_multiplier(), 2.0)

    def test_unknown_hidden(self):
        w = World()
        w.spawn(state=Plot.STATE_UNKNOWN)
        self.assertEqual(w.visible_plots(), [])

    def test_roundtrip(self):
        w = World()
        w.spawn(ring=2, kind=Plot.KIND_ORE, substance="coal",
                grade=82, reserve=6800, state=Plot.STATE_KNOWN)
        w2 = World.from_dict(w.to_dict())
        self.assertEqual(len(w2.plots), 1)
        p = list(w2.plots.values())[0]
        self.assertEqual(p.substance, "coal")
        self.assertAlmostEqual(p.reserve, 6800)


class TestUnits(unittest.TestCase):
    def test_pool(self):
        pool = UnitPool()
        u1 = pool.add_unit("α")
        u2 = pool.add_unit("β")
        self.assertEqual(pool.count(), 2)
        self.assertEqual(pool.count_idle(), 2)
        self.assertAlmostEqual(pool.utilization(), 0.0)
        got = pool.assign_any("炉")
        self.assertIsNotNone(got)
        self.assertEqual(got.id, u1.id)
        self.assertEqual(pool.count_idle(), 1)
        self.assertAlmostEqual(pool.utilization(), 0.5)
        self.assertTrue(u2.assign("另炉"))    # 空闲 idle→busy 成功
        self.assertFalse(u1.assign("再派"))   # busy 拒绝
        u1.release()
        self.assertEqual(pool.count_idle(), 1)

    def test_roundtrip(self):
        pool = UnitPool()
        pool.add_unit("α").assign("A")
        p2 = UnitPool.from_dict(pool.to_dict())
        self.assertEqual(p2.count(), 1)
        self.assertEqual(p2.units[0].status, "busy")
        self.assertEqual(p2.units[0].task, "A")


class TestEngine(unittest.TestCase):
    def test_tick_and_cruise_autopause(self):
        eng = Engine()
        eng.clock.resume()
        eng.tick(3)
        self.assertAlmostEqual(eng.clock.time, 3)
        eng.cruise(5)
        for _ in range(40):
            eng.tick(0.3)
            if eng.clock.paused:
                break
        self.assertTrue(eng.clock.paused)
        self.assertIn("自动暂停", eng.log_lines[-1])

    def test_paused_no_advance(self):
        eng = Engine()
        eng.clock.pause()
        eng.tick(50)
        self.assertAlmostEqual(eng.clock.time, 0)

    def test_full_roundtrip(self):
        eng = Engine()
        eng.economy.add("coal", 7.5)
        eng.world.spawn(ring=1, kind=Plot.KIND_ORE, substance="coal",
                        grade=80, reserve=99, state=Plot.STATE_KNOWN)
        eng.units.add_unit("γ").assign("任务")
        eng.log("hello")
        eng.clock.advance(12)
        data = eng.to_dict()
        eng2 = Engine()
        eng2.from_dict(data)
        self.assertAlmostEqual(eng2.clock.time, 12)
        self.assertEqual(eng2.economy.snapshot(), eng.economy.snapshot())
        self.assertEqual(len(eng2.world.plots), 1)
        self.assertEqual(eng2.units.units[0].task, "任务")
        self.assertEqual(eng2.log_lines, eng.log_lines)


if __name__ == "__main__":
    unittest.main()
