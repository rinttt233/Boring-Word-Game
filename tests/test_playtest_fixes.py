# -*- coding: utf-8 -*-
"""试玩提案（MODIFIABLE_PARTS_SUMMARY.md）定向回归。

对照 §14 的四条定向验证 + 其余可测条目：
1. 死锁逃生：coal=0 / electricity=0 但有煤气 → 燃气发电机 + fuel 能救回来；
2. 文案区分：副产物积压限产要显示为 backlog，而不是"输入不足"；
3. 枯竭告警：煤矿采空要给"供给中断"的醒目告警；
4. 秒数一致：suggest 提示的临时窗口 = recovery 的真实 TTL；
外加：电网满/复电不再每几秒刷屏；report 新字段；agent_play 不从第 1 行消费。
"""
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                              # noqa: E402
from tools.agent_play import AgentPlay                    # noqa: E402
from ui.agent_api import build_report, suggest_actions    # noqa: E402
from ui.commands import CommandRouter                     # noqa: E402


def make(seed=3):
    with io.open(os.path.join(ROOT, "content", "substances.json"),
                 encoding="utf-8") as f:
        subs = {s["id"]: s for s in json.load(f)["substances"]}
    eng = main.build_engine(seed=seed)
    ind = eng.registry.get("industry")
    ind.instant_build = True
    eng.clock.resume()
    return eng, CommandRouter(eng, subs), ind


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def add_staff(eng, ind, plot_id, def_id):
    err = ind.install_instant(eng, plot_id, def_id)
    assert err is None, f"build {def_id}@{plot_id}: {err}"
    f = next(x for x in ind.facilities.values() if x.plot_id == plot_id)
    ind.assign(eng, f.id)
    return f


class TestDeadlockEscape(unittest.TestCase):
    """§14.1：煤电双 0 时，燃气发电机烧副产煤气能把电网救回来。"""

    def test_gas_generator_rescues_from_zero(self):
        eng, router, ind = make()
        eng.economy.set("coal", 0.0)
        eng.economy.set("electricity", 0.0)
        eng.economy.set("coalgas", 400.0)
        eng.economy.set("scrap_alloy", 60.0)
        f = add_staff(eng, ind, "HOME", "gas_generator")
        self.assertIsNone(ind.set_fuel(eng, f.id, "coalgas"))
        ticks(eng, 30.0)
        self.assertGreater(eng.economy.get("electricity"), 5.0,
                           "燃气发电机应把电网充起来（死锁逃生）")
        self.assertLess(eng.economy.get("coalgas"), 400.0)

    def test_gas_generator_needs_no_recovery(self):
        cfg = {f["id"]: f for f in json.load(
            io.open(os.path.join(ROOT, "content", "facilities.json"),
                    encoding="utf-8"))["facilities"]}
        self.assertNotIn("requires_recovery", cfg["gas_generator"],
                         "燃气发电机必须免科技 —— 它是唯一的免科技应急电源")

    def test_suggest_offers_gas_generator_when_coal_low(self):
        eng, router, ind = make()
        eng.economy.set("coal", 10.0)
        eng.economy.set("electricity", 200.0)
        eng.economy.set("scrap_alloy", 60.0)
        sugs = suggest_actions(eng, router, limit=8)
        cmds = " ".join(s["cmd"] for s in sugs)
        self.assertIn("gas_generator", cmds,
                      f"煤低时应建议建燃气发电机，实际：{cmds}")


