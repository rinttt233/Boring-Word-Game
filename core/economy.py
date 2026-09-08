"""资源经济：数量以 float(吨/立方米/kWh…) 存储，id 字符串寻址。

具体资源定义（名称/类别/单位/描述）属于内容层 content/substances.json，
内核只处理 id → 数量。
"""
from typing import Dict


class Economy:
    def __init__(self) -> None:
        self._amounts: Dict[str, float] = {}

    # ---- 基础操作 -------------------------------------------------
    def set(self, rid: str, amount: float) -> None:
        if amount <= 0.0:
            self._amounts.pop(rid, None)
        else:
            self._amounts[rid] = amount

    def get(self, rid: str) -> float:
        return self._amounts.get(rid, 0.0)

    def add(self, rid: str, amount: float) -> None:
        if amount > 0.0:
            self._amounts[rid] = self.get(rid) + amount

    def take(self, rid: str, amount: float) -> bool:
        """严格扣除：库存不足则整体失败，返回 False。"""
        if amount <= 0.0:
            return True
        if self.get(rid) < amount - 1e-9:
            return False
        self.set(rid, self.get(rid) - amount)
        return True

    def take_available(self, rid: str, amount: float) -> float:
        """尽力扣除：返回实际扣除量。用于持续消耗（断供即停）。"""
        avail = min(amount, self.get(rid))
        if avail > 0.0:
            self.set(rid, self.get(rid) - avail)
        return avail

    # ---- 查询 ------------------------------------------------------
    def snapshot(self) -> Dict[str, float]:
        """返回有库存的 id→数量 副本（不含内部对象引用）。"""
        return {k: v for k, v in self._amounts.items() if v > 0.0}

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"amounts": self.snapshot()}

    @classmethod
    def from_dict(cls, data: dict) -> "Economy":
        e = cls()
        for k, v in (data.get("amounts") or {}).items():
            e.set(k, float(v))
        return e
