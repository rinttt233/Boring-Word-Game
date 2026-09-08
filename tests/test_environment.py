"""M4 拓展性验收测试。

验收标准：新增第 5 模块（行星环境）零内核改动 —— 只注册进 registry，
并让 industry/memory 通过"可选查询"读取调制系数。验证：
1. 模块可独立注册运行，事件经总线广播；
2. 环境效果真实调制生产与记忆劣化；
3. 无环境模块时一切照旧（向后兼容）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import Engine             # noqa: E402
from systems.claim import ClaimSystem      # noqa: E402
from systems.environment import EnvironmentSystem  # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402
from systems.memory import MemorySystem    # noqa: E402
from systems.recovery import RecoverySystem  # noqa: E402

ENV_CFG = {
    "events": [
        {"id": "fair", "name": "晴天", "weight": 100, "duration": 300.0,
         "effects": {}},
        {"id": "dust", "name": "沙暴", "weight": 1, "duration": 300.0,
         "effects": {"production": 0.5}},
        {"id": "storm", "name": "风暴", "weight": 1, "duration": 300.0,
         "effects": {"production": 0.8, "memory": 2.0}},
    ]
}
MEM_CFG = {"max_integrity": 100.0, "degrade_per_sec": 1.0,
           "warn_threshold": 30.0, "crisis_threshold": 10.0,
           "maintain": {"cost": {}, "restore": 100.0, "duration": 1.0}}
FACS = [
    {"id": "power_plant", "name": "电厂", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "recipe": "burn_coal", "slots": 1},
]
RECIPES = [{"id": "burn_coal", "name": "发电", "inputs": {"coal": 0.1},
            "outputs": {"electricity": 0.5}}]


def make_engine(with_env=True, with_recovery=True):
    eng = Engine()
    eng.economy.set("coal", 1000.0)
    eng.economy.set("electricity", 100.0)
    eng.units.add_unit("执行器-α")
    eng.units.add_unit("执行器-β")
    eng.bootstrap_world([
        {"id": "HOME", "ring": 0, "kind": "empty", "state": "claimed"}])
    eng.registry.register("memory", MemorySystem(MEM_CFG))
    if with_recovery:
        eng.registry.register("recovery", RecoverySystem([]))
    eng.registry.register("claim", ClaimSystem())
    eng.registry.register("industry", IndustrySystem(FACS, RECIPES))
    if with_env:
        eng.registry.register("environment", EnvironmentSystem(ENV_CFG, seed=7))
    eng.start()
    return eng


def run_ticks(eng, seconds, step=0.5):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


class TestEnvironmentModule(unittest.TestCase):
    def test_register_works_and_defaults_fair(self):
        eng = make_engine(with_env=True)
        env = eng.registry.get("environment")
        self.assertIsNotNone(env)
        self.assertEqual(env.current()["id"], "fair")
        self.assertEqual(env.effect("production"), 1.0)
        # 事件经总线广播
        got = []
        eng.bus.on("environment_change", lambda p: got.append(p["id"]))
        env.current_id = "dust"      # 直接切状态模拟（跳过随机）
        env._until = eng.clock.time
        run_ticks(eng, 1)
        self.assertTrue(got)

    def test_effect_modulates_production(self):
        eng = make_engine(with_env=True)
        env = eng.registry.get("environment")
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "power_plant")
        ind.assign(eng, "F1")
        # 晴天基线
        run_ticks(eng, 20)
        base = eng.economy.get("electricity") - 100.0
        # 手动压到沙暴
        env.current_id = "dust"
        run_ticks(eng, 20)
        dusty = eng.economy.get("electricity") - (100.0 + base)
        self.assertAlmostEqual(base, 0.5 * 20 * 1.0, delta=0.5)
        self.assertAlmostEqual(dusty, 0.5 * 20 * 0.5, delta=0.5)

    def test_storm_modulates_memory(self):
        eng = make_engine(with_env=True)
        env = eng.registry.get("environment")
        env.current_id = "storm"     # memory×2, degrade 1/s → 2/s
        run_ticks(eng, 10)
        mem = eng.registry.get("memory")
        self.assertAlmostEqual(mem.integrity, 100.0 - 2.0 * 10, delta=0.6)

    def test_backward_compat_without_env(self):
        """不注册环境模块：industry/memory 照常工作（可选查询）。"""
        eng = make_engine(with_env=False)
        ind = eng.registry.get("industry")
        ind.build(eng, "HOME", "power_plant")
        ind.assign(eng, "F1")
        e0 = eng.economy.get("electricity")
        run_ticks(eng, 10)
        self.assertGreater(eng.economy.get("electricity"), e0)
        mem = eng.registry.get("memory")
        run_ticks(eng, 10)
        # 前面已跑了 10s（电厂测试），总计 20s × 1/s
        self.assertAlmostEqual(mem.integrity, 100.0 - 20.0, delta=0.6)

    def test_roundtrip_env_state(self):
        eng = make_engine(with_env=True)
        env = eng.registry.get("environment")
        env.current_id = "dust"
        env._until = eng.clock.time + 50.0
        data = eng.to_dict()
        eng2 = make_engine(with_env=True)
        eng2.from_dict(data)
        env2 = eng2.registry.get("environment")
        self.assertEqual(env2.current_id, "dust")
        self.assertAlmostEqual(env2._until, eng.clock.time + 50.0, delta=0.6)


if __name__ == "__main__":
    unittest.main()
