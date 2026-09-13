# -*- coding: utf-8 -*-
"""§8（维持现状 + 强化预警）与 §11（提升单元奖励上限）的回归测试。"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                              # noqa: E402
from ui.agent_api import build_report, suggest_actions    # noqa: E402
from ui.commands import CommandRouter                     # noqa: E402

MAINLINE = ["db_coking", "db_blast_furnace", "db_steel", "db_sulfuric",
            "db_ammonia", "db_petrol", "db_copper", "db_distill",
            "db_refractory", "db_rare_earth", "db_unit_bus",
            "db_unit_parallel", "db_upkeep", "db_units"]


def make(seed=3):
    with io.open(os.path.join(ROOT, "content", "substances.json"),
                 encoding="utf-8") as f:
        subs = {s["id"]: s for s in json.load(f)["substances"]}
    eng = main.build_engine(seed=seed)
    eng.clock.resume()
    return eng, CommandRouter(eng, subs)


class TestUnitBonusCap(unittest.TestCase):
    """§11：奖励上限从写死的 6 提到 content 可配的 10。"""

    def test_config_is_data_driven(self):
        with io.open(os.path.join(ROOT, "content", "recovery.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        ub = cfg.get("unit_bonus")
        self.assertIsInstance(ub, dict, "奖励参数必须写在 content 里")
        self.assertGreaterEqual(int(ub["cap"]), 10, "上限已提升到 ≥10")
        self.assertEqual(int(ub["per_mainline"]), 2)

    def test_cap_is_read_from_content(self):
        eng, router = make()
        rec = eng.registry.get("recovery")
        self.assertEqual(rec.unit_bonus_cap, 10)
        self.assertEqual(rec.unit_bonus_per, 2)

    def test_full_mainline_grants_seven_units(self):
        """14 条主线 ÷ 2 = 7（旧上限 6 会截掉一个）→ 现在给满 7。"""
        eng, router = make()
        rec = eng.registry.get("recovery")
        base = eng.units.count()
        for eid in MAINLINE:
            rec.status[eid] = "permanent"
        rec._sync_efficiency()
        self.assertEqual(rec._unit_bonus_granted, 7)
        self.assertEqual(eng.units.count(), base + 7)

    def test_optional_entries_do_not_count(self):
        eng, router = make()
        rec = eng.registry.get("recovery")
        base = eng.units.count()
        optional = [e for e, d in rec.entries.items() if d.get("optional")][:8]
        for eid in optional:
            rec.status[eid] = "permanent"
        rec._sync_efficiency()
        self.assertEqual(eng.units.count(), base, "可选项不该发单元奖励")


class TestMemoryWarnings(unittest.TestCase):
    """§8：维持 maintain 成本不变，但预警要多级、带倒计时。"""

    def test_levels_and_countdown(self):
        eng, router = make()
        mem = eng.registry.get("memory")
        mem.integrity = 80.0
        self.assertEqual(mem.warn_level(eng), "ok")
        mem.integrity = 55.0
        self.assertEqual(mem.warn_level(eng), "watch")
        mem.integrity = 25.0
        self.assertEqual(mem.warn_level(eng), "warn")
        mem.integrity = 12.0
        self.assertEqual(mem.warn_level(eng), "crisis")
        mem.integrity = 5.0
        self.assertEqual(mem.warn_level(eng), "critical")
        secs = mem.seconds_to_crash(eng)
        self.assertGreater(secs, 0.0)
        self.assertLess(secs, 10000.0)

    def test_crossing_levels_logs_once_each(self):
        eng, router = make()
        mem = eng.registry.get("memory")
        # 逐档手动下压（劣化本身很慢，靠 tick 走到 8% 要几千秒）
        for v in (55.0, 25.0, 12.0, 5.0):
            for _ in range(3):          # 同一档连跑三拍：只应记一次
                mem.integrity = v
                eng.tick(0.5)
        hits = [l for l in eng.log_lines if "分钟后归零" in l]
        self.assertGreaterEqual(len(hits), 4, f"应逐级预警（watch/warn/crisis/critical）：{hits}")
        self.assertEqual(len(hits), len(set(hits)), "同一档不该重复刷")
        self.assertTrue(any("critical" in l for l in hits))
        self.assertTrue(any("watch" in l for l in hits))

    def test_maintain_cost_unchanged(self):
        """§8 用户选"维持现状"：maintain 的成本不能动。"""
        with io.open(os.path.join(ROOT, "content", "memory.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        cost = cfg["maintain"]["cost"]
        self.assertEqual(float(cost["electricity"]), 20.0)
        self.assertEqual(float(cost["scrap_alloy"]), 5.0)

    def test_report_exposes_level_and_countdown(self):
        eng, router = make()
        mem = eng.registry.get("memory")
        mem.integrity = 20.0
        rep = build_report(eng, router)
        self.assertIn("level", rep["memory"])
        self.assertEqual(rep["memory"]["level"], "warn")
        self.assertGreater(rep["memory"]["seconds_to_crash"], 0)

    def test_suggest_escalates_when_critical(self):
        eng, router = make()
        mem = eng.registry.get("memory")
        mem.integrity = 9.0
        sugs = suggest_actions(eng, router, limit=6)
        self.assertTrue(sugs)
        self.assertEqual(sugs[0]["cmd"], "maintain",
                         f"临界时应最优先 maintain，实际：{sugs[0]}")
        self.assertIn("未固化", sugs[0]["why"])

    def test_suggest_tells_you_to_free_a_unit(self):
        eng, router = make()
        mem = eng.registry.get("memory")
        mem.integrity = 9.0
        for _ in range(20):                  # 占满所有单元
            eng.units.assign_any("busy")
        sugs = suggest_actions(eng, router, limit=6)
        self.assertEqual(sugs[0]["cmd"], "")
        self.assertIn("空闲执行单元", sugs[0]["blocked"])


if __name__ == "__main__":
    unittest.main()
