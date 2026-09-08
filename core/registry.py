"""活动模块注册表："便于拓展"的核心支柱。

一条玩法循环（勘探/扩展/开采/研发，或未来的贸易/防御）= 一个模块。
模块实现可选钩子：
  start(engine)              装配时注册事件订阅、初始化
  tick(engine, dt)           每游戏秒推进
  to_dict() / load(dict)     模块自持状态存档
新增玩法 = 新增一个文件注册进 registry，不改动既有模块与内核。
"""
from typing import Dict, List


class SystemRegistry:
    def __init__(self) -> None:
        self._systems: Dict[str, object] = {}
        self._order: List[str] = []

    def register(self, name: str, system: object) -> object:
        if name in self._systems:
            raise ValueError(f"模块已存在: {name}")
        self._systems[name] = system
        self._order.append(name)
        return system

    def get(self, name: str) -> object:
        return self._systems.get(name)

    def names(self) -> List[str]:
        return list(self._order)

    # ---- 生命周期 -------------------------------------------------
    def start_all(self, engine: object) -> None:
        for name in self._order:
            s = self._systems[name]
            if hasattr(s, "start"):
                s.start(engine)

    def tick_all(self, engine: object, dt: float) -> None:
        for name in self._order:
            s = self._systems[name]
            if hasattr(s, "tick"):
                s.tick(engine, dt)

    # ---- 存档（模块自持状态，供 M1+ 使用）-------------------------
    def to_dict(self) -> dict:
        out = {}
        for name in self._order:
            s = self._systems[name]
            out[name] = s.to_dict() if hasattr(s, "to_dict") else {}
        return out

    def load_dict(self, data: dict) -> None:
        for name, blob in data.items():
            s = self._systems.get(name)
            if s is not None and hasattr(s, "load"):
                s.load(blob)
