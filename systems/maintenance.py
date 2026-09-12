"""设备维护系统（批次2b）：让"规模"有持续代价。

设定：恢复出「设备维护与检修工艺包」(db_upkeep) 之后，ASI 才真正理解
"机械会磨损"这件事 —— 于是所有设施开始持续消耗**维护件**：

  - 运转中的设施按 use_per_sec 消耗；停着（无执行单元）的只按 idle_factor
    （默认 33%）消耗；**封存(mothball)** 的完全不消耗，但重启要时间与单元。
  - 维护件供得上 → 设备状态 upkeep(0~100) 回升；供不上 → 按
    neglect_decay_per_sec 下滑。
  - upkeep 低于阈值(75) 起，按线性概率随机**故障停机**；upkeep 归零时约
    每分钟 50%。停机 downtime 秒后抢修恢复至 on_restart_upkeep（仍易故障）。

这正是"无限仓库 + 无维护 ⇒ 后期只剩排队"的反面：工厂越大，维持它越贵。
参数全部来自 content/maintenance.json。
"""
import random
from typing import Dict, Optional

from core.stats import bump as stat_bump


class MaintenanceSystem:
    def __init__(self, cfg: dict, seed: Optional[int] = None) -> None:
        self.cfg = cfg or {}
        self.requires = self.cfg.get("requires_recovery", "")
        self.kit = self.cfg.get("kit", "maintenance_kit")
        self.use_per_sec = float(self.cfg.get("use_per_sec", 0.01))
        self.idle_factor = float(self.cfg.get("idle_factor", 0.33))
        self.repair_per_sec = float(self.cfg.get("repair_per_sec", 0.5))
        self.neglect_decay = float(
            self.cfg.get("neglect_decay_per_sec", 0.03))
        self.max_upkeep = float(self.cfg.get("max_upkeep", 100.0))
        bd = self.cfg.get("breakdown", {}) or {}
        self.bd_threshold = float(bd.get("threshold", 75.0))
        self.bd_max_chance = float(bd.get("max_chance_per_min", 0.5))
        self.bd_downtime = float(bd.get("downtime", 25.0))
        self.on_restart_upkeep = float(bd.get("on_restart_upkeep", 40.0))
        self.warn_kit_seconds = float(self.cfg.get("warn_kit_seconds", 60.0))
        self._rng = random.Random(
            seed if seed is not None else self.cfg.get("seed"))
        self._shortage_logged = False
        self._warned_low = False

    def start(self, engine: object) -> None:
        self._engine = engine

    # ---- 门控与需求 ------------------------------------------------
    def enabled(self, engine: object) -> bool:
        """未恢复「设备维护」知识前，本模块完全静默（前期不背维护包袱）。"""
        if not self.requires:
            return True
        getter = getattr(engine.registry, "get", None)
        rec = getter("recovery") if getter is not None else None
        if rec is None:
            return False
        return bool(rec.is_unlocked(self.requires))

    def _facilities(self, engine: object):
        getter = getattr(engine.registry, "get", None)
        ind = getter("industry") if getter is not None else None
        if ind is None:
            return None, []
        active = [f for f in ind.facilities.values()
                  if not f.under_construction
                  and not getattr(f, "mothballed", False)]
        return ind, active

    def demand_per_sec(self, engine: object) -> float:
        """当前每秒维护件需求（运转 100%、停着 33%、封存 0）。

        未恢复维护知识时体系未启用 → 需求恒为 0（前期不背维护包袱）。
        """
        if not self.enabled(engine):
            return 0.0
        _ind, facs = self._facilities(engine)
        total = 0.0
        for f in facs:
            total += self.use_per_sec * (1.0 if f.assigned else self.idle_factor)
        return total

    def worst_upkeep(self, engine: object) -> Optional[float]:
        _ind, facs = self._facilities(engine)
        vals = [float(getattr(f, "upkeep", self.max_upkeep)) for f in facs]
        return min(vals) if vals else None

    def status_text(self, engine: object) -> str:
        if not self.enabled(engine):
            return "维护体系：未恢复（无维护件消耗）"
        avail = engine.economy.get(self.kit)
        demand = self.demand_per_sec(engine)
        worst = self.worst_upkeep(engine)
        due = (avail / demand) if demand > 0 else float("inf")
        due_txt = "∞" if due == float("inf") else f"{due:.0f}s"
        worst_txt = "—" if worst is None else f"{worst:.0f}"
        return (f"维护件 {avail:.1f}（需求 {demand:.2f}/s，可撑 {due_txt}）"
                f"｜最低设备状态 {worst_txt}")

    # ---- 每 tick ---------------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        if dt <= 0 or not self.enabled(engine):
            return
        _ind, facs = self._facilities(engine)
        if not facs:
            return
        now = float(engine.clock.time)
        avail = engine.economy.get(self.kit)
        for f in facs:
            avail = self._tick_facility(engine, f, dt, now, avail)
        self._warn_supply(engine, avail)

    def _tick_facility(self, engine: object, f, dt: float, now: float,
                       avail: float) -> float:
        # 故障停机结束 → 抢修完毕（恢复到易故障区间）
        if f.halt_reason == "维护失效停机" and f.halt_until <= now:
            f.upkeep = max(float(f.upkeep), self.on_restart_upkeep)
            f.halt_reason = ""
            engine.log(f"[维护] {f.name} 抢修完成（设备状态 "
                       f"{f.upkeep:.0f}），仍处于易故障区间。",
                       recover=f"fac:{f.id}")
        need = self.use_per_sec * dt * (1.0 if f.assigned else self.idle_factor)
        coverage = 1.0 if need <= 0 else min(1.0, avail / need)
        if coverage > 0.0:
            taken = need * coverage
            engine.economy.take(self.kit, taken)
            avail -= taken
            stat_bump(engine, "kits_used", taken)
        upkeep = float(getattr(f, "upkeep", self.max_upkeep))
        upkeep += (self.repair_per_sec * coverage
                   - self.neglect_decay * (1.0 - coverage)) * dt
        f.upkeep = max(0.0, min(self.max_upkeep, upkeep))
        # 故障判定（停机中不再重复触发）
        if f.upkeep < self.bd_threshold and now >= f.halt_until:
            span = max(self.bd_threshold, 1e-9)
            ratio = (self.bd_threshold - f.upkeep) / span
            chance = self.bd_max_chance * ratio * dt / 60.0
            if chance > 0 and self._rng.random() < chance:
                f.halt_until = now + self.bd_downtime
                f.halt_reason = "维护失效停机"
                stat_bump(engine, "breakdowns")
                engine.log(f"[维护] {f.name} 故障停机！设备状态 "
                           f"{f.upkeep:.0f}（维护件短缺）—— 预计停机 "
                           f"{self.bd_downtime:.0f}s。",
                           level="danger", category=f"fac:{f.id}")
        return avail

    def _warn_supply(self, engine: object, avail: float) -> None:
        demand = self.demand_per_sec(engine)
        low = demand > 0 and avail < demand * self.warn_kit_seconds
        if low and not self._warned_low:
            self._warned_low = True
            self._shortage_logged = True
            secs = avail / demand if demand > 0 else 0.0
            engine.log(f"[维护] 维护件库存偏低：{avail:.1f}，仅够 {secs:.0f}s "
                       f"（需求 {demand:.2f}/s）。缺供将导致设备状态下滑与故障。",
                       level="warn", category="maintenance")
        elif not low and self._warned_low:
            self._warned_low = False
            engine.log("[维护] 维护件供应恢复，设备状态开始回升。",
                       recover="maintenance")
