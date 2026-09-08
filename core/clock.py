"""游戏时钟：纯游戏时间模型。

内核只认识"游戏时间"(秒)与"暂停"两个概念；墙钟播放速度是 UI/驱动层的
映射，不进内核 —— 保证变速永不破坏数值模型，存档/读档天然一致。

速率语义：所有产出/消耗/劣化都以"每游戏秒"表达，engine.tick(dt) 的 dt
为游戏秒。驱动层负责把墙钟时间 × 播放速度换算成 dt。
"""
from typing import Optional


class GameClock:
    def __init__(self, start: float = 0.0) -> None:
        self.time: float = start          # 累计游戏秒
        self.paused: bool = False         # 暂停 = 世界停止，墙钟照走
        self._cruise_until: Optional[float] = None   # 巡航目标时刻

    # ---- 播放控制 -------------------------------------------------
    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    # ---- 巡航（自动驾驶到站自动暂停）-----------------------------
    @property
    def cruising(self) -> bool:
        return self._cruise_until is not None

    def start_cruise(self, seconds: float) -> None:
        self._cruise_until = self.time + max(0.0, seconds)
        self.paused = False

    def cancel_cruise(self) -> None:
        self._cruise_until = None

    def cruise_remaining(self) -> float:
        if self._cruise_until is None:
            return 0.0
        return max(0.0, self._cruise_until - self.time)

    # ---- 推进 ------------------------------------------------------
    def advance(self, dt: float) -> bool:
        """推进游戏时间。返回 True 表示本步恰好越过巡航终点。"""
        if self.paused or dt <= 0.0:
            return False
        self.time += dt
        if self._cruise_until is not None and self.time >= self._cruise_until:
            self._cruise_until = None
            return True
        return False

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"time": self.time, "paused": self.paused}

    @classmethod
    def from_dict(cls, data: dict) -> "GameClock":
        c = cls(start=float(data.get("time", 0.0)))
        c.paused = bool(data.get("paused", False))
        return c
