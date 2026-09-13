# -*- coding: utf-8 -*-
"""BUG-5 修复测试：临时知识窗口（180s / 游戏时间）+ fixate 排队 + 倒计时告警。"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                            # noqa: E402
from ui.agent_api import build_report, suggest_actions  # noqa: E402
from ui.commands import CommandRouter                  # noqa: E402


def setup(seed=3):
    with io.open(os.path.join(ROOT, "content", "substances.json"),
                 encoding="utf-8") as f:
        subs = {s["id"]: s for s in json.load(f)["substances"]}
    eng = main.build_engine(seed=seed)
    router = CommandRouter(eng, subs)
    eng.clock.resume()
    # 给足恢复/固化材料，专注测窗口与队列
    eng.economy.set("electricity", 5000.0)
    eng.economy.set("scrap_alloy", 1000.0)
    return eng, router, eng.registry.get("recovery")


def recover(eng, router, eid="db_coking"):
    router.execute(f"recover {eid}")
    for _ in range(80):                    # 等恢复作业完成
        eng.tick(0.5)
        if eng.registry.get("recovery").status.get(eid) == "active":
            return True
    return False


class TestWindowLength(unittest.TestCase):
    def test_content_ttl_is_180(self):
        with io.open(os.path.join(ROOT, "content", "recovery.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg.get("temporary_ttl_default"), 180.0)
        for e in cfg["entries"]:
            self.assertEqual(e.get("temporary_ttl"), 180.0,
                             f"{e['id']} 的临时窗口应为 180")

    def test_ttl_starts_on_recover_and_counts_game_time(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        left0 = rec.expires_in(eng, "db_coking")
        self.assertAlmostEqual(left0, 180.0, delta=2.0)
        eng.tick(10.0)
        self.assertAlmostEqual(rec.expires_in(eng, "db_coking"), 170.0,
                               delta=0.5)
        # 暂停 → 游戏时间不走 → 窗口不流逝
        eng.clock.pause()
        for _ in range(20):
            eng.tick(0.5)
        self.assertAlmostEqual(rec.expires_in(eng, "db_coking"), 170.0,
                               delta=0.5)
        eng.clock.resume()

    def test_expires_in_none_when_not_active(self):
        eng, router, rec = setup()
        self.assertIsNone(rec.expires_in(eng, "db_steel"))


class TestFixateQueue(unittest.TestCase):
    def test_fixate_queues_without_idle_unit(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):                # 占满所有单元
            eng.units.assign_any("test_busy")
        self.assertEqual(eng.units.count_idle(), 0)
        router.execute("fixate db_coking")
        self.assertTrue(rec.is_queued("db_coking"))
        self.assertEqual(rec.status["db_coking"], "active")
        # 排队**不**冻结窗口
        eng.tick(20.0)
        self.assertLess(rec.expires_in(eng, "db_coking"), 165.0)

    def test_queue_runs_when_unit_freed(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):
            eng.units.assign_any("test_busy")
        router.execute("fixate db_coking")
        self.assertTrue(rec.is_queued("db_coking"))
        eng.units.release_all()
        eng.tick(0.5)
        self.assertNotIn("db_coking", rec.queued, "队列应被服务")
        for _ in range(60):                # 等固化作业完成
            eng.tick(0.5)
            if rec.status["db_coking"] == "permanent":
                break
        self.assertEqual(rec.status["db_coking"], "permanent")
        self.assertIsNone(rec.expires_in(eng, "db_coking"))

    def test_queue_drops_expired_entry_with_warning(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):
            eng.units.assign_any("test_busy")
        router.execute("fixate db_coking")
        self.assertTrue(rec.is_queued("db_coking"))
        for _ in range(int(200 / 0.5)):    # 跑到窗口耗尽（单元一直不还）
            eng.tick(0.5)
        self.assertEqual(rec.status["db_coking"], "locked")
        self.assertNotIn("db_coking", rec.queued)
        logs = "\n".join(eng.log_lines[-40:])
        self.assertIn("排队", logs)

    def test_running_fixate_protects_entry(self):
        """已开工的烧录不会因为窗口到点而丢失。"""
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        eng.tick(176.0)                    # 先把窗口耗到只剩几秒
        self.assertLess(rec.expires_in(eng, "db_coking"), 5.0)
        rec.fixate(eng, "db_coking")       # 此时开工：作业 6s > 剩余窗口
        self.assertTrue(rec._fixate_running(eng, "db_coking"))
        locked_at = None
        for _ in range(20):
            eng.tick(0.5)
            if rec.status["db_coking"] == "locked":
                locked_at = rec.expires_in(eng, "db_coking")
                break
        self.assertIsNone(locked_at, "开工中的固化不该因窗口到点而丢失")
        self.assertEqual(rec.status["db_coking"], "permanent")

    def test_fixate_idempotent_while_queued(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):
            eng.units.assign_any("test_busy")
        rec.fixate(eng, "db_coking")
        rec.fixate(eng, "db_coking")
        self.assertEqual(rec.queued.count("db_coking"), 1)


class TestCountdownSurface(unittest.TestCase):
    def test_report_exposes_expires_in(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        rep = build_report(eng, router)
        act = [e for e in rep["entries"]["active"] if e["id"] == "db_coking"]
        self.assertEqual(len(act), 1)
        self.assertIn("expires_in", act[0])
        self.assertGreater(act[0]["expires_in"], 150.0)
        self.assertIn("fixate_queued", act[0])

    def test_report_queued_flag(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):
            eng.units.assign_any("test_busy")
        router.execute("fixate db_coking")
        rep = build_report(eng, router)
        act = [e for e in rep["entries"]["active"] if e["id"] == "db_coking"][0]
        self.assertTrue(act["fixate_queued"])

    def test_suggest_warns_when_about_to_expire(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        eng.tick(150.0)                    # 只剩 30s
        sugs = suggest_actions(eng, router, limit=6)
        self.assertTrue(sugs)
        self.assertEqual(sugs[0]["cmd"], "fixate db_coking",
                         f"应优先提示固化，实际：{sugs[0]}")
        self.assertIn("过期", sugs[0]["why"])

    def test_entries_command_shows_countdown(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        router.execute("entries")
        lines = [l for l in eng.log_lines if "db_coking" in l]
        self.assertTrue(any("剩余" in l for l in lines),
                        f"entries 应显示剩余秒数：{lines}")


class TestSaveLoad(unittest.TestCase):
    def test_queue_persisted_and_old_save_compatible(self):
        eng, router, rec = setup()
        self.assertTrue(recover(eng, router))
        for _ in range(10):
            eng.units.assign_any("test_busy")
        router.execute("fixate db_coking")
        data = rec.to_dict()
        self.assertIn("db_coking", data["queued"])
        eng2, router2, rec2 = setup(seed=4)
        rec2.load(data)
        self.assertIn("db_coking", rec2.queued)
        # 旧档（无 queued 字段）不炸
        old = {"status": {"db_coking": "active"},
               "active_until": {"db_coking": 999.0}}
        rec2.load(old)
        self.assertEqual(rec2.queued, [])


if __name__ == "__main__":
    unittest.main()