class TestBacklogReason(unittest.TestCase):
    """§14.2：积压限产要写成 backlog，而不是"输入不足"。"""

    def test_throttled_facility_reports_backlog(self):
        eng, router, ind = make()
        eng.registry.get("recovery").status["db_coking"] = "permanent"
        eng.economy.set("coal", 5000.0)
        eng.economy.set("electricity", 400.0)
        f = add_staff(eng, ind, "HOME", "cokery")
        eng.economy.set("coalgas", 9999.0)      # 远超阈值 400
        eng.tick(1.0)
        self.assertIsNotNone(f.backlog_over, "应标记被积压限产")
        self.assertEqual(f.stall_code, "backlog")
        self.assertIn("积压", f.stall_reason or "")
        self.assertFalse(f.stalled_reported, "限产 ≠ 停摆（state 仍是 running）")
        rep = build_report(eng, router)
        self.assertTrue(any(t["id"] == f.id for t in rep["throttled"]),
                        "report.throttled 应列出被限产的设施")
        fac = next(x for x in rep["facilities"] if x["id"] == f.id)
        self.assertEqual(fac["reason_code"], "backlog")
        self.assertEqual(fac["state"], "running")

    def test_status_lists_throttled(self):
        eng, router, ind = make()
        eng.registry.get("recovery").status["db_coking"] = "permanent"
        eng.economy.set("coal", 5000.0)
        eng.economy.set("electricity", 400.0)
        add_staff(eng, ind, "HOME", "cokery")
        eng.economy.set("coalgas", 9999.0)
        eng.tick(1.0)
        router.execute("status")
        self.assertTrue(any("[积压]" in l for l in eng.log_lines),
                        "status 应给出 [积压] 行")


class TestDepleteWarning(unittest.TestCase):
    """§14.3：关键资源采空要给醒目告警。"""

    def test_coal_deplete_warns(self):
        eng, router, ind = make()
        eng.economy.set("electricity", 500.0)
        eng.world.spawn(ring=0, kind="ore", substance="coal", grade=78.0,
                        reserve=2.0, state="claimed")
        pid = [p.id for p in eng.world.plots.values()
               if p.substance == "coal"][-1]
        before = len(eng.log_lines)
        add_staff(eng, ind, pid, "extractor")
        ticks(eng, 20.0)
        self.assertEqual(eng.world.get(pid).state, "depleted")
        new = "\n".join(eng.log_lines[before:])
        self.assertIn("采空", new)
        self.assertIn("供给中断", new)

    def test_non_key_resource_deplete_is_quiet(self):
        eng, router, ind = make()
        eng.economy.set("electricity", 500.0)
        eng.world.spawn(ring=0, kind="ore", substance="nickel_ore", grade=2.0,
                        reserve=2.0, state="claimed")
        pid = [p.id for p in eng.world.plots.values()
               if p.substance == "nickel_ore"][-1]
        before = len(eng.log_lines)
        add_staff(eng, ind, pid, "extractor")
        ticks(eng, 20.0)
        self.assertNotIn("供给中断", "\n".join(eng.log_lines[before:]))


