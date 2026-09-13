"""agent 接口与盲测基础设施测试：report schema / suggest / cover / guide / 可复现性。

这些接口是"新会话的 AI 不读源码也能玩"的关键：契约必须稳定。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main                                          # noqa: E402
from ui.agent_api import (CoverageTracker, build_report,   # noqa: E402
                          guide_text, suggest_actions)
from ui.commands import CommandRouter                # noqa: E402


def make_router(seed=1):
    eng = main.build_engine(seed=seed)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "content", "substances.json"),
              encoding="utf-8") as f:
        subs = {s["id"]: s for s in json.load(f)["substances"]}
    return eng, CommandRouter(eng, subs), subs


class TestReportSchema(unittest.TestCase):
    def test_top_level_keys_stable(self):
        eng, r, _ = make_router()
        rep = build_report(eng, r)
        self.assertEqual(rep["report_version"], 1)
        for key in ("t", "paused", "memory", "units", "power", "resources",
                    "facilities", "plots", "entries", "database",
                    "maintenance", "jobs", "stats"):
            self.assertIn(key, rep, f"report 缺少字段 {key}")

    def test_json_serializable(self):
        eng, r, _ = make_router()
        text = json.dumps(build_report(eng, r), ensure_ascii=False)
        self.assertGreater(len(text), 200)
        json.loads(text)                       # 必须是合法 JSON

    def test_entries_split_by_state(self):
        eng, r, _ = make_router()
        rec = eng.registry.get("recovery")
        rec.status["db_coking"] = "permanent"
        rec.status["db_steel"] = "active"
        rep = build_report(eng, r)
        ids = [e["id"] for e in rep["entries"]["permanent"]]
        self.assertIn("db_coking", ids)
        self.assertIn("db_steel",
                      [e["id"] for e in rep["entries"]["active"]])
        # 主线/可选的划分要能看出来（AI 靠它决定优先级）
        main = [e for e in rep["entries"]["locked"] if not e["optional"]]
        self.assertTrue(main)

    def test_plot_grade_is_rounded_for_display_but_precise_in_report(self):
        eng, r, _ = make_router()
        eng.world.spawn(ring=1, kind="ore", substance="coal", grade=83.9121,
                        reserve=4101.0, state="known")
        pid = [p.id for p in eng.world.plots.values() if p.kind == "ore"][-1]
        eng.world.get(pid).set_survey(0.1, 0.2)
        rep = build_report(eng, r)
        row = next(p for p in rep["plots"] if p["id"] == pid)
        self.assertAlmostEqual(row["grade_true"], 83.912, places=2)
        self.assertIsNotNone(row["grade_est"])


class TestSuggest(unittest.TestCase):
    def test_fresh_game_suggests_power(self):
        eng, r, _ = make_router()
        items = suggest_actions(eng, r)
        self.assertTrue(items)
        self.assertTrue(any("power_plant" in s["cmd"] for s in items),
                        f"开局应建议建电站，实际: {items}")

    def test_suggestions_carry_reason_and_blocker(self):
        eng, r, _ = make_router(seed=3)
        for _ in range(3):
            eng.world.spawn(ring=1, kind="empty", state="claimed")
        eng.economy.set("scrap_alloy", 0.0)
        items = suggest_actions(eng, r)
        self.assertTrue(all("why" in s and s["why"] for s in items))
        self.assertTrue(all("blocked" in s for s in items))

    def test_always_has_fallback_wait(self):
        """没有任何可执行动作时也要给一条"等"，避免 AI 空转死循环。"""
        eng, r, _ = make_router()
        items = suggest_actions(eng, r, limit=50)
        self.assertTrue(any(s["wait"] for s in items))

    def test_when_all_materials_available_suggests_build(self):
        eng, r, _ = make_router(seed=5)
        eng.economy.set("scrap_alloy", 500.0)
        eng.economy.set("coal", 500.0)
        home = eng.world.get("HOME")
        home.state = "claimed"
        items = suggest_actions(eng, r, limit=12)
        self.assertTrue(any(s["cmd"].startswith("build HOME") for s in items))


class TestCoverage(unittest.TestCase):
    def test_progress_and_stickiness(self):
        eng, r, _ = make_router()
        tr = CoverageTracker()
        self.assertEqual(tr.progress()["done"], 0)
        tr.update(build_report(eng, r))
        first = tr.progress()
        self.assertGreater(first["total"], 10)
        # 粘性：即使状态回退，已判定的项不会丢
        eng.economy.set("coal", 0.0)
        tr.update(build_report(eng, r))
        self.assertGreaterEqual(tr.progress()["done"], first["done"])

    def test_survey_marks_coverage(self):
        eng, r, _ = make_router(seed=11)
        tr = CoverageTracker()
        sur = eng.registry.get("survey")
        for _ in range(2):
            sur.survey(eng, 1)
            t = 0.0
            while eng.jobs.count() > 0 and t < 60:
                eng.tick(0.5)
                t += 0.5
        tr.update(build_report(eng, r))
        self.assertIn("survey", tr.progress()["done_ids"])


class TestGuide(unittest.TestCase):
    def test_guide_text_has_core_sections(self):
        text = guide_text()
        for kw in ("目标", "规则", "开局", "排障", "接口", "上报"):
            self.assertIn(kw, text, f"向导缺少「{kw}」相关内容")

    def test_guide_section_filter(self):
        text = guide_text("opening")
        self.assertIn("开局", text)
        self.assertNotIn("常见卡点", text)


class TestSeedReproducibility(unittest.TestCase):
    def test_same_seed_same_world(self):
        def roll(seed):
            eng = main.build_engine(seed=seed)
            sur = eng.registry.get("survey")
            ids, subs = [], []
            for _ in range(4):
                sur.survey(eng, 1)
                t = 0.0
                while eng.jobs.count() > 0 and t < 60:
                    eng.tick(0.5)
                    t += 0.5
            for p in sorted(eng.world.plots.values(), key=lambda x: x.id):
                ids.append(p.id)
                subs.append((p.substance, round(p.grade, 3)))
            return ids, subs

        self.assertEqual(roll(42), roll(42))
        self.assertNotEqual(roll(42)[1], roll(43)[1])

    def test_main_cli_accepts_seed(self):
        eng = main.build_engine(seed=7)
        self.assertIsNotNone(eng.registry.get("survey"))


class TestCommandsRegistered(unittest.TestCase):
    def test_agent_commands_exist(self):
        eng, r, _ = make_router()
        for name in ("report", "suggest", "cover", "guide"):
            self.assertTrue(hasattr(r, f"_cmd_{name}"),
                            f"缺少命令 {name}")

    def test_report_to_file(self):
        eng, r, _ = make_router()
        out = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "saves", "_test_report.json")
        r.execute(f"report to {out}")
        self.assertTrue(os.path.exists(out))
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["report_version"], 1)
        os.remove(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
