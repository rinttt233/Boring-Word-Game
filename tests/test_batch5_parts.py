# -*- coding: utf-8 -*-
"""批次5 回收/拆解生态测试（A6 部件加工链 + B4 配方改造 + B5 拆解返还）。

A6：残骸不再"拆空白给一个执行单元"，改为拆出**回收部件**；
B4：部件经加工链变成 执行单元 / 维护件 / 合金+钢；
B5：`demolish` 返还 50% 建造成本（P1 ③ 已落地，这里补一条回归）。
"""
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                              # noqa: E402
from ui.agent_api import build_report                    # noqa: E402
from ui.commands import CommandRouter                    # noqa: E402


def content(name):
    with io.open(os.path.join(ROOT, "content", name), encoding="utf-8") as f:
        return json.load(f)


def make(seed=3):
    with io.open(os.path.join(ROOT, "content", "substances.json"),
                 encoding="utf-8") as f:
        subs = {s["id"]: s for s in json.load(f)["substances"]}
    eng = main.build_engine(seed=seed)
    ind = eng.registry.get("industry")
    ind.instant_build = True
    eng.clock.resume()
    eng.economy.set("electricity", 2000.0)
    for res, amt in (("steel", 500.0), ("copper", 200.0), ("scrap_alloy", 500.0),
                     ("salvage_part", 500.0), ("coal", 5000.0)):
        eng.economy.set(res, amt)
    # WRECK1 出生是 known，建回收站前要先占领
    eng.registry.get("claim").claim(eng, "WRECK1")
    ticks(eng, 12.0)
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


class TestContentA6(unittest.TestCase):
    def test_salvager_no_longer_gives_free_unit(self):
        facs = {f["id"]: f for f in content("facilities.json")["facilities"]}
        self.assertNotIn("reward_unit_on_deplete", facs["salvager"],
                         "A6：残骸拆空不再白给执行单元")
        self.assertEqual(facs["salvager"].get("extract_extra"),
                         {"salvage_part": 0.3}, "残骸应附带拆出回收部件")

    def test_new_substance_exists(self):
        ids = {s["id"] for s in content("substances.json")["substances"]}
        self.assertIn("salvage_part", ids)

    def test_parts_chain_recipes(self):
        rec = {r["id"]: r for r in content("recipes.json")["recipes"]}
        # 单元：部件 + 钢 + 铜 + 电
        self.assertIn("salvage_part", rec["assemble_unit"]["inputs"])
        # 维护件：部件替代部分钢/合金
        self.assertIn("salvage_part", rec["make_maintenance"]["inputs"])
        # 出口：部件 → 合金 + 钢
        self.assertEqual(rec["recycle_parts"]["outputs"],
                         {"scrap_alloy": 0.22, "steel": 0.08})

    def test_parts_works_facility(self):
        facs = {f["id"]: f for f in content("facilities.json")["facilities"]}
        w = facs["parts_works"]
        self.assertEqual(w["recipe"], "recycle_parts")
        self.assertEqual(w["requires_recovery"], "db_steel")


class TestWreckExtraction(unittest.TestCase):
    def test_wreck_yields_alloy_and_parts(self):
        """残骸回收：合金与部件按 1 : 0.3 一起出。"""
        eng, router, ind = make()
        p0 = eng.economy.get("salvage_part")
        f = add_staff(eng, ind, "WRECK1", "salvager")
        self.assertEqual(f.def_id, "salvager")
        ticks(eng, 60.0)
        alloy = eng.economy.get("scrap_alloy") - 500.0
        parts = eng.economy.get("salvage_part") - p0
        self.assertGreater(alloy, 30.0, "应采出合金")
        self.assertGreater(parts, 5.0, "应拆出回收部件")
        self.assertAlmostEqual(parts / alloy, 0.3, delta=0.02,
                               msg="部件应为主产物的 30%")

    def test_deplete_gives_no_unit_but_keeps_parts(self):
        eng, router, ind = make()
        # 造一块极小残骸，拆到枯竭
        eng.world.spawn(ring=0, kind="wreck", substance="scrap_alloy",
                        grade=55.0, reserve=3.0, state="claimed")
        wid = [p.id for p in eng.world.plots.values() if p.kind == "wreck"][-1]
        add_staff(eng, ind, wid, "salvager")
        n0 = eng.units.count()
        parts0 = eng.economy.get("salvage_part")
        ticks(eng, 30.0)
        self.assertEqual(eng.world.get(wid).state, "depleted")
        self.assertEqual(eng.units.count(), n0, "枯竭不该再奖励执行单元（A6）")
        self.assertGreater(eng.economy.get("salvage_part"), parts0)


class TestPartsChain(unittest.TestCase):
    def test_unit_factory_consumes_parts(self):
        eng, router, ind = make()
        ind.instant_build = True
        rec = eng.registry.get("recovery")
        rec.status["db_units"] = "permanent"
        rec.status["db_steel"] = "permanent"
        rec.status["db_copper"] = "permanent"
        f = add_staff(eng, ind, "HOME", "unit_factory")
        p0 = eng.economy.get("salvage_part")
        n0 = eng.units.count()
        ticks(eng, 230.0)
        self.assertLess(eng.economy.get("salvage_part"), p0,
                        "装配厂应消耗回收部件")
        self.assertGreaterEqual(eng.units.count(), n0 + 1,
                                "约 200 秒应装出 1 个执行单元")

    def test_maintenance_uses_parts(self):
        eng, router, ind = make()
        rec = eng.registry.get("recovery")
        rec.status["db_upkeep"] = "permanent"
        f = add_staff(eng, ind, "HOME", "maintenance_depot")
        p0 = eng.economy.get("salvage_part")
        ticks(eng, 20.0)
        self.assertLess(eng.economy.get("salvage_part"), p0)
        self.assertGreater(eng.economy.get("maintenance_kit"), 0.0)

    def test_recycle_parts_back_to_alloy_and_steel(self):
        eng, router, ind = make()
        rec = eng.registry.get("recovery")
        rec.status["db_steel"] = "permanent"
        f = add_staff(eng, ind, "HOME", "parts_works")
        p0 = eng.economy.get("salvage_part")
        a0 = eng.economy.get("scrap_alloy")
        s0 = eng.economy.get("steel")
        ticks(eng, 20.0)
        self.assertLess(eng.economy.get("salvage_part"), p0)
        self.assertGreater(eng.economy.get("scrap_alloy"), a0)
        self.assertGreater(eng.economy.get("steel"), s0)

    def test_part_shows_up_in_report(self):
        eng, router, ind = make()
        add_staff(eng, ind, "WRECK1", "salvager")
        ticks(eng, 30.0)
        rep = build_report(eng, router)
        self.assertIn("salvage_part", rep["resources"])


class TestDemolishRefund(unittest.TestCase):
    def test_demolish_returns_half(self):
        """B5 回归：`demolish` 返还一半建造成本（P1 ③ 已落地）。"""
        eng, router, ind = make()
        a0 = eng.economy.get("scrap_alloy")
        f = add_staff(eng, ind, "HOME", "power_plant")
        cost = ind.defs["power_plant"]["build_cost"]["scrap_alloy"]
        a1 = eng.economy.get("scrap_alloy")
        self.assertAlmostEqual(a1, a0 - cost, delta=0.01)
        self.assertIsNone(ind.demolish(eng, f.id, refund=0.5))
        self.assertAlmostEqual(eng.economy.get("scrap_alloy"), a1 + cost * 0.5,
                               delta=0.01)


if __name__ == "__main__":
    unittest.main()