class TestCountdownConsistency(unittest.TestCase):
    """§14.4：suggest 的秒数必须等于 recovery 的真实窗口（别再写死 90s）。"""

    def test_suggest_uses_real_ttl(self):
        eng, router, ind = make()
        rec = eng.registry.get("recovery")
        # db_coking：恢复费 电30+合金5；固化费 电15+合金8
        # 让"恢复够、固化不够" → 触发"先攒够再 recover（窗口只有 Ns）"提示
        eng.economy.set("electricity", 200.0)
        eng.economy.set("scrap_alloy", 6.0)
        ttl = rec.ttl_of("db_coking")
        sugs = suggest_actions(eng, router, limit=10)
        txt = " ".join(s["why"] for s in sugs)
        self.assertIn(f"{ttl:.0f}s", txt, f"提示里应出现真实窗口 {ttl:.0f}s：{txt}")
        self.assertNotIn("90s", txt)

    def test_content_ttl_is_180(self):
        with io.open(os.path.join(ROOT, "content", "recovery.json"),
                     encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(float(cfg.get("temporary_ttl_default")), 180.0)


class TestGridFlapThrottle(unittest.TestCase):
    """§6：电网满/复电的抖动不能再刷爆日志。"""

    def test_grid_full_does_not_flap_every_tick(self):
        eng, router, ind = make()
        p = eng.registry.get("power")
        eng.economy.set("coal", 5000.0)
        eng.economy.set("electricity", float(p.capacity(eng)))   # 电网已满
        add_staff(eng, ind, "HOME", "power_plant")
        before = len(eng.log_lines)
        ticks(eng, 120.0)                       # 跑 2 分钟
        n = len(eng.log_lines) - before
        grid_lines = sum(1 for l in eng.log_lines[before:] if "电网已满" in l)
        self.assertLess(grid_lines, 8,
                        f"电网满的日志不该反复刷（2 分钟 {grid_lines} 条 / 共 {n} 行）")

    def test_grid_full_uses_hysteresis(self):
        eng, router, ind = make()
        p = eng.registry.get("power")
        self.assertGreater(p.grid_resume_ratio, p.grid_full_ratio,
                           "恢复阈值必须高于判满阈值（滞后）")
        eng.economy.set("electricity", float(p.capacity(eng)))
        self.assertTrue(p.grid_full(eng, "F1"))
        # 只放掉一点点（仍低于恢复阈值）→ 仍算满
        eng.economy.set("electricity", p.capacity(eng) * 0.95)
        self.assertTrue(p.grid_full(eng, "F1"))
        eng.economy.set("electricity", p.capacity(eng) * 0.5)
        self.assertFalse(p.grid_full(eng, "F1"))


class TestReportFields(unittest.TestCase):
    """§10/§13：report 增补的可观测性字段。"""

    def test_new_summary_fields(self):
        eng, router, ind = make()
        rep = build_report(eng, router)
        for k in ("plots_free_empty", "depleting", "throttled", "flow"):
            self.assertIn(k, rep, f"report 缺字段 {k}")
        self.assertIn("HOME", rep["plots_free_empty"], "HOME 是可用空地")

    def test_flow_has_net_for_key_resources(self):
        eng, router, ind = make()
        add_staff(eng, ind, "HOME", "power_plant")
        ticks(eng, 20.0)
        rep = build_report(eng, router)
        self.assertIn("electricity", rep["flow"])
        self.assertGreater(rep["flow"]["electricity"]["produce"], 0.0)
        self.assertIn("coal", rep["flow"])
        self.assertGreater(rep["flow"]["coal"]["consume"], 0.0)

    def test_depleting_lists_low_reserve_mines(self):
        eng, router, ind = make()
        eng.economy.set("electricity", 500.0)
        eng.world.spawn(ring=0, kind="ore", substance="iron_ore", grade=51.0,
                        reserve=120.0, state="claimed")
        pid = [p.id for p in eng.world.plots.values()
               if p.substance == "iron_ore"][-1]
        add_staff(eng, ind, pid, "extractor")
        rep = build_report(eng, router)
        self.assertTrue(any(d["id"] == pid for d in rep["depleting"]),
                        "低储量矿脉应出现在 depleting 里")


class TestAgentPlayProtocol(unittest.TestCase):
    """§12：不能从 in 文件第 1 行消费；out 文件默认追加。"""

    def _shell(self, tmp, in_text):
        inp = os.path.join(tmp, "ai_in.txt")
        outp = os.path.join(tmp, "ai_out.txt")
        with io.open(inp, "w", encoding="utf-8") as f:
            f.write(in_text)
        eng = main.build_engine(seed=1)
        with io.open(os.path.join(ROOT, "content", "substances.json"),
                     encoding="utf-8") as f:
            subs = {s["id"]: s for s in json.load(f)["substances"]}
        return AgentPlay(eng, subs, inp, outp, None), inp, outp

    def test_skips_preexisting_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell, inp, outp = self._shell(tmp, "@quit\n@view\n")
            self.assertEqual(shell.consumed, 2,
                             "启动前已有的行要跳过（否则残留 @quit 会立刻退出）")
            with io.open(outp, encoding="utf-8") as f:
                self.assertIn("已跳过", f.read())

    def test_replay_flag_reads_from_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "ai_in.txt")
            outp = os.path.join(tmp, "ai_out.txt")
            with io.open(inp, "w", encoding="utf-8") as f:
                f.write("@view\n")
            eng = main.build_engine(seed=1)
            with io.open(os.path.join(ROOT, "content", "substances.json"),
                         encoding="utf-8") as f:
                subs = {s["id"]: s for s in json.load(f)["substances"]}
            shell = AgentPlay(eng, subs, inp, outp, None, replay=True)
            self.assertEqual(shell.consumed, 0)

    def test_out_file_is_appended(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell, inp, outp = self._shell(tmp, "")
            first = io.open(outp, encoding="utf-8").read()
            shell2 = AgentPlay(shell.engine, shell.subs, inp, outp, None)
            second = io.open(outp, encoding="utf-8").read()
            self.assertIn(first.strip().splitlines()[-1], second)
            self.assertGreater(len(second), len(first))


if __name__ == "__main__":
    unittest.main()
