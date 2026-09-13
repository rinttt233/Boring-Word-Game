"""批次3 测试：副产物积压限产 + 4 种应对（转化/放空/回注/政策开关）+ 观测接口。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main                                          # noqa: E402
from core.engine import Engine                       # noqa: E402
from systems.industry import IndustrySystem          # noqa: E402
from ui.agent_api import build_report, suggest_actions   # noqa: E402
from ui.commands import CommandRouter                # noqa: E402

FACS = [
    {"id": "cokery", "name": "炼焦炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "coke", "slots": 1, "build_time": 5.0},
    {"id": "vent", "name": "放空塔", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "vent", "vent_substances": ["gas", "tar"],
     "vent_rate": 1.0, "power_use": 0.0, "slots": 1, "build_time": 5.0},
    {"id": "well", "name": "回注井", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "sink", "sink_substances": ["tar"],
     "sink_rate": 2.0, "capacity": 100.0, "power_use": 0.0, "slots": 1,
     "build_time": 5.0},
    {"id": "shift", "name": "变换炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "shift", "slots": 1, "build_time": 5.0},
]
RECIPES = [
    {"id": "coke", "name": "炼焦", "inputs": {"coal": 0.5},
     "outputs": {"coke": 0.35}, "byproducts": {"gas": 0.25, "tar": 0.05}},
    {"id": "shift", "name": "水煤气变换", "inputs": {"gas": 0.6},
     "outputs": {"hydrogen": 0.35}},
]
BACKLOG = {"limits": {"gas": 10.0, "tar": 5.0}, "min_factor": 0.2,
           "policy_default": "throttle"}


def make(coal=10000.0, plots=6, policy=None):
    eng = Engine()
    eng.economy.set("coal", coal)
    eng.economy.set("electricity", 1000.0)
    eng.economy.set("gas", 0.0)
    eng.economy.set("tar", 0.0)
    for i in range(3):
        eng.units.add_unit(f"U{i + 1}")
    en = [{"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}]
    for i in range(plots):
        en.append({"id": f"P{i}", "ring": 0, "kind": "empty",
                   "state": "claimed"})
    eng.bootstrap_world(en)
    cfg = dict(BACKLOG)
    if policy:
        cfg["policy_default"] = policy
    ind = IndustrySystem(FACS, RECIPES, backlog=cfg)
    ind.instant_build = True
    eng.registry.register("industry", ind)
    eng.start()
    return eng, ind


def ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def run_cokery(eng, ind, plot="HOME"):
    ind.build(eng, plot, "cokery")
    fid = [f.id for f in ind.facilities.values() if f.plot_id == plot][-1]
    ind.assign(eng, fid)
    return fid


class TestBacklogThrottle(unittest.TestCase):
    def test_over_limit_throttles_production(self):
        eng, ind = make()
        run_cokery(eng, ind, "HOME")
        eng.economy.set("gas", 20.0)          # 超过 limit 10
        coke0 = eng.economy.get("coke")
        ticks(eng, 10.0)
        # 限产系数随时间变化（积压还在增长），取区间断言而非精确值
        got = (eng.economy.get("coke") - coke0) / 10.0
        self.assertLess(got, 0.35 * 0.55)
        self.assertGreater(got, 0.35 * 0.35)
        f = ind.facilities[list(ind.facilities)[0]]
        self.assertIsNotNone(f.backlog_over)

    def test_factor_floor(self):
        eng, ind = make()
        run_cokery(eng, ind, "HOME")
        eng.economy.set("gas", 1000.0)         # 10/1000 → 夹到 0.2
        self.assertAlmostEqual(
            ind.backlog_factor(eng, RECIPES[0]), 0.2, places=3)

    def test_policy_ignore_disables(self):
        eng, ind = make(policy="ignore")
        run_cokery(eng, ind, "HOME")
        eng.economy.set("gas", 500.0)
        coke0 = eng.economy.get("coke")
        ticks(eng, 10.0)
        self.assertAlmostEqual((eng.economy.get("coke") - coke0) / 10.0,
                               0.35, places=3)

    def test_recovery_from_backlog(self):
        eng, ind = make()
        fid = run_cokery(eng, ind, "HOME")
        eng.economy.set("gas", 50.0)
        ticks(eng, 5.0)
        self.assertIsNotNone(ind.facilities[fid].backlog_over)
        eng.economy.set("gas", 1.0)            # 消化掉
        ticks(eng, 5.0)
        self.assertIsNone(ind.facilities[fid].backlog_over)

    def test_policy_command(self):
        eng, ind = make()
        router = CommandRouter(eng, {})
        router.execute("policy")
        self.assertIn("throttle", "\n".join(eng.log_lines[-3:]))
        router.execute("policy backlog ignore")
        self.assertEqual(ind.backlog_policy, "ignore")
        router.execute("policy backlog bad")
        self.assertIn("可选值", eng.log_lines[-1])


class TestFourCountermeasures(unittest.TestCase):
    def test_vent_destroys_byproduct(self):
        eng, ind = make()
        eng.economy.set("gas", 50.0)
        ind.build(eng, "HOME", "vent")
        ind.assign(eng, "F1")
        ticks(eng, 10.0)
        self.assertAlmostEqual(eng.economy.get("gas"), 40.0, places=2)
        self.assertGreater(eng.stats.get("vented"), 0.0)

    def test_sink_stores_then_stalls_when_full(self):
        eng, ind = make()
        eng.economy.set("tar", 500.0)
        ind.build(eng, "HOME", "well")
        fid = [f.id for f in ind.facilities.values()][0]
        ind.assign(eng, fid)
        ticks(eng, 200.0)
        f = ind.facilities[fid]
        self.assertAlmostEqual(f.stored, 100.0, places=2)   # capacity
        self.assertTrue(f.stalled_reported)
        self.assertEqual(f.stall_code, "sink_full")
        self.assertGreater(eng.stats.get("sunk"), 0.0)

    def test_conversion_consumes_byproduct(self):
        eng, ind = make()
        eng.economy.set("gas", 40.0)
        ind.build(eng, "HOME", "shift")
        ind.assign(eng, "F1")
        h0 = eng.economy.get("hydrogen")
        ticks(eng, 10.0)
        self.assertLess(eng.economy.get("gas"), 40.0)
        self.assertGreater(eng.economy.get("hydrogen"), h0)

    def test_report_and_suggest_expose_backlog(self):
        eng, ind = make()
        run_cokery(eng, ind, "HOME")
        eng.economy.set("gas", 30.0)
        ticks(eng, 2.0)
        router = CommandRouter(eng, {})
        rep = build_report(eng, router)
        self.assertIn("gas", rep["backlog"])
        self.assertTrue(rep["backlog"]["gas"]["over"])
        sugs = suggest_actions(eng, router, limit=12)
        self.assertTrue(any(s["cmd"].startswith("build")
                            for s in sugs), f"应建议给副产物找出路: {sugs}")
        self.assertTrue(any("积压" in s["why"] for s in sugs))

    def test_policy_persisted_in_save(self):
        eng, ind = make(policy="ignore")
        data = eng.to_dict()
        e2 = main.build_engine(seed=1)
        e2.from_dict(data)
        self.assertEqual(e2.registry.get("industry").backlog_policy, "ignore")


class TestRealContentBacklog(unittest.TestCase):
    def test_real_content_has_limits_and_countermeasures(self):
        eng = main.build_engine(seed=1)
        ind = eng.registry.get("industry")
        self.assertIn("coalgas", ind.backlog_limits)
        for fid in ("vent_tower", "injection_well", "gas_shift"):
            self.assertIn(fid, ind.defs, f"缺少应对设施 {fid}")
        st = ind.backlog_status(eng)
        self.assertTrue(all(not v["over"] for v in st.values()))

    def test_vent_clears_real_coalgas(self):
        eng = main.build_engine(seed=1)
        ind = eng.registry.get("industry")
        rec = eng.registry.get("recovery")
        rec.status["db_coking"] = "permanent"
        eng.economy.set("scrap_alloy", 200.0)
        eng.economy.set("electricity", 5000.0)
        eng.economy.set("coalgas", 500.0)
        ind.instant_build = True
        self.assertIsNone(ind.build(eng, "HOME", "vent_tower"))
        fid = [f.id for f in ind.facilities.values()][0]
        self.assertIsNone(ind.assign(eng, fid))
        self.assertTrue(ind.backlog_status(eng)["coalgas"]["over"])
        t = 0.0
        while t < 150.0:            # 1.5/s × 150s = 225 → 500-225 < 400
            eng.tick(0.5)
            t += 0.5
        self.assertLess(eng.economy.get("coalgas"), 500.0)
        self.assertFalse(ind.backlog_status(eng)["coalgas"]["over"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
