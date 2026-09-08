"""事件总线：机制之间解耦通信的唯一通道。

模块不互相直接调用，只 emit / 订阅事件。新增机制想插入现有循环任意位置，
订阅对应事件即可，不改动其它模块 —— 这是"便于拓展"的支柱之一。
"""
from typing import Callable, Dict, List, Optional


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable]] = {}
        self._all: List[Callable] = []

    def on(self, event: str, handler: Callable) -> Callable:
        """订阅指定事件。handler(payload: dict)。"""
        self._handlers.setdefault(event, []).append(handler)
        return handler

    def off(self, event: str, handler: Callable) -> None:
        h = self._handlers.get(event)
        if h and handler in h:
            h.remove(handler)

    def on_any(self, handler: Callable) -> Callable:
        """订阅全部事件。handler(event: str, payload: dict)。用于日志/审计。"""
        self._all.append(handler)
        return handler

    def emit(self, event: str, payload: Optional[dict] = None) -> None:
        payload = payload or {}
        for h in list(self._handlers.get(event, ())):
            h(payload)
        for h in list(self._all):
            h(event, payload)
