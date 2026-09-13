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
    def __init__(self, entries: List[dict],
                 default_ttl: float = 180.0,
                 unit_bonus: Optional[dict] = None) -> None:
        self.entries: Dict[str, dict] = {e["id"]: e for e in entries}
        # status: locked / active / permanent
        self.status: Dict[str, str] = {eid: "locked" for eid in self.entries}
        self.active_until: Dict[str, float] = {}
        # 临时窗口默认值（游戏秒；暂停不流逝）。条目自己的 temporary_ttl 优先。
        self.default_ttl = float(default_ttl)
        # fixate 排队（BUG-5）：没有空闲单元时不报错，而是入队等单元空出。
        # 注意：**排队不冻结窗口** —— 临时窗口照常流逝，压力还在。
        self.queued: List[str] = []
        self._warned = set()             # 已发过「即将过期」告警的条目
        # 主线固化进度奖励（P1 ②b2；§11 起上限 6 → 10，且从 content 读，不再写死）
        ub = unit_bonus or {}
        self.unit_bonus_per = max(1, int(ub.get("per_mainline", 2)))
        self.unit_bonus_cap = max(0, int(ub.get("cap", 10)))
        self._unit_bonus_granted = 0

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)
        engine.bus.on("memory_crash", self._on_memory_crash)
        self._sync_efficiency()

    # ---- 临时窗口（BUG-5：显式倒计时，供 report/UI/suggest 共用）----
    def ttl_of(self, entry_id: str) -> float:
        return float(self.entries.get(entry_id, {}).get(
            "temporary_ttl", self.default_ttl))

    def expires_in(self, engine: object, entry_id: str):
        """临时条目还剩多少游戏秒；非 active 返回 None。"""
        if self.status.get(entry_id) != "active":
            return None
        until = self.active_until.get(entry_id)
        if until is None:
            return None
        return max(0.0, float(until) - float(engine.clock.time))

    def is_queued(self, entry_id: str) -> bool:
        return entry_id in self.queued

    def _fixate_running(self, engine: object, entry_id: str) -> bool:
        """该条目是否已有正在进行的 fixate 作业（已开工的烧录不会中途丢失）。"""
        jobs = getattr(engine, "jobs", None)
        if jobs is None or not hasattr(jobs, "list_jobs"):
            return False
        for j in jobs.list_jobs():
            if j.kind == "fixate" and j.target_id == entry_id:
                return True
        return False

    def _can_pay(self, engine: object, cost: dict) -> bool:
        for rid, amt in (cost or {}).items():
            if engine.economy.get(rid) + 1e-9 < float(amt):
                return False
        return True

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
        per = max(1, int(getattr(self, "unit_bonus_per", 2)))
        want = min(int(getattr(self, "unit_bonus_cap", 10)), n_main // per)
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
        if entry_id in self.queued:
            return None                      # 已在队列里（幂等）
        cost = e.get("fixate_cost", {})
        if not self._can_pay(engine, cost):
            miss = "、".join(f"{k} {float(v):g}" for k, v in cost.items()
                             if engine.economy.get(k) < float(v))
            return f"固化资源不足：缺 {miss}。"
        unit = engine.units.assign_any("fixate")
        if unit is None:
            # BUG-5：拿不到空闲单元不报错，改为排队；材料**不预扣**（真开工时才扣）
            self.queued.append(entry_id)
            left = self.expires_in(engine, entry_id)
            left_txt = f"剩 {left:.0f}s" if left is not None else "窗口未知"
            engine.log(
                f"[数据库] 没有空闲执行单元：{e['name']} 的烧录固化**已排队**，"
                f"单元一空出就自动开工（临时窗口仍在走，{left_txt}）。"
                "想更快就 unassign/mothball 一个不急的设施腾单元。",
                level="warn", category="memory", recover=f"entry:{entry_id}")
            return None
        for rid, amt in cost.items():
            engine.economy.take(rid, float(amt))   # 上面已确认付得起
        self._begin(engine, entry_id, unit)
        return None

    def _begin(self, engine: object, entry_id: str, unit: object) -> None:
        """开工一次 fixate 作业（队列与直接下令共用）。"""
        e = self.entries[entry_id]
        dur = 6.0 / max(self._eff(engine), 1e-9)
        engine.jobs.add("fixate", unit.id, entry_id, dur,
                        {"entry": entry_id})
        engine.log(f"[数据库] 烧录固化开始：{e['name']}（单元 {unit.id}，"
                   f"{dur:.0f}s）。已开工的烧录不会中途丢失。")

    def _serve_queue(self, engine: object) -> None:
        """把排队的 fixate 逐个开工（FIFO；材料/单元任一不足就停在队首）。"""
        while self.queued:
            eid = self.queued[0]
            e = self.entries.get(eid, {})
            if self.status.get(eid) != "active":
                self.queued.pop(0)
                self._warned.discard(eid)
                engine.log(
                    f"[数据库] 排队中的固化未能开工：条目「{e.get('name', eid)}」"
                    "已不再是临时状态（已过期或被记忆崩溃清掉）。",
                    level="warn", category="memory")
                continue
            if not self._can_pay(engine, e.get("fixate_cost", {})):
                break                        # 材料还没攒够 → 继续等
            unit = engine.units.assign_any("fixate")
            if unit is None:
                break                        # 没空闲单元 → 继续等
            for rid, amt in e.get("fixate_cost", {}).items():
                engine.economy.take(rid, float(amt))
            self.queued.pop(0)
            self._begin(engine, eid, unit)
            if self.expires_in(engine, eid) is None:
                break

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
            ttl = self.ttl_of(entry_id)
            self.active_until[entry_id] = self._engine.clock.time + ttl
            self._warned.discard(entry_id)
            stat_bump(self._engine, "recovered")
            self._engine.log(
                f"[数据库] {e['name']} 已恢复 —— 临时可用 {ttl:.0f}s（游戏时间，"
                "暂停不流逝），速速 fixate 固化或趁热使用！"
                + ("（没有空闲单元也没关系：fixate 会自动排队）"
                   if self._engine.units.count_idle() <= 0 else ""),
                recover=f"entry:{entry_id}")
        else:
            self.status[entry_id] = "permanent"
            self.active_until.pop(entry_id, None)
            self._warned.discard(entry_id)
            if entry_id in self.queued:
                self.queued.remove(entry_id)
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
            self._warned.discard(eid)
            if eid in self.queued:
                self.queued.remove(eid)
        names = "、".join(self.entries[eid]["name"] for eid in lost)
        self._engine.log(f"[数据库] 记忆崩溃！未固化条目已丢失：{names}。"
                         "已固化条目安然无恙。")
        self._sync_efficiency()

    # ---- 每 tick：服务排队 + 过期 + 倒计时告警 ------------------------
    def tick(self, engine: object, dt: float) -> None:
        now = engine.clock.time
        self._serve_queue(engine)
        # 过期：已经开始烧录的条目（有 fixate 作业在跑）不掉 —— 这是明确承诺
        expired = []
        for eid, st in self.status.items():
            if st != "active":
                continue
            if self.active_until.get(eid, 0.0) > now:
                continue
            if self._fixate_running(engine, eid):
                continue
            expired.append(eid)
        for eid in expired:
            self.status[eid] = "locked"
            self.active_until.pop(eid, None)
            self._warned.discard(eid)
            if eid in self.queued:
                self.queued.remove(eid)
                engine.log(f"[数据库] 条目「{self.entries[eid]['name']}」"
                           "在排队等单元时窗口耗尽 —— 已丢失（重做要再付一次"
                           "恢复材料）。下次记得早点腾单元。",
                           level="warn", category="memory")
                continue
            engine.log(f"[数据库] 条目「{self.entries[eid]['name']}」"
                       "未及时固化，已从易失存储中丢失。",
                       level="warn", category="memory")
        if expired:
            self._sync_efficiency()
        # 倒计时告警：剩 30s 时提醒一次（进警告看板，可点击跳转）
        for eid, st in self.status.items():
            if st != "active" or eid in self._warned:
                continue
            left = self.expires_in(engine, eid)
            if left is None or left > 30.0:
                continue
            self._warned.add(eid)
            tail = ("（固化已排队，等空闲单元）" if eid in self.queued
                    else "（还没 fixate）")
            engine.log(f"[数据库] 「{self.entries[eid]['name']}」临时窗口只剩 "
                       f"{left:.0f}s{tail}，过期即丢失。",
                       level="warn", category="memory",
                       recover=f"entry:{eid}")

    # ---- 存档 -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"status": dict(self.status),
                "unit_bonus_granted": self._unit_bonus_granted,
                "queued": list(self.queued),
                "active_until": {k: v for k, v in self.active_until.items()}}

    def load(self, data: dict) -> None:
        self.status.update(data.get("status", {}))
        for eid in self.entries:
            self.status.setdefault(eid, "locked")
        self.active_until = {
            k: float(v) for k, v in data.get("active_until", {}).items()}
        # 旧档没有 queued 字段 → 空队列（向后兼容）
        self.queued = [eid for eid in data.get("queued", [])
                       if self.status.get(eid) == "active"]
        self._warned = set()
        self._unit_bonus_granted = int(data.get("unit_bonus_granted", 0) or 0)
        self._sync_efficiency()
