"""M1 最小闭环测试：勘探→扩展→开采→生产 + 执行单元调度。

headless 驱动：直接调 engine.tick，不依赖 UI 与真实时钟。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine           # noqa: E402
from core.world import Plot              # noqa: E402
from systems.claim import ClaimSystem    # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402
from systems.survey import SurveySystem  # noqa: E402


def make_engine():
    eng = Engine()
    eng.economy.set("scrap_alloy", 100.0)
    eng.economy.set("coal", 200.0)
    eng.economy.set("water", 500.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"},
        {"id": "RIVER", "ring": 0, "kind": "water", "state": "known"},
        {"id": "IRON1", "ring": 0, "kind": "ore", "substance": "iron_ore",
         "grade": 58.0, "reserve": 1000.0, "state": "known"},
    ])
    facs = [
        {"id": "power_plant", "name": "燃煤发电站", "allowed_plot_kinds": ["empty"],
         "build_cost": {"scrap_alloy": 15.0}, "recipe": "burn_coal", "slots": 1},
        {"id": "extractor", "name": "采矿机", "allowed_plot_kinds": ["ore", "water"],
         "build_cost": {"scrap_alloy": 10.0}, "extract_rate": 0.5,
         "power_use": 0.1, "slots": 1},
        {"id": "salvager", "name": "残骸回收站", "allowed_plot_kinds": ["wreck"],
         "build_cost": {}, "extract_rate": 0.4, "power_use": 0.05, "slots": 1,
         "reward_unit_on_deplete": True},
    ]
    recipes = [
        {"id": "burn_coal", "name": "燃煤发电",
         "inputs": {"coal": 0.4}, "outputs": {"electricity": 1.6}},
    ]
    eng.registry.register("survey", SurveySystem({
        "regions": [{"ring": 1, "name": "丘陵", "pools": [
            {"kind": "ore", "substance": "coal", "weight": 1,
             "grade": [80, 80], "reserve": [900, 900]}]}],
        "surveys_per_ring": 3}))
    eng.registry.register("claim", ClaimSystem())
    ind = IndustrySystem(facs, recipes)
    ind.instant_build = True          # 本文件测生产闭环；建造耗时见 test_build_time
    eng.registry.register("industry", ind)
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestSurvey(unittest.TestCase):
    def test_survey_discovers_plot(self):
        eng = make_engine()
        before = len(eng.world.plots)
        err = eng.registry.get("survey").survey(eng, 1)
        self.assertIsNone(err)
        self.assertEqual(eng.jobs.count(), 1)
        # 作业需 ~8s（4+1*4）
        run_ticks(eng, 12)
        self.assertEqual(eng.jobs.count(), 0)
        self.assertEqual(len(eng.world.plots), before + 1)
        new = [p for p in eng.world.plots.values()
               if p.state == Plot.STATE_KNOWN]
        self.assertTrue(new)

    def test_survey_requires_idle_unit(self):
        eng = make_engine()
        # 占满两个单元
        eng.units.units[0].assign("a")
        eng.units.units[1].assign("b")
        err = eng.registry.get("survey").survey(eng, 1)
        self.assertIsNotNone(err)
        self.assertIn("没有空闲", err)


class TestClaimBuild(unittest.TestCase):
    def test_claim_and_build(self):
        eng = make_engine()
        claim = eng.registry.get("claim")
        # IRON1 已知未占领
        err = claim.claim(eng, "IRON1")
        self.assertIsNone(err)
        run_ticks(eng, 6)   # 3 + 0*2
        plot = eng.world.get("IRON1")
        self.assertEqual(plot.state, Plot.STATE_CLAIMED)
        # 建厂
        ind = eng.registry.get("industry")
        err = ind.build(eng, "IRON1", "extractor")
        self.assertIsNone(err)
        self.assertEqual(len(ind.facilities), 1)
        self.assertEqual(plot.state, Plot.STATE_DEVELOPED)
        # 建在未占领地应失败
        err = ind.build(eng, "RIVER", "extractor")
        self.assertIsNotNone(err)
        # 在空地建 extractor 应失败
        err = ind.build(eng, "HOME", "extractor")
        self.assertIsNotNone(err)


class TestProduction(unittest.TestCase):
    def test_power_and_extraction_loop(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        claim = eng.registry.get("claim")
        # 1) HOME 建发电站
        ind.build(eng, "HOME", "power_plant")
        err = ind.assign(eng, "F1")
        self.assertIsNone(err)
        # 2) 占领并开采 IRON1
        claim.claim(eng, "IRON1")
        run_ticks(eng, 6)
        ind.build(eng, "IRON1", "extractor")
        err = ind.assign(eng, "F2")
        self.assertIsNone(err)
        self.assertEqual(eng.units.count_idle(), 0)   # 两单元全忙
        # 3) 跑 30s：发电站耗煤产电，采矿机耗电产铁
        coal0 = eng.economy.get("coal")
        elec0 = eng.economy.get("electricity")
        ore0 = eng.economy.get("iron_ore")
        reserve0 = eng.world.get("IRON1").reserve
        run_ticks(eng, 30)
        self.assertGreater(eng.economy.get("electricity"), elec0)
        self.assertGreater(eng.economy.get("iron_ore"), ore0)
        self.assertLess(eng.world.get("IRON1").reserve, reserve0)
        self.assertLess(eng.economy.get("coal"), coal0)
        # 利用率应满
        self.assertAlmostEqual(eng.units.utilization(), 1.0)

    def test_no_unit_no_production(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "power_plant")
        elec0 = eng.economy.get("electricity")
        run_ticks(eng, 20)
        self.assertAlmostEqual(eng.economy.get("electricity"), elec0)  # 不产

    def test_stall_when_input_missing(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "power_plant")
        ind.assign(eng, "F1")
        eng.economy.set("coal", 0.0)     # 断煤
        run_ticks(eng, 5)
        self.assertAlmostEqual(eng.economy.get("electricity"), 0.0)

    def test_depletion_rewards_unit(self):
        eng = make_engine()
        eng.world.spawn(ring=0, kind="wreck", substance="scrap_alloy",
                        grade=50, reserve=2.0, state="claimed")
        wreck_id = [p.id for p in eng.world.plots.values()
                    if p.kind == "wreck"][0]
        ind = eng.registry.get("industry")
        # 直接给电避免搭发电站
        eng.economy.set("electricity", 1000.0)
        ind.build(eng, wreck_id, "salvager")
        ind.assign(eng, "F1")
        n0 = eng.units.count()
        run_ticks(eng, 10)    # 2/0.4=5s 枯竭
        self.assertEqual(eng.units.count(), n0 + 1)
        self.assertEqual(eng.world.get(wreck_id).state, Plot.STATE_DEPLETED)
        self.assertEqual(len(ind.facilities), 0)
        # 拆除后单元应释放回池
        self.assertGreaterEqual(eng.units.count_idle(), 1)


class TestSaveLoad(unittest.TestCase):
    def test_roundtrip_with_facilities(self):
        eng = make_engine()
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "power_plant")
        ind.assign(eng, "F1")
        eng.jobs.add("survey", "U2", "ring1", 5.0, {"ring": 1})
        data = eng.to_dict()
        eng2 = make_engine()
        eng2.from_dict(data)
        self.assertEqual(len(eng2.registry.get("industry").facilities), 1)
        f = list(eng2.registry.get("industry").facilities.values())[0]
        self.assertEqual(f.assigned, ["U1"])
        # 作业与单元状态恢复
        self.assertEqual(eng2.jobs.count(), 1)
        self.assertEqual(eng2.units.get("U1").task, "F1")
        # 读档后继续推进，生产依旧工作
        elec0 = eng2.economy.get("electricity")
        run_ticks(eng2, 10)
        self.assertGreater(eng2.economy.get("electricity"), elec0)


if __name__ == "__main__":
    unittest.main()
