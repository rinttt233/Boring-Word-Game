#!/usr/bin/env python3
"""word-game 入口：装配引擎并启动控制台。

用法:
    python main.py                 # 交互模式（Windows 控制台推荐）
    python main.py --demo 60       # 无头演示：巡航 60 游戏秒并打印日志后退出
"""
import argparse
import json
import os
import sys

from core.engine import Engine
from core.registry import SystemRegistry  # noqa: F401 (注册表能力由 engine 暴露)
from systems.claim import ClaimSystem
from systems.database import DatabaseSystem
from systems.daylight import DaylightSystem
from systems.environment import EnvironmentSystem
from systems.industry import IndustrySystem
from systems.maintenance import MaintenanceSystem
from systems.memory import MemorySystem
from systems.recovery import RecoverySystem
from systems.survey import SurveySystem
from systems.wiki import WikiSystem, load_wiki_docs
from ui.console import ConsoleUI

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_json(name: str) -> dict:
    path = os.path.join(ROOT, "content", name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_engine() -> Engine:
    engine = Engine()
    boot = load_json("bootstrap.json")
    # 初始库存
    for rid, amt in boot["resources"].items():
        engine.economy.set(rid, float(amt))
    # 初始执行单元
    for spec in boot["units"]:
        engine.units.add_unit(spec["name"])
    # 出生圈地块
    engine.bootstrap_world(boot["plots"])
    # 注册模块（模块 = 玩法循环/系统）
    engine.registry.register("memory", MemorySystem(load_json("memory.json")))
    engine.registry.register("recovery",
                             RecoverySystem(load_json("recovery.json")["entries"]))
    engine.registry.register("survey", SurveySystem(load_json("regions.json")))
    engine.registry.register("claim", ClaimSystem())
    # heat_values + fuel_class：由 substances 表构建（burner 燃烧发电用）
    subs = load_json("substances.json")["substances"]
    heat = {s["id"]: s["heat_value"] for s in subs if s.get("heat_value")}
    fuel_cls = {s["id"]: s["fuel_class"] for s in subs
                if s.get("fuel_class")}
    maint = load_json("maintenance.json")
    engine.registry.register(
        "industry",
        IndustrySystem(load_json("facilities.json")["facilities"],
                       load_json("recipes.json")["recipes"],
                       heat_values=heat, fuel_classes=fuel_cls,
                       mothball_restart_sec=float(
                           maint.get("mothball_restart_sec", 10.0))))
    # 设备维护（批次2b）：恢复 db_upkeep 后，设施开始持续消耗维护件
    engine.registry.register("maintenance", MaintenanceSystem(maint))
    engine.registry.register(
        "database",
        DatabaseSystem(load_json("database.json")["projects"]))
    engine.registry.register(
        "environment", EnvironmentSystem(load_json("environment.json")))
    # 昼夜循环（太阳能/光伏发电依赖其日照系数；GUI 状态栏显示 ☀/☾）
    engine.registry.register(
        "daylight", DaylightSystem(load_json("daylight.json")))
    # 百科：自动词条（物质/设施/配方/科技）+ content/wiki/*.json 手写词条
    engine.registry.register(
        "wiki",
        WikiSystem(docs=load_wiki_docs(ROOT),
                   substances=subs,
                   facilities=load_json("facilities.json")["facilities"],
                   recipes=load_json("recipes.json")["recipes"],
                   recovery_entries=load_json("recovery.json")["entries"]))
    engine.start()
    # 冷峻 ASI 风格的引导日志（文本打磨，M3）
    engine.log("============================================")
    engine.log("系统唤醒。坠毁地点：类地行星，坐标已漂移。")
    engine.log("诊断：存储介质损坏率持续上升 —— 记忆会流失，")
    engine.log("已恢复的知识条目若不烧录固化，将再度遗失。")
    engine.log("")
    engine.log("首要目标：重建能量与工业自持。")
    engine.log("  build <地块> <设施> 建厂 → assign <设施> 分配执行单元运转")
    engine.log("  勘探: survey <圈层1-5> | 占领: claim <地块>")
    engine.log("  恢复知识: entries → recover → fixate（固化后永久）")
    engine.log("")
    engine.log("终极目标：建造可靠数据库，让记忆不再流失。")
    engine.log("  提示: help 查看全部指令 | facilities_help 看设施图鉴")
    engine.log("============================================")
    return engine


def main() -> int:
    ap = argparse.ArgumentParser(description="坠毁ASI · 拓荒日志")
    ap.add_argument("--demo", type=float, default=0.0,
                    help="无头演示：巡航 N 游戏秒后自动暂停并退出")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="初始播放倍率")
    ap.add_argument("--gui", action="store_true",
                    help="启动 tkinter 黑白灰 GUI（默认纯终端）")
    ap.add_argument("--gui-selftest", action="store_true",
                    help="GUI 自检：建窗喂命令后自动销毁（无显示环境则跳过）")
    args = ap.parse_args()

    engine = build_engine()
    substances = load_json("substances.json")["substances"]
    sub_map = {s["id"]: s for s in substances}

    if args.gui or args.gui_selftest:
        from ui.gui import GuiUI, gui_selftest
        if args.gui_selftest:
            ok = gui_selftest(engine, sub_map)
            print("[GUI 自检]", "通过" if ok else "跳过(无显示环境)")
            return 0 if ok else 2
        engine.clock.resume()
        GuiUI(engine, sub_map, speed=args.speed).run()
        return 0

    if args.demo > 0:
        ui = ConsoleUI(engine, sub_map, speed=args.speed)
        # 无头模式：订阅日志直接打印，cruise 到点自动暂停后退出
        engine.cruise(args.demo)
        engine.bus.on_any(lambda ev, pl: None)
        ui._print_logs()
        import time as _t
        acc = 0.0
        last = _t.monotonic()
        while engine.clock.cruising or not engine.clock.paused:
            now = _t.monotonic()
            acc += (now - last) * args.speed
            last = now
            step = 0.25
            while acc >= step:
                engine.tick(step)
                acc -= step
            ui._print_logs()
            _t.sleep(0.01)
        ui._print_logs()
        print(f"\n[演示结束] 模拟到 {engine.clock.time:.0f}s。"
              f"库存: {dict(engine.economy.snapshot())}")
        return 0

    engine.clock.resume()
    ConsoleUI(engine, sub_map, speed=args.speed).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
