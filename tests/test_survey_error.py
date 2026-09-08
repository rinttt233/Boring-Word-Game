"""勘探情报误差测试：估值带误差、开采逐步揭示、存档往返。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine           # noqa: E402
from core.world import Plot              # noqa: E402
from systems.survey import SurveySystem  # noqa: E402


def make_engine(survey_seed=42):
    eng = Engine()
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    regions = {"regions": [
        {"ring": 1, "name": "丘陵", "pools": [
            {"kind": "ore", "substance": "iron_ore", "weight": 1,
             "grade": [50, 50], "reserve": [1000, 1000]}]}],
        "surveys_per_ring": 3}
    s = SurveySystem(regions)
    s._rng = __import__("random").Random(survey_seed)
    eng.registry.register("survey", s)
    eng.start()
    return eng, s


class TestSurveyError(unittest.TestCase):
    def test_survey_creates_estimate(self):
        eng, s = make_engine()
        # 直接 roll 一个地块（绕过作业，验证估值逻辑）
        cfg = s.ring_cfg(1)
        plot = s._roll_plot(eng, 1, cfg)
        self.assertTrue(plot.surveyed)
        self.assertIsNotNone(plot.known_grade)
        self.assertIsNotNone(plot.known_reserve)
        self.assertGreater(plot.grade_err, 0)
        self.assertGreater(plot.reserve_err, 0)
        # 真值不变（估值围绕真值浮动）
        self.assertEqual(plot.grade, 50.0)
        self.assertEqual(plot.reserve, 1000.0)
        # 估值应在真值 ± 误差范围内
        self.assertLessEqual(
            abs(plot.known_grade - plot.grade) / plot.grade,
            plot.grade_err + 1e-9)

    def test_describe_uses_estimate(self):
        eng, s = make_engine()
        cfg = s.ring_cfg(1)
        plot = s._roll_plot(eng, 1, cfg)
        desc = plot.describe()
        self.assertIn("~", desc)          # 用 ~ 表示估值
        self.assertNotIn("品位50", desc)   # 不出真值

    def test_refine_converges_to_true(self):
        eng, s = make_engine()
        cfg = s.ring_cfg(1)
        plot = s._roll_plot(eng, 1, cfg)
        initial_err = abs(plot.known_grade - plot.grade)
        # 多次 refine 后误差应减小（向真值收敛）
        before = abs(plot.known_grade - plot.grade)
        for _ in range(20):
            plot.refine(grade_frac=0.25, reserve_frac=0.25)
        after = abs(plot.known_grade - plot.grade)
        self.assertLess(after, before + 1e-9)
        self.assertLess(after, initial_err)

    def test_roundtrip(self):
        eng, s = make_engine()
        cfg = s.ring_cfg(1)
        plot = s._roll_plot(eng, 1, cfg)
        data = plot.to_dict()
        p2 = Plot.from_dict(data)
        self.assertEqual(p2.known_grade, plot.known_grade)
        self.assertEqual(p2.surveyed, plot.surveyed)
        self.assertAlmostEqual(p2.grade_err, plot.grade_err)

    def test_survey_error_grows_with_ring(self):
        """圈层越大误差越大。"""
        from core.world import Plot
        eng = Engine()
        regions = {"regions": [
            {"ring": r, "name": f"r{r}", "pools": [
                {"kind": "ore", "substance": "coal", "weight": 1,
                 "grade": [80, 80], "reserve": [5000, 5000]}]}
            for r in range(1, 6)]}
        s = SurveySystem(regions)
        s._rng = __import__("random").Random(7)
        errs = []
        for ring in range(1, 6):
            plot = s._roll_plot(eng, ring, s.ring_cfg(ring))
            errs.append(plot.grade_err)
        self.assertLess(errs[0], errs[4])   # 圈5误差 > 圈1


if __name__ == "__main__":
    unittest.main()
