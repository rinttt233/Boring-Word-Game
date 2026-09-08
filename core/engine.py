"""引擎：装配内核（时钟/经济/世界/单元/注册表/事件总线），对外唯一入口。

tick(dt) 是唯一"世界推进"入口，dt 单位 = 游戏秒。UI 层把墙钟×速度换算成
dt 后喂进来 —— 引擎不感知墙钟与播放速度，保证可测试、可变速、可存档。
"""
from typing import List, Optional

from .bus import EventBus
from .clock import GameClock
from .economy import Economy
from .jobs import JobRegistry
from .registry import SystemRegistry
from .units import UnitPool
from .world import World, Plot


class Engine:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.clock = GameClock()
        self.economy = Economy()
        self.world = World()
        self.units = UnitPool()
        self.jobs = JobRegistry()
        self.registry = SystemRegistry()
        self.log_lines: List[str] = []   # 历史日志（用于展示/存档）

    # ---- 启动 ------------------------------------------------------
    def start(self) -> None:
        self.registry.start_all(self)
        self._log("系统启动。核心指令集加载完毕。")

    # ---- 主推进 ----------------------------------------------------
    def tick(self, dt: float) -> None:
        if dt <= 0.0 or self.clock.paused:
            return
        arrived = self.clock.advance(dt)
        self.jobs.tick(self, dt)
        self.registry.tick_all(self, dt)
        if arrived:
            # 巡航到点 → 自动暂停（驱动层的"自动驾驶到站"）
            self.clock.pause()
            self._log("巡航到点，已自动暂停。")

    # ---- 巡航便捷入口 ----------------------------------------------
    def cruise(self, seconds: float) -> None:
        self.clock.resume()
        self.clock.start_cruise(seconds)

    # ---- 日志与事件 -------------------------------------------------
    def _log(self, text: str, level: str = "normal",
             category: Optional[str] = None,
             recover: Optional[str] = None) -> None:
        self.log_lines.append(text)
        self.bus.emit("log", {
            "text": text,
            "level": level,
            "category": category,
            "recover": recover,
        })

    def log(self, text: str, level: str = "normal",
            category: Optional[str] = None,
            recover: Optional[str] = None) -> None:
        self._log(text, level=level, category=category, recover=recover)

    # ---- 便捷：一次性建造出生点（内容层 bootstrap 调用）------------
    def bootstrap_world(self, seed_plots: List[dict]) -> None:
        for spec in seed_plots:
            p = Plot.from_dict(spec)
            self.world.add_plot(p)

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "clock": self.clock.to_dict(),
            "economy": self.economy.to_dict(),
            "world": self.world.to_dict(),
            "units": self.units.to_dict(),
            "jobs": self.jobs.to_dict(),
            "systems": self.registry.to_dict(),
            "log": list(self.log_lines),
        }

    def from_dict(self, data: dict) -> None:
        self.clock = GameClock.from_dict(data.get("clock", {}))
        self.economy = Economy.from_dict(data.get("economy", {}))
        self.world = World.from_dict(data.get("world", {}))
        self.units = UnitPool.from_dict(data.get("units", {}))
        self.jobs = JobRegistry.from_dict(data.get("jobs", {}))
        self.registry.load_dict(data.get("systems", {}))
        self.log_lines = list(data.get("log", []))
