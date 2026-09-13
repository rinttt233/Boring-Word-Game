"""运行统计埋点（批次1 起累积，供批次8 的结算画面与统计面板使用）。

设计原则：**只加计数器，不参与玩法判定**。任何系统都可以 bump，
读取者（结算/GUI）不应依赖某个键一定存在 —— 缺失视为 0。
"""
from typing import Dict


class Stats:
    DEFAULTS = {
        "stall_events": 0.0,      # 设施进入停摆的次数
        "stall_seconds": 0.0,     # 设施停摆累计（设施·秒）
        "maintains": 0.0,         # 记忆加固次数
        "recovered": 0.0,         # 恢复的条目数
        "fixated": 0.0,           # 固化的条目数
        "memory_crashes": 0.0,    # 记忆崩溃次数
        "unit_peak": 0.0,         # 执行单元数量峰值
        "efficiency_peak": 1.0,   # 单元效能峰值
        "breakdowns": 0.0,        # 设备故障停机次数（维护失效）
        "kits_used": 0.0,         # 累计消耗维护件
        "emergency_starts": 0.0,  # 读档死锁时发放应急启动的次数
        "units_built": 0.0,       # 装配厂产出的执行单元数
        "units_bonus": 0.0,       # 主线固化奖励的执行单元数
        "play_seconds": 0.0,      # 游戏内累计运行时间
    }

    def __init__(self) -> None:
        self.data: Dict[str, float] = dict(self.DEFAULTS)

    # ---- 写入 ------------------------------------------------------
    def bump(self, key: str, amount: float = 1.0) -> None:
        self.data[key] = float(self.data.get(key, 0.0)) + float(amount)

    def set_max(self, key: str, value: float) -> None:
        if float(value) > float(self.data.get(key, 0.0)):
            self.data[key] = float(value)

    # ---- 读取 ------------------------------------------------------
    def get(self, key: str) -> float:
        return float(self.data.get(key, 0.0))

    def snapshot(self) -> Dict[str, float]:
        return dict(self.data)

    def summary_text(self) -> str:
        d = self.data
        return (f"停摆 {d.get('stall_events', 0):.0f} 次"
                f"/{d.get('stall_seconds', 0) / 60:.1f} 分 · "
                f"加固 {d.get('maintains', 0):.0f} 次 · "
                f"恢复 {d.get('recovered', 0):.0f} 条"
                f"(固化 {d.get('fixated', 0):.0f}) · "
                f"崩溃 {d.get('memory_crashes', 0):.0f} 次 · "
                f"单元峰值 {d.get('unit_peak', 0):.0f}"
                f"(效能 ×{d.get('efficiency_peak', 1.0):.2f})")

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return dict(self.data)

    @classmethod
    def from_dict(cls, data: dict) -> "Stats":
        s = cls()
        for k, v in (data or {}).items():
            try:
                s.data[k] = float(v)
            except (TypeError, ValueError):
                continue
        return s


def bump(engine: object, key: str, amount: float = 1.0) -> None:
    """安全写入（引擎无 stats 时静默跳过，便于极简测试替身）。"""
    st = getattr(engine, "stats", None)
    if st is not None:
        st.bump(key, amount)
