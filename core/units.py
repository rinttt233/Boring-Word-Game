"""执行单元调度器：生产力瓶颈的核心。

设定：受损 ASI 的物理执行单元（机械臂/建造无人机/运输单元）有限，
设施必须分配执行单元才运转。玩家每时每刻在"把有限的执行器拨给谁"。
单元数量随剧情/科技回收增长；利用率可被科技提升 —— 均为数据驱动。
"""
from typing import Dict, List, Optional


class Unit:
    def __init__(self, unit_id: str, name: str = "") -> None:
        self.id = unit_id
        self.name = name or unit_id
        self.status = "idle"       # idle / busy
        self.task: Optional[str] = None   # 挂载的设施/任务 id

    def assign(self, task: str) -> bool:
        if self.status == "busy":
            return False
        self.status = "busy"
        self.task = task
        return True

    def release(self) -> None:
        self.status = "idle"
        self.task = None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "status": self.status, "task": self.task}

    @classmethod
    def from_dict(cls, data: dict) -> "Unit":
        u = cls(data["id"], data.get("name", ""))
        u.status = data.get("status", "idle")
        u.task = data.get("task")
        return u


class UnitPool:
    def __init__(self) -> None:
        self.units: List[Unit] = []
        self._seq = 0

    def add_unit(self, name: str = "") -> Unit:
        self._seq += 1
        u = Unit(f"U{self._seq}", name)
        self.units.append(u)
        return u

    def get(self, unit_id: str) -> Optional[Unit]:
        for u in self.units:
            if u.id == unit_id:
                return u
        return None

    def idle_units(self) -> List[Unit]:
        return [u for u in self.units if u.status == "idle"]

    def assign_any(self, task: str) -> Optional[Unit]:
        """把一个空闲单元分配去执行 task；无空闲返回 None。"""
        idle = self.idle_units()
        if not idle:
            return None
        idle[0].assign(task)
        return idle[0]

    def release_all(self) -> None:
        for u in self.units:
            u.release()

    def count(self) -> int:
        return len(self.units)

    def count_idle(self) -> int:
        return len(self.idle_units())

    def utilization(self) -> float:
        n = self.count()
        return 0.0 if n == 0 else 1.0 - self.count_idle() / n

    def to_dict(self) -> dict:
        return {"units": [u.to_dict() for u in self.units], "seq": self._seq}

    @classmethod
    def from_dict(cls, data: dict) -> "UnitPool":
        p = cls()
        p._seq = int(data.get("seq", 0))
        for ud in data.get("units", []):
            p.units.append(Unit.from_dict(ud))
        return p
