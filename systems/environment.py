"""行星环境系统（M4 拓展性验收的第 5 模块）。

作为"新增玩法循环零内核改动"的验收样例：
- 纯新模块文件 + JSON 配置，通过注册表挂载；
- 通过事件总线广播环境变化日志；
- 通过可选的 registry 查询向既有模块提供调制系数 —— industry/memory
  使用 self._effect("production"/"memory") 读取；未注册本模块时返回 1.0，
  证明新增机制不破坏既有系统。
"""
import random
from typing import Optional


class EnvironmentSystem:
    def __init__(self, cfg: dict, seed: Optional[int] = None) -> None:
        self.events = cfg.get("events", [])
        self.current_id = "fair"
        self._until: float = 0.0
        self._rng = random.Random(seed)

    def start(self, engine: object) -> None:
        self._engine = engine
        self._until = engine.clock.time + self._duration_of("fair")
        engine.bus.emit("environment_change", {"id": "fair"})

    # ---- 状态 -------------------------------------------------------
    def current(self) -> Optional[dict]:
        for e in self.events:
            if e["id"] == self.current_id:
                return e
        return None

    def _duration_of(self, eid: str) -> float:
        for e in self.events:
            if e["id"] == eid:
                return float(e.get("duration", 60.0))
        return 60.0

    def effect(self, key: str) -> float:
        """供 industry/memory 查询调制系数（默认 1.0 = 无影响）。"""
        e = self.current()
        if e is None:
            return 1.0
        return float(e.get("effects", {}).get(key, 1.0))

    # ---- 每 tick ---------------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        if engine.clock.time < self._until:
            return
        total = sum(float(e.get("weight", 1)) for e in self.events)
        roll = self._rng.uniform(0, total)
        acc = 0.0
        chosen = self.events[0]
        for e in self.events:
            acc += float(e.get("weight", 1))
            if roll <= acc:
                chosen = e
                break
        old = self.current_id
        self.current_id = chosen["id"]
        self._until = engine.clock.time + float(chosen.get("duration", 60.0))
        if chosen["id"] != old:
            engine.bus.emit("environment_change", {"id": chosen["id"]})
            engine.log(f"[环境] 气候转替：{chosen['name']}。{chosen.get('desc', '')}")

    # ---- 存档 -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"current_id": self.current_id, "until": self._until}

    def load(self, data: dict) -> None:
        self.current_id = data.get("current_id", "fair")
        self._until = float(data.get("until", 0.0))
