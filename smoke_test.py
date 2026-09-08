# -*- coding: utf-8 -*-
"""集成冒烟：以阻塞模式驱动 UI，喂命令，断言回显日志。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import Engine             # noqa: E402
from systems.claim import ClaimSystem      # noqa: E402
from systems.database import DatabaseSystem  # noqa: E402
from systems.environment import EnvironmentSystem  # noqa: E402
from systems.industry import IndustrySystem  # noqa: E402
from systems.memory import MemorySystem    # noqa: E402
from systems.recovery import RecoverySystem  # noqa: E402
from systems.survey import SurveySystem    # noqa: E402
from ui.console import ConsoleUI           # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_json(name):
    with open(os.path.join(ROOT, "content", name), "r", encoding="utf-8") as f:
        return json.load(f)


def build_engine():
    eng = Engine()
    boot = load_json("bootstrap.json")
    for rid, amt in boot["resources"].items():
        eng.economy.set(rid, float(amt))
    for spec in boot["units"]:
        eng.units.add_unit(spec["name"])
    eng.bootstrap_world(boot["plots"])
    eng.registry.register("memory", MemorySystem(load_json("memory.json")))
    eng.registry.register("recovery",
                          RecoverySystem(load_json("recovery.json")["entries"]))
    eng.registry.register("survey", SurveySystem(load_json("regions.json")))
    eng.registry.register("claim", ClaimSystem())
    eng.registry.register(
        "industry",
        IndustrySystem(load_json("facilities.json")["facilities"],
                       load_json("recipes.json")["recipes"]))
    eng.registry.register(
        "database",
        DatabaseSystem(load_json("database.json")["projects"]))
    eng.registry.register(
        "environment", EnvironmentSystem(load_json("environment.json")))
    eng.start()
    return eng


def main():
    eng = build_engine()
    substances = load_json("substances.json")["substances"]
    ui = ConsoleUI(eng, {s["id"]: s for s in substances}, speed=1.0)

    for cmd in ["status", "plots", "units", "resources", "help"]:
        ui._execute(cmd)

    text = "\n".join(eng.log_lines)
    expected = ["执行单元", "IRON1", "铁矿", "执行器-α", "库存",
                "cruise", "logs"]
    results = {e: (e in text) for e in expected}
    print(results)
    assert all(results.values()), "集成冒烟断言失败"
    print("SMOKE-OK")


if __name__ == "__main__":
    main()
