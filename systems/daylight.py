"""昼夜循环模块（第6模块：电力/环境派生）。

设定：行星自转周期短，玩家在一个 60s 周期里体会"白天产电、夜里停机"
的节奏。纯派生模块：不持有状态（相位由 engine.clock.time 计算），
不改内核、不落存档（派生即可，存档往返天然一致）。

- effect("solar") -> [0,1]：日照强度（供光热/光伏设施与 UI ☀/☾ 查询）
  * 白天段（cycle 起点起 day 秒）为 1；
  * 黄昏段（day..day+dusk）线性衰减到 0；
  * 其余（夜间）为 0。
"""
from typing import Optional


class DaylightSystem:
    def __init__(self, cfg: dict) -> None:
        self.period = float(cfg.get("period", 60.0))
        self.day = float(cfg.get("day", 32.0))
        self.dusk = float(cfg.get("dusk", 6.0))

    def start(self, engine: object) -> None:
        self._engine = engine

    # ---- 查询 ------------------------------------------------------
    def effect(self, key: str = "solar") -> float:
        """日照系数 0~1（向后兼容：未知 key 恒 1）。"""
        if key != "solar":
            return 1.0
        if getattr(self, "_engine", None) is None:
            return 1.0
        t = self._engine.clock.time % self.period
        if t < self.day:
            return 1.0
        if t < self.day + self.dusk and self.dusk > 0:
            return max(0.0, 1.0 - (t - self.day) / self.dusk)
        return 0.0

    def is_day(self) -> bool:
        return self.effect("solar") > 0.0

    def phase_text(self) -> str:
        return "☀ 白天" if self.is_day() else "☾ 夜"

    # ---- 存档：纯派生，无需状态 ----------------------------------
    def to_dict(self) -> dict:
        return {}

    def load(self, data: dict) -> None:
        pass
