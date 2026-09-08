"""勘探圈内补偿测试：稀缺类型(相对配置占比)权重上调、富余回落。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine                 # noqa: E402
from core.world import Plot                    # noqa: E402
from systems.survey import SurveySystem        # noqa: E402


def make_cfg():
    return {"regions": [{"ring": 1, "name": "丘陵", "pools": [
        {"kind": "empty", "weight": 90},
        {"kind": "ore", "substance": "coal", "weight": 10,
         "grade": [80, 80], "reserve": [5000, 5000]},
    ]}], "surveys_per_ring": 3}


def make_engine():
    eng = Engine()
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    s = SurveySystem(make_cfg())
    eng.registry.register("survey", s)
    eng.start()
    return eng, s


def weights_map(weighted):
    out = {}
    for p, w in weighted:
        out[(p.get("kind"), p.get("substance"))] = w
    return out


class TestSurveyCompensation(unittest.TestCase):
    def test_empty_scarce_gets_boosted(self):
        """圈内已有大量矿、却一块空地都无 → 空地权重应被上调。"""
        eng, s = make_engine()
        # 预置：圈1 已可见 9 块煤矿、0 块空地
        for i in range(9):
            eng.world.spawn(ring=1, kind="ore", substance="coal",
                            grade=80, reserve=5000,
                            state=Plot.STATE_KNOWN)
        cfg = s.ring_cfg(1)
        w = weights_map(s._adjusted_pool(eng, 1, cfg))
        base_empty = 90.0
        self.assertGreater(w[("empty", None)], base_empty,
                           "空地稀缺时权重应上调")

    def test_ore_surplus_gets_reduced(self):
        """空地占绝大多数、矿占比远低于配置 → 矿权重上调。"""
        eng, s = make_engine()
        for i in range(9):
            eng.world.spawn(ring=1, kind="empty",
                            state=Plot.STATE_KNOWN)
        cfg = s.ring_cfg(1)
        w = weights_map(s._adjusted_pool(eng, 1, cfg))
        base_ore = 10.0
        self.assertGreater(w[("ore", "coal")], base_ore,
                           "矿稀缺时权重应上调")

    def test_many_empty_plot_pool_pulls_back_to_empty(self):
        """圈1 配置空地上限高；即使已有不少空地，若仍低于配置占比应保持不降。"""
        eng, s = make_engine()
        # 配置：empty 90/100 → 目标 90%。已见 4 空 1 矿 → 实际 80% 仍偏低
        for i in range(4):
            eng.world.spawn(ring=1, kind="empty",
                            state=Plot.STATE_KNOWN)
        eng.world.spawn(ring=1, kind="ore", substance="coal",
                        grade=80, reserve=5000, state=Plot.STATE_KNOWN)
        cfg = s.ring_cfg(1)
        w = weights_map(s._adjusted_pool(eng, 1, cfg))
        # 实际 80% < 目标 90% → 空地权重仍 ≥ 基础权重
        self.assertGreaterEqual(w[("empty", None)], 90.0 - 1e-9)

    def test_roll_early_compensates_shortage(self):
        """早期补偿：圈内空地产量远低于配置(50/50 配比)时，勘探应先补空地。

        注意：本机制会把圈内配比"收敛回配置值"，因此只看早期(前30次)
        是否显著偏向稀缺类型，而非全程占比。
        """
        import random
        cfg = {"regions": [{"ring": 1, "name": "丘陵", "pools": [
            {"kind": "empty", "weight": 50},
            {"kind": "ore", "substance": "coal", "weight": 50,
             "grade": [80, 80], "reserve": [5000, 5000]},
        ]}], "surveys_per_ring": 3}
        eng = Engine()
        eng.units.add_unit("执行器-α")
        s = SurveySystem(cfg)
        eng.registry.register("survey", s)
        eng.start()
        # 预置严重失衡：90 矿 10 空地(配置目标本应 50/50)
        for i in range(90):
            eng.world.spawn(ring=1, kind="ore", substance="coal",
                            grade=80, reserve=5000,
                            state=Plot.STATE_KNOWN)
        for i in range(10):
            eng.world.spawn(ring=1, kind="empty",
                            state=Plot.STATE_KNOWN)
        s._rng = random.Random(3)
        rc = s.ring_cfg(1)
        first = [s._roll_plot(eng, 1, rc).kind for _ in range(30)]
        empty_hits = first.count("empty")
        # 无补偿基线 = 50%(15/30)；补偿后稀缺的空地应显著 > 50%
        self.assertGreater(empty_hits, 19, f"早期空地补偿不足: {empty_hits}/30")


if __name__ == "__main__":
    unittest.main()
