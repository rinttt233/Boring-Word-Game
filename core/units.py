"""执行单元调度器：生产力瓶颈的核心。

设定：受损 ASI 的物理执行单元（机械臂/建造无人机/运输单元）有限，
设施必须分配执行单元才运转。玩家每时每刻在"把有限的执行器拨给谁"。

单元效能 efficiency（≥1.0）：由恢复出的知识条目 grants.unit_efficiency 汇总
得到 —— 数量不变，但每个单元"更懂怎么干活"：产能与作业速度随之提升。
数值全部数据驱动（content/recovery.json）。
"""
import math
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
        # 单元效能：1.0 = 原始状态；>1 表示同样单元数产出更多、作业更快
        self.efficiency = 1.0

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

    # ---- 单元效能（批次1：效率科技）--------------------------------
    def set_efficiency(self, value: float) -> float:
        """设置效能并返回旧值（下限 0.1，防止除零/负值）。"""
        old = self.efficiency
        self.efficiency = max(0.1, float(value))
        return old

    def effective_slots(self, slots: int = 1) -> int:
        """效能折算后"喂满"该设施所需的单元数（多槽大设施受益）。"""
        slots = max(1, int(slots))
        return max(1, int(math.ceil(slots / max(self.efficiency, 1e-9))))

    def staff_factor(self, assigned: int, slots: int = 1) -> float:
        """派员比例 × 效能 → 产能/速度倍率。

        效能为 1.0 时恒返回 1.0（严格保持既有平衡基线）；
        效能越高、派员越足，倍率越接近 efficiency。
        """
        if assigned <= 0:
            return 0.0
        slots = max(1, int(slots))
        ratio = min(1.0, float(assigned) / float(slots))
        return 1.0 + (self.efficiency - 1.0) * ratio

    def to_dict(self) -> dict:
        return {"units": [u.to_dict() for u in self.units], "seq": self._seq,
                "efficiency": self.efficiency}

    @classmethod
    def from_dict(cls, data: dict) -> "UnitPool":
        p = cls()
        p._seq = int(data.get("seq", 0))
        for ud in data.get("units", []):
            p.units.append(Unit.from_dict(ud))
        # 旧档无 efficiency 字段 → 1.0（向后兼容）
        p.efficiency = float(data.get("efficiency", 1.0) or 1.0)
        return p
