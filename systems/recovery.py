"""数据库恢复系统：研究 = 恢复受损存储中的知识条目。

条目生命周期：
  locked(未恢复) →(recover 作业: 耗时+消耗恢复资源) active(临时可用)
  active →(fixate 作业: 消耗固化资源) permanent(永久)
  active →(temporary_ttl 到期 或 记忆崩溃事件) locked(丢失，需重新恢复)

解锁语义：facility 定义携带 requires_recovery；industry 在建造与运行时
查询本系统 is_unlocked —— 条目丢失会使依赖它的设施停摆，固化才保险。
这正是"研究是临时的，必须烧录固化"的机制表达。
"""
from typing import Dict, List, Optional

from core.stats import bump as stat_bump


class RecoverySystem:
    def __init__(self, entries: List[dict]) -> None:
        self.entries: Dict[str, dict] = {e["id"]: e for e in entries}
        # status: locked / active / permanent
        self.status: Dict[str, str] = {eid: "locked" for eid in self.entries}
        self.active_until: Dict[str, float] = {}
        # 主线固化进度奖励（P1 ②b2）：每 2 条主线 +1 单元，上限 6
        self.unit_bonus_cap = 6
        self._unit_bonus_granted = 0

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)
        engine.bus.on("memory_crash", self._on_memory_crash)
        self._sync_efficiency()

    # ---- 门控查询 ---------------------------------------------------
    def is_unlocked(self, entry_id: str) -> bool:
        return self.status.get(entry_id) in ("active", "permanent")

    def entry_list(self) -> List[dict]:
        return [dict(e, state=self.status.get(e["id"], "locked"))
                for e in self.entries.values()]

    # ---- 知识加成（批次1：效率科技）--------------------------------
    def grant_total(self, key: str) -> float:
        """汇总所有可用条目（active/permanent）在某加成键上的数值。

        临时恢复的条目即生效 —— 与"unlocks_facility 只需恢复不需固化"
        的既有语义一致；条目丢失（记忆崩溃/超时）加成同步消失。
        """
        total = 0.0
        for eid, e in self.entries.items():
            if self.status.get(eid) in ("active", "permanent"):
                grants = e.get("grants") or {}
                total += float(grants.get(key, 0.0))
        return total

    def unit_efficiency(self) -> float:
        """单元效能 = 1.0 + Σ grants.unit_efficiency。"""
        return 1.0 + self.grant_total("unit_efficiency")

    def _eff(self, engine: object) -> float:
        """当前单元效能（engine.units 上同步后的值；无则退回 1.0）。"""
        return float(getattr(engine.units, "efficiency", 1.0) or 1.0)

    def _sync_efficiency(self) -> None:
        """把知识加成同步到 UnitPool，并按主线固化进度发放单元奖励。

        - 单元效能 = 1.0 + Σ grants.unit_efficiency（临时条目也生效）
        - **P1 ②b2**：每永久固化 2 条主线条目 → +1 执行单元（上限 +6）。
          知识不只是产能，也是"能同时干更多事"的能力。
        """
        eng = getattr(self, "_engine", None)
        if eng is None:
            return
        target = self.unit_efficiency()
        old = float(getattr(eng.units, "efficiency", 1.0) or 1.0)
        if abs(old - target) >= 1e-9:
            eng.units.set_efficiency(target)
            arrow = "提升" if target > old else "下降"
            eng.log(f"[单元] 调度知识{arrow}：单元效能 ×{old:.2f} → ×{target:.2f}"
                    f"（产能与作业速度随之{'提高' if target > old else '回落'}）。",
                    level="normal" if target > old else "warn", category="unit")
        n_main = sum(1 for eid, st in self.status.items()
                     if st == "permanent"
                     and not self.entries.get(eid, {}).get("optional"))
        want = min(int(getattr(self, "unit_bonus_cap", 6)), n_main // 2)
        while self._unit_bonus_granted < want:
            self._unit_bonus_granted += 1
            u = eng.units.add_unit("执行器-知识")
            stat_bump(eng, "units_bonus", 1.0)
            eng.log(f"[单元] 主线知识固化奖励：新增执行单元 {u.id}"
                    f"（已奖励 {self._unit_bonus_granted}/{want}，"
                    f"共 {eng.units.count()} 个单元）。", recover="unit")

    # ---- 指令 -------------------------------------------------------
    def recover(self, engine: object, entry_id: str) -> Optional[str]:
        e = self.entries.get(entry_id)
        if e is None:
            return f"未知条目: {entry_id}"
        st = self.status.get(entry_id)
        if st == "permanent":
            return "该条目已永久固化。"
        if st == "active":
            return "该条目已恢复（临时可用），可执行 fixate 固化。"
        # 依赖前置检查：需前置条目已永久固化
        for dep in e.get("depends_on", []):
            if self.status.get(dep) != "permanent":
                dep_name = self.entries.get(dep, {}).get("name", dep)
                return f"前置依赖未固化：需先永久固化「{dep_name}」。"
        cost = e.get("cost", {})
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
                return f"恢复资源不足：缺 {rid} {amt:g}。"
        unit = engine.units.assign_any("recover")
        if unit is None:
            for rid, amt in cost.items():
                engine.economy.add(rid, float(amt))
            return "没有空闲执行单元执行恢复作业。"
        dur = float(e.get("duration", 10.0)) / max(self._eff(engine), 1e-9)
        engine.jobs.add("recover", unit.id, entry_id, dur,
                        {"entry": entry_id})
        engine.log(f"[数据库] 恢复作业开始：{e['name']}"
                   f"（单元 {unit.id}，{dur:.0f}s）。")
        return None

    def fixate(self, engine: object, entry_id: str) -> Optional[str]:
        e = self.entries.get(entry_id)
        if e is None:
            return f"未知条目: {entry_id}"
        if self.status.get(entry_id) != "active":
            return "只能固化已恢复且尚未永久的条目（先 recover）。"
        cost = e.get("fixate_cost", {})
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
                return f"固化资源不足：缺 {rid} {amt:g}。"
        unit = engine.units.assign_any("fixate")
        if unit is None:
            for rid, amt in cost.items():
                engine.economy.add(rid, float(amt))
            return "没有空闲执行单元执行固化作业。"
        dur = 6.0 / max(self._eff(engine), 1e-9)
        engine.jobs.add("fixate", unit.id, entry_id, dur,
                        {"entry": entry_id})
        engine.log(f"[数据库] 烧录固化开始：{e['name']}（{dur:.0f}s）。")
        return None

    # ---- 作业完成 ---------------------------------------------------
    def _on_job_done(self, payload: dict) -> None:
        kind = payload.get("kind")
        if kind not in ("recover", "fixate"):
            return
        entry_id = payload.get("target_id")
        e = self.entries.get(entry_id)
        if e is None:
            return
        if kind == "recover":
            self.status[entry_id] = "active"
            ttl = float(e.get("temporary_ttl", 90.0))
            self.active_until[entry_id] = self._engine.clock.time + ttl
            stat_bump(self._engine, "recovered")
            self._engine.log(
                f"[数据库] {e['name']} 已恢复 —— 临时可用 {ttl:.0f}s，"
                "速速 fixate 固化或趁热使用！")
        else:
            self.status[entry_id] = "permanent"
            self.active_until.pop(entry_id, None)
            stat_bump(self._engine, "fixated")
            self._engine.log(f"[数据库] {e['name']} 已永久固化。"
                             "从此不再因记忆崩溃丢失。")
        self._sync_efficiency()

    # ---- 记忆崩溃：所有未固化条目丢失 -------------------------------
    def _on_memory_crash(self, payload: dict) -> None:
        lost = [eid for eid, st in self.status.items()
                if st == "active"]
        if not lost:
            return
        for eid in lost:
            self.status[eid] = "locked"
            self.active_until.pop(eid, None)
        names = "、".join(self.entries[eid]["name"] for eid in lost)
        self._engine.log(f"[数据库] 记忆崩溃！未固化条目已丢失：{names}。"
                         "已固化条目安然无恙。")
        self._sync_efficiency()

    # ---- 每 tick：检查临时条目过期 ----------------------------------
    def tick(self, engine: object, dt: float) -> None:
        now = engine.clock.time
        expired = [eid for eid, st in self.status.items()
                   if st == "active"
                   and self.active_until.get(eid, 0.0) <= now]
        for eid in expired:
            self.status[eid] = "locked"
            self.active_until.pop(eid, None)
            engine.log(f"[数据库] 条目「{self.entries[eid]['name']}」"
                       "未及时固化，已从易失存储中丢失。")
        if expired:
            self._sync_efficiency()

    # ---- 存档 -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"status": dict(self.status),
                "unit_bonus_granted": self._unit_bonus_granted,
                "active_until": {k: v for k, v in self.active_until.items()}}

    def load(self, data: dict) -> None:
        self.status.update(data.get("status", {}))
        for eid in self.entries:
            self.status.setdefault(eid, "locked")
        self.active_until = {
            k: float(v) for k, v in data.get("active_until", {}).items()}
        self._unit_bonus_granted = int(data.get("unit_bonus_granted", 0) or 0)
        self._sync_efficiency()
