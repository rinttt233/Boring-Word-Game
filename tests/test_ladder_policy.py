# -*- coding: utf-8 -*-
"""阶梯盲玩策略（tools/ladder_policy.py）测试。

这个策略的意义是：**只用文档化接口**（`report` + 游戏命令 + `content/*.json`）
就能从零开局通关 —— 它是"新会话 AI 不读源码也能玩通"的可复现证据。
所以这里既测策略状态机本身，也测它确实遵守了那些硬约束。
"""
import ast
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                              # noqa: E402
from tools.ladder_policy import PLAN, LadderPolicy       # noqa: E402
from tools.blind_test import BlindRun, load_subs         # noqa: E402
from ui.agent_api import build_report                    # noqa: E402
from ui.commands import CommandRouter                    # noqa: E402


def make(seed=7):
    eng = main.build_engine(seed=seed)
    router = CommandRouter(eng, load_subs())
    return eng, router, LadderPolicy()


class TestPolicyContent(unittest.TestCase):
    def test_plan_defs_exist(self):
        pol = LadderPolicy()
        for role in PLAN:
            self.assertIn(role["def_id"], pol.fac_defs,
                          f"PLAN 里的 {role['def_id']} 不在 facilities.json")

    def test_plan_recovery_gates_are_real(self):
        pol = LadderPolicy()
        for role in PLAN:
            rec = role.get("rec")
            if rec:
                self.assertIn(rec, pol.entries,
                              f"{role['key']} 的 {rec} 不在 recovery.json")
                self.assertIn(rec, pol.mainline_ids,
                              f"{role['key']} 的前置 {rec} 不是主线条目")

    def test_mainline_deps_are_satisfiable(self):
        pol = LadderPolicy()
        for eid in pol.mainline_ids:
            for dep in pol.entries[eid].get("depends_on", []):
                self.assertIn(dep, pol.entries, f"{eid} 依赖未知条目 {dep}")

    def test_targets_include_db_materials(self):
        eng, router, pol = make()
        rep = build_report(eng, router)
        targets = pol.targets(rep)
        # 数据库 5 项要 160 钢 / 65 合金 / 40 硫酸 / 25 氨
        self.assertGreater(targets.get("steel", 0), 100)
        self.assertGreater(targets.get("scrap_alloy", 0), 50)
        self.assertGreater(targets.get("sulfuric_acid", 0), 20)


class TestPolicyDecisions(unittest.TestCase):
    def test_first_move_is_power(self):
        eng, router, pol = make()
        act = pol.next_action(build_report(eng, router))
        self.assertIsNotNone(act)
        self.assertEqual(act["cmd"], "build HOME power_plant")

    def test_role_of_extractor_by_substance(self):
        eng, router, pol = make()
        rep = build_report(eng, router)
        plots = {p["id"]: p for p in rep["plots"]}
        fac = {"def": "extractor", "plot": "IRON1"}
        self.assertEqual(pol._role_of(fac, rep), "iron")
        rng = next(p["id"] for p in rep["plots"] if p["kind"] == "water")
        self.assertIsNotNone(plots[rng])
        self.assertEqual(pol._role_of({"def": "extractor", "plot": rng}, rep),
                         "water")

    def test_survey_ring_prefers_near_rings(self):
        pol = LadderPolicy()
        coal = pol._role("coal")
        sulfur = pol._role("sulfur")
        self.assertEqual(pol.survey_ring(coal), 1)
        # 硫磺只在圈3/4/5；不能因为圈5占比高就跑去圈5
        self.assertEqual(pol.survey_ring(sulfur), 3)

    def test_pick_fuel_prefers_byproduct(self):
        eng, router, pol = make()
        eng.economy.set("coal", 500.0)
        eng.economy.set("coalgas", 200.0)
        self.assertEqual(pol._pick_fuel(build_report(eng, router)), "coalgas")
        eng.economy.set("coalgas", 0.0)
        self.assertEqual(pol._pick_fuel(build_report(eng, router)), "coal")

    def test_vent_only_when_backlog_over(self):
        eng, router, pol = make()
        rep = build_report(eng, router)
        targets = pol.targets(rep)
        self.assertFalse(pol._needed(pol._role("vent"), rep, targets))
        eng.economy.set("coalgas", 9999.0)
        rep = build_report(eng, router)
        self.assertTrue(pol._needed(pol._role("vent"), rep,
                                    pol.targets(rep)))

    def test_power_throttling_when_coal_low(self):
        """电量够、煤少 → 电站不该被派员（省煤），这是开局的保命招。"""
        pol = LadderPolicy()
        low_coal = {"power": {"stored": 500.0}, "resources": {"coal": 60.0}}
        self.assertFalse(pol._power_needed(low_coal), "电量够 + 煤少 → 停机省煤")
        need = {"power": {"stored": 10.0}, "resources": {"coal": 60.0}}
        self.assertTrue(pol._power_needed(need), "电量见底 → 该开机")
        rich = {"power": {"stored": 500.0}, "resources": {"coal": 5000.0}}
        self.assertFalse(pol._power_needed(rich), "煤多时充满到 400 就够")
        rich2 = {"power": {"stored": 100.0}, "resources": {"coal": 5000.0}}
        self.assertTrue(pol._power_needed(rich2), "煤多但电量低 → 继续充")


class TestShortRun(unittest.TestCase):
    """短程实跑：策略能在真实引擎上推进，且不出异常、不越权。"""

    def test_early_game_progress(self):
        run = BlindRun(11, "ladder", max_minutes=20)
        pol = run.ladder
        for _ in range(120):
            rep = build_report(run.eng, run.router)
            act = pol.next_action(rep)
            if act is None:
                break
            cmd = act["cmd"]
            # 硬约束：策略不得使用调试/作弊手段
            self.assertNotIn("dbg", cmd)
            self.assertNotIn("spawn", cmd)
            if cmd.startswith("@tick"):
                run.advance(float(cmd.split()[1]))
            else:
                ok = run.exe(cmd)
                pol.note_result(cmd, ok)
                run.advance(10)
        rep = build_report(run.eng, run.router)
        defs = {f["def"] for f in rep["facilities"]}
        self.assertIn("power_plant", defs, "开局应先建电站")
        self.assertGreaterEqual(len(rep["plots"]), 4, "应已勘探出新地块")

    def test_never_uses_dbg_or_resource_injection(self):
        """策略源码里不允许出现 dbg 指令、资源注入或 core/systems 依赖。

        注意只看**代码**（AST 里的字符串常量与 import），不看注释/文档字符串 ——
        文档里解释"不用 dbg"是合理的。
        """
        path = os.path.join(ROOT, "tools", "ladder_policy.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        bad_cmds = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value.strip()
                if v.startswith("dbg") or "economy.set" in v \
                        or "economy.add" in v:
                    bad_cmds.append(v)
        self.assertEqual(bad_cmds, [], f"策略里不该有这些指令: {bad_cmds}")
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                self.assertFalse(m.startswith(("core", "systems")),
                                 f"策略不该依赖 {m}（只读 report 与 content）")
        # 也不该直接调用引擎内部接口
        for banned in ("instant_build", "install_instant", "add_unit",
                       "units.add_unit"):
            self.assertNotIn(banned, src, f"策略不该用内部接口 {banned}")


if __name__ == "__main__":
    unittest.main()
