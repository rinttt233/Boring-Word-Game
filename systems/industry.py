"""工业模块：地块设施 = 产能引擎（M1 生产闭环核心）。

- 设施实例挂在已占领地块上（plot.state → developed）。
- 每座设施须分配 ≥1 执行单元才运转（调度瓶颈）。
- 两类设施：
  * 配方设施（发电/冶炼…）：按 recipes.json 连续转换 inputs→outputs。
  * 提取设施（采矿/取水/残骸回收）：消耗电力按 extract_rate 提取地块资源，
    矿脉储量递减，枯竭后地块置 depleted；残骸回收枯竭可奖励执行单元。
- 输入不足 → 按可用比例减产（断供即停，不停机但零产）。
"""
from typing import Dict, List, Optional

from core.world import Plot
from core.stats import bump as stat_bump

# 关键资源（枯竭时给醒目告警，试玩提案 §5）：物质 id → 中文名
KEY_SUBSTANCES = {
    "coal": "煤", "iron_ore": "铁矿石", "limestone": "石灰石",
    "sulfur": "硫磺", "copper_ore": "铜矿石", "petroleum": "石油",
    "scrap_alloy": "残骸合金",
}


class Facility:
    def __init__(self, fac_id: str, plot_id: str, def_id: str,
                 name: str) -> None:
        self.id = fac_id
        self.plot_id = plot_id
        self.def_id = def_id
        self.name = name
        self.assigned: List[str] = []        # 分配的执行单元 id
        self.stalled_reported = False        # 停摆日志去重
        self.stall_reason: Optional[str] = None   # 最近一次停摆原因（供 UI 展示）
        # 机器可读的停摆码（P0 统一口径）：no_input/no_power/no_fuel/
        # no_fuel_set/fuel_class/fuel_unburnable/no_recipe/no_capacity/
        # knowledge_lost/halted。供 report 与脚本断言用，文案可自由改。
        self.stall_code: Optional[str] = None
        self.fuel: Optional[str] = None      # burner 当前燃料
        self.produced_any = False            # 是否已产出过（撤销建造的安全闸）
        self.under_construction = False      # 建造作业进行中（不耗料/不产出）
        # 通用停机接口（维护系统/重启封存用）：halt_until 之前不运转
        self.halt_until = 0.0
        self.halt_reason: Optional[str] = None
        self.mothballed = False              # 封存：不运转、不消耗维护件
        self.upkeep = 100.0                  # 设备状态 0~100（维护系统结算）
        self.unit_progress = 0.0             # 单元装配进度（0~1，unit_factory 用）
        self.stored = 0.0                    # 堆存量（sink 用）
        self.backlog_over: Optional[str] = None   # 因哪种副产物积压而限产

    def to_dict(self) -> dict:
        return {"id": self.id, "plot_id": self.plot_id, "def_id": self.def_id,
                "name": self.name, "assigned": list(self.assigned),
                "stall_reason": self.stall_reason,
                "stall_code": self.stall_code,
                "produced_any": self.produced_any,
                "under_construction": self.under_construction,
                "halt_until": self.halt_until,
                "halt_reason": self.halt_reason,
                "mothballed": self.mothballed,
                "upkeep": self.upkeep,
                "unit_progress": self.unit_progress,
                "stored": self.stored,
                "backlog_over": self.backlog_over,
                "fuel": self.fuel}

    @classmethod
    def from_dict(cls, data: dict) -> "Facility":
        f = cls(data["id"], data["plot_id"], data["def_id"], data["name"])
        f.assigned = list(data.get("assigned", []))
        f.stall_reason = data.get("stall_reason")
        f.stall_code = data.get("stall_code")
        f.produced_any = bool(data.get("produced_any", False))
        f.under_construction = bool(data.get("under_construction", False))
        f.halt_until = float(data.get("halt_until", 0.0) or 0.0)
        f.halt_reason = data.get("halt_reason")
        f.mothballed = bool(data.get("mothballed", False))
        f.upkeep = float(data.get("upkeep", 100.0) or 0.0)
        f.unit_progress = float(data.get("unit_progress", 0.0) or 0.0)
        f.stored = float(data.get("stored", 0.0) or 0.0)
        f.backlog_over = data.get("backlog_over")
        f.fuel = data.get("fuel")
        return f


class IndustrySystem:
    def __init__(self, facility_defs: List[dict], recipes: List[dict],
                 heat_values: Optional[Dict[str, float]] = None,
                 fuel_classes: Optional[Dict[str, str]] = None,
                 mothball_restart_sec: float = 10.0,
                 ref_grades: Optional[Dict[str, float]] = None,
                 backlog: Optional[dict] = None) -> None:
        self.defs: Dict[str, dict] = {d["id"]: d for d in facility_defs}
        self.recipes: Dict[str, dict] = {r["id"]: r for r in recipes}
        self.facilities: Dict[str, Facility] = {}
        self.heat_values: Dict[str, float] = heat_values or {}
        self.fuel_classes: Dict[str, str] = fuel_classes or {}
        # 品位归一基准（P1 ①）：产出 = 标称 × clamp(品位/基准, 0.25, 2.0)
        self.ref_grades: Dict[str, float] = ref_grades or {}
        # 副产物积压（批次3）：超限则限产；policy=ignore 时完全关闭
        bl = backlog or {}
        self.backlog_limits: Dict[str, float] = {
            k: float(v) for k, v in (bl.get("limits") or {}).items()}
        self.backlog_min_factor = float(bl.get("min_factor", 0.15))
        self.backlog_policy = str(bl.get("policy_default", "throttle"))
        self.last_build: Optional[dict] = None   # 最近一次建造快照（撤销用）
        self.instant_build = False               # True=调试：建造即时完成
        self.default_build_time = 8.0            # 设施未配 build_time 时的兜底
        self.mothball_restart_sec = float(mothball_restart_sec)
        # 停摆日志限流（试玩提案 §6）：同设施同原因 N 秒内只记一次
        self.stall_log_gap = 20.0
        self._stall_log: Dict[str, tuple] = {}
        self._seq = 0

    # ---- 品位折算（P1 ①）-------------------------------------------
    def grade_factor(self, substance: Optional[str], grade: float) -> float:
        """品位因子：以各物质的中位品位为基准，clamp 到 [0.25, 2.0]。

        基准品位写在各物质的 ref_grade（content/substances.json）：
        中位品位的矿恰好是标称产量；贫矿最低打到 25%，富矿最高翻倍。
        没有基准（如水源）→ 恒为 1.0。
        """
        ref = float(self.ref_grades.get(substance or "", 0.0) or 0.0)
        if ref <= 0:
            return 1.0
        return max(0.25, min(2.0, float(grade) / ref))

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)

    def _on_job_done(self, payload: dict) -> None:
        """建造作业完成 → 设施转入可分配状态（并不自动派单元）。"""
        if payload.get("kind") != "build":
            return
        f = self.facilities.get(payload.get("target_id", ""))
        eng = getattr(self, "_engine", None)
        if f is None:
            return
        f.under_construction = False
        if eng is not None:
            eng.log(f"[工业] {f.name} 建造完成，分配执行单元后即可开工。",
                    recover=f"fac:{f.id}")

    # ---- 恢复门控 ---------------------------------------------------
    def _recovery(self, engine: object):
        """获取恢复系统实例（可能未注册，测试环境）。"""
        return getattr(engine.registry, "get", lambda _: None)("recovery")

    def _require_recovery(self, engine: object, def_id: str) -> Optional[str]:
        """返回缺失的数据库条目名（若无要求/已解锁 → None）。"""
        d = self.defs.get(def_id, {})
        need = d.get("requires_recovery")
        if not need:
            return None
        rec = self._recovery(engine)
        if rec is None or rec.is_unlocked(need):
            return None
        name = "该条目"
        if rec is not None:
            e = rec.entries.get(need)
            name = f"「{e['name']}」" if e else need
        return name

    # ---- 建造/分配 -------------------------------------------------
    def build(self, engine: object, plot_id: str, def_id: str) -> Optional[str]:
        """开工建造：占用一个执行单元的"建造"作业，耗时 = 设施 build_time。

        `instant_build=True`（调试/测试）时退回即时落地，不占单元不耗时。
        """
        plot = engine.world.get(plot_id)
        if plot is None:
            return f"地块不存在: {plot_id}"
        d = self.defs.get(def_id)
        if d is None:
            return f"未知设施: {def_id}"
        # PLOT_BUILD-1 修复：先看"这块地上有没有设施"，再谈状态，避免
        # developed/depleted 地块被误报成"尚未占领"。
        existing = next((f for f in self.facilities.values()
                         if f.plot_id == plot_id), None)
        if existing is not None:
            return (f"该地块已有设施（{existing.id} {existing.name}）——"
                    "一地块一设施，请另选地块或用 demolish 拆除。")
        if plot.state in (Plot.STATE_UNKNOWN, Plot.STATE_KNOWN):
            return "该地块尚未占领（先 claim）。"
        if plot.state not in (Plot.STATE_CLAIMED, Plot.STATE_DEVELOPED,
                              Plot.STATE_DEPLETED):
            return f"该地块当前状态（{plot.state}）不可建造。"
        # 允许在 depleted（采空）与"设施已拆除的 developed"地块重建
        if plot.kind not in d.get("allowed_plot_kinds", []):
            return f"{d['name']} 不能建在 {plot.kind} 地块上。"
        missing = self._require_recovery(engine, def_id)
        if missing is not None:
            return f"知识缺失：需先从数据库恢复 {missing} 才能建造。"
        cost = d.get("build_cost", {})
        # 非即时建造：先占单元（因此先判空，避免白扣资源）
        unit = None
        if not self.instant_build:
            unit = engine.units.assign_any("build")
            if unit is None:
                return "没有空闲执行单元，无法开工建造（先调离一个单元）。"
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                # 退还本次已扣资源（避免失败建造造成净消耗）
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
                if unit is not None:
                    unit.release()
                return f"资源不足：缺 {rid} {amt}。"
        self._seq += 1
        f = Facility(f"F{self._seq}", plot_id, def_id, d["name"])
        self.facilities[f.id] = f
        prev_state = plot.state
        plot.state = Plot.STATE_DEVELOPED
        # 撤销快照（仅保留最近一次；已产出后不可撤销，防"发电后退货"套利）
        self.last_build = {
            "fac_id": f.id, "plot_id": plot_id, "def_id": def_id,
            "cost": {k: float(v) for k, v in cost.items()},
            "plot_state_before": prev_state,
        }
        cost_txt = ", ".join(f"{k} {v:g}" for k, v in cost.items()) or "免费"
        if self.instant_build:
            engine.log(f"[工业] {d['name']} 建于 {plot_id} "
                       f"(成本 {cost_txt})。assign 单元后开工。")
            return None
        f.under_construction = True
        duration = float(d.get("build_time", self.default_build_time))
        engine.jobs.add("build", unit.id, f.id, duration,
                        {"plot": plot_id, "def_id": def_id})
        engine.log(f"[工业] {d['name']} 在 {plot_id} 开工建造"
                   f"(成本 {cost_txt}) —— 单元 {unit.id} 施工中，"
                   f"预计 {duration:.0f}s。")
        return None

    def install_instant(self, engine: object, plot_id: str,
                        def_id: str) -> Optional[str]:
        """调试/测试入口：即时建成（不受建造耗时与单元占用限制）。"""
        old = self.instant_build
        self.instant_build = True
        try:
            return self.build(engine, plot_id, def_id)
        finally:
            self.instant_build = old

    def _build_job_for(self, engine: object, fac_id: str):
        """返回该设施当前的建造作业（无则 None）。"""
        jobs = getattr(engine, "jobs", None)
        if jobs is None:
            return None
        for j in jobs.list_jobs():
            if j.kind == "build" and j.target_id == fac_id:
                return j
        return None

    def build_progress(self, engine: object, fac_id: str) -> Optional[float]:
        """建造剩余秒数（非建造中或找不到作业 → None）。"""
        j = self._build_job_for(engine, fac_id)
        return None if j is None else max(0.0, float(j.remaining))

    # ---- 封存 / 解封（批次2b：维护压力下的"停机省钱"手段）----------
    def mothball(self, engine: object, fac_id: str,
                 on: bool = True) -> Optional[str]:
        """封存（零维护消耗、不产出）或解除封存（需一个单元 + 重启时间）。

        对应同类作品的 "paused building consumes nothing"：
        玩家可以选择"养不起就先停"，但重启有成本，不能来回白嫖。
        """
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        if f.under_construction:
            return f"{f.name} 仍在建造中，无法封存。"
        if on:
            if f.mothballed:
                return f"{f.name} 已经处于封存状态。"
            for uid in list(f.assigned):
                u = engine.units.get(uid)
                if u is not None:
                    u.release()
            f.assigned.clear()
            f.mothballed = True
            f.stall_reason = None
            f.stalled_reported = False
            engine.log(f"[工业] {f.name} 已封存：停止运转、零维护消耗，"
                       f"释放 {len(f.assigned) or 0} 个单元（需时再解封）。")
            return None
        if not f.mothballed:
            return f"{f.name} 未处于封存状态。"
        unit = engine.units.assign_any(fac_id)
        if unit is None:
            return "没有空闲执行单元，无法解封并重启。"
        f.mothballed = False
        f.assigned.append(unit.id)
        f.halt_until = float(engine.clock.time) + self.mothball_restart_sec
        f.halt_reason = "封存重启中"
        engine.log(f"[工业] {f.name} 解除封存：单元 {unit.id} 正在重启设备"
                   f"（{self.mothball_restart_sec:.0f}s）。")
        return None

    def undo_build(self, engine: object) -> Optional[str]:
        """撤销最近一次建造（仅当该设施尚未产出任何东西时允许）。"""
        snap = getattr(self, "last_build", None)
        if not snap:
            return "没有可撤销的建造记录。"
        f = self.facilities.get(snap["fac_id"])
        if f is None:
            self.last_build = None
            return "该设施已不存在（可能已枯竭拆除），无法撤销。"
        if f.produced_any:
            return (f"{f.name} 已投入生产（有产出），不能撤销建造"
                    "（避免建成即退货的资源套利）。")
        # 建造中：取消施工作业并释放施工单元
        was_building = f.under_construction
        if was_building:
            job = self._build_job_for(engine, f.id)
            if job is not None:
                engine.jobs.cancel(job.id)
                u = engine.units.get(job.unit_id)
                if u is not None:
                    u.release()
            f.under_construction = False
        # 释放已分配执行单元
        for uid in list(f.assigned):
            u = engine.units.get(uid)
            if u is not None:
                u.release()
        f.assigned.clear()
        # 归还建造成本
        for rid, amt in snap.get("cost", {}).items():
            engine.economy.add(rid, float(amt))
        # 移除设施并还原地块状态
        self.facilities.pop(f.id, None)
        plot = engine.world.get(snap["plot_id"])
        if plot is not None:
            plot.state = snap.get("plot_state_before", Plot.STATE_CLAIMED)
        self.last_build = None
        cost_txt = ", ".join(f"{k} {v:g}"
                             for k, v in snap.get("cost", {}).items()) or "无"
        engine.log(f"[工业] 已撤销建造：{f.name} @{snap['plot_id']}，"
                   f"返还 {cost_txt}，地块恢复可建"
                   + ("（施工已中止，单元回收）。" if was_building else "。"))
        return None

    def demolish(self, engine: object, fac_id: str,
                 refund: float = 0.5) -> Optional[str]:
        """拆除设施（P1 ③）：返还 refund 比例材料，地块恢复可建。

        与 undo 的区别：undo 只允许"刚建且未产出过"，demolish 对任何设施都可用
        （但只返还一半材料，防止"建造→产出→拆掉"套利）。
        施工中的设施走 undo（全额返还 + 中止作业）。
        """
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        if f.under_construction:
            snap = getattr(self, "last_build", None)
            if snap and snap.get("fac_id") == fac_id:
                return self.undo_build(engine)      # 施工中：全额返还
        d = self.defs.get(f.def_id, {})
        cost = d.get("build_cost", {}) or {}
        # 释放执行单元
        for uid in list(f.assigned):
            u = engine.units.get(uid)
            if u is not None:
                u.release()
        f.assigned.clear()
        back = {k: float(v) * float(refund) for k, v in cost.items()}
        for rid, amt in back.items():
            if amt > 0:
                engine.economy.add(rid, amt)
        self.facilities.pop(f.id, None)
        if getattr(self, "last_build", None) and \
                self.last_build.get("fac_id") == fac_id:
            self.last_build = None
        plot = engine.world.get(f.plot_id)
        if plot is not None and plot.state == Plot.STATE_DEVELOPED:
            plot.state = Plot.STATE_CLAIMED          # 腾出来可再建
        back_txt = "、".join(f"{k} {v:g}" for k, v in back.items() if v > 0) or "无"
        engine.log(f"[工业] 已拆除 {f.name} @{f.plot_id}，返还 {back_txt}"
                   f"（{int(refund * 100)}%）—— 地块可重新规划。")
        return None

    # ---- 单元效能（批次1：效率科技）--------------------------------
    def _staff_factor(self, engine: object, f: "Facility", d: dict) -> float:
        """派员比例 × 单元效能 → 产能/速度倍率。

        效能为 1.0 时恒为 1.0（保持既有平衡基线）；效能由恢复出的知识
        条目（grants.unit_efficiency）汇总，记忆崩溃丢条目时会回落。
        """
        pool = getattr(engine, "units", None)
        fn = getattr(pool, "staff_factor", None)
        if fn is None:                     # 兼容旧的极简测试替身
            return 1.0
        return float(fn(len(f.assigned), int(d.get("slots", 1))))

    def _unit_eff(self, engine: object) -> float:
        return float(getattr(getattr(engine, "units", None), "efficiency", 1.0)
                     or 1.0)

    def assign(self, engine: object, fac_id: str) -> Optional[str]:
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        if f.under_construction:
            left = self.build_progress(engine, fac_id)
            left_txt = f"（剩余约 {left:.0f}s）" if left is not None else ""
            return f"{f.name} 仍在建造中{left_txt}，完工后才能分配单元。"
        if f.mothballed:
            return f"{f.name} 已封存（零维护消耗），先解除封存再分配单元。"
        d = self.defs[f.def_id]
        slots = int(d.get("slots", 1))
        pool = getattr(engine, "units", None)
        limit = (pool.effective_slots(slots)
                 if hasattr(pool, "effective_slots") else slots)
        if len(f.assigned) >= limit:
            if limit < slots:
                return (f"{f.name} 单元已满（效能 ×{self._unit_eff(engine):.2f}，"
                        f"仅需 {limit} 个单元即可满负荷，其余单元请调给别处）。")
            return f"{f.name} 单元已满 (上限 {slots})。"
        unit = engine.units.assign_any(fac_id)
        if unit is None:
            return "没有空闲执行单元。"
        f.assigned.append(unit.id)
        if limit < slots:
            engine.log(f"[工业] 单元 {unit.id} 已分配至 {f.name}"
                       f"（效能 ×{self._unit_eff(engine):.2f}，该设施已满负荷）。")
        else:
            engine.log(f"[工业] 单元 {unit.id} 已分配至 {f.name}。")
        return None

    def unassign(self, engine: object, fac_id: str,
                 unit_id: Optional[str] = None) -> Optional[str]:
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        if not f.assigned:
            return f"{f.name} 没有已分配单元。"
        uid = unit_id if unit_id and unit_id in f.assigned else f.assigned[0]
        f.assigned.remove(uid)
        u = engine.units.get(uid)
        if u is not None:
            u.release()
        engine.log(f"[工业] 单元 {uid} 已从 {f.name} 调离。")
        return None

    # ---- 每 tick 生产 ----------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        env = None
        # 可选查询第5模块（环境系统）：不存在则无影响（向后兼容）
        getter = getattr(engine.registry, "get", None)
        if getter is not None:
            env = getter("environment")
        pmul = env.effect("production") if env is not None else 1.0
        now = float(engine.clock.time)
        for f in list(self.facilities.values()):
            if f.under_construction:
                # 建造中：不产出、不耗料。作业丢失时自愈（读档兜底）。
                if self._build_job_for(engine, f.id) is None:
                    f.under_construction = False
                else:
                    continue
            if f.mothballed:
                continue                    # 封存：不运转、不消耗
            if f.halt_until > now:
                self._report_stall(engine, f, True,
                                   f.halt_reason or "停机检修", "halted")
                stat_bump(engine, "stall_seconds", dt)
                continue
            if f.halt_reason == "封存重启中":
                f.halt_reason = None        # 重启完成，清除提示
                engine.log(f"[工业] {f.name} 重启完成，恢复运转。",
                           recover=f"fac:{f.id}")
            if not f.assigned:
                stat_bump(engine, "stall_seconds", dt)   # 无单元 = 停摆
                continue                    # 无单元 = 停摆
            if f.stalled_reported:
                stat_bump(engine, "stall_seconds", dt)
            d = self.defs[f.def_id]
            # 恢复条目丢失 → 设施失忆停摆（需重新恢复才能开工）
            if self._require_recovery(engine, f.def_id) is not None:
                self._report_stall(engine, f, True, "知识条目已丢失",
                                   "knowledge_lost")
                continue
            # 注意：不再在此处无条件清除停摆状态（那会与下方各 tick 的
            # 真实判定互相抵消，导致"停摆/恢复"日志与统计每 tick 抖动）。
            umul = self._staff_factor(engine, f, d)
            if d.get("extract_rate"):
                self._tick_extractor(engine, f, d, dt, pmul, umul)
            elif d.get("kind") == "burner":
                self._tick_burner(engine, f, d, dt, pmul, umul)
            elif d.get("kind") == "renewable":
                self._tick_renewable(engine, f, d, dt, pmul, umul)
            elif d.get("kind") == "unit_factory":
                self._tick_unit_factory(engine, f, d, dt, pmul, umul)
            elif d.get("kind") == "vent":
                self._tick_vent(engine, f, d, dt, umul)
            elif d.get("kind") == "sink":
                self._tick_sink(engine, f, d, dt, umul)
            elif d.get("kind") == "storage":
                # 储能设施的充放由 PowerSystem 统一调度（批次4），这里不动
                continue
            else:
                self._tick_recipe(engine, f, d, dt, pmul, umul)

    # ---- 副产物积压（批次3）-----------------------------------------
    def backlog_factor(self, engine: object, recipe: dict) -> float:
        """副产物积压 → 限产系数（policy=ignore 时恒为 1.0）。"""
        if self.backlog_policy == "ignore" or not self.backlog_limits:
            return 1.0
        factor = 1.0
        for rid in (recipe.get("byproducts") or {}):
            limit = self.backlog_limits.get(rid)
            if not limit or limit <= 0:
                continue
            stock = engine.economy.get(rid)
            if stock > limit:
                factor *= max(self.backlog_min_factor,
                              min(1.0, limit / stock))
        return factor

    def backlog_status(self, engine: object) -> Dict[str, dict]:
        """各受限副产物的当前状态（供 report/UI 展示）。"""
        out = {}
        for rid, limit in self.backlog_limits.items():
            stock = engine.economy.get(rid)
            out[rid] = {"stock": round(stock, 1), "limit": limit,
                        "over": stock > limit,
                        "factor": round(max(self.backlog_min_factor,
                                            min(1.0, limit / stock))
                                        if stock > limit else 1.0, 3)}
        return out

    def _note_backlog(self, engine: object, f: Facility, over: str,
                      factor: float = 1.0) -> None:
        """积压限产的进入/退出只记一次日志（避免刷屏）。

        试玩提案 §3：被限产的设施要能一眼看出是"副产物积压"而不是"输入不足" ——
        所以这里把原因写进 `stall_reason`/`stall_code=backlog`（**不算停摆**，
        state 仍是 running），report / UI / AI 都读得到。
        """
        if over and f.backlog_over != over:
            f.backlog_over = over
            f.stall_reason = f"副产物积压限产（{over} 超限，产出 ×{factor:.2f}）"
            f.stall_code = "backlog"
            engine.log(f"[工业] {f.name} 因 {over} 积压而限产"
                       f"（×{factor:.2f}，政策 {self.backlog_policy}）—— "
                       "给它找出路：转化(水煤气变换)／放空塔／回注井，"
                       "或建更多下游。",
                       level="warn", category=f"fac:{f.id}")
        elif not over and f.backlog_over:
            f.backlog_over = None
            if f.stall_code == "backlog":
                f.stall_reason = None
                f.stall_code = None
            engine.log(f"[工业] {f.name} 积压缓解，恢复满产。",
                       recover=f"fac:{f.id}")

    def _tick_vent(self, engine: object, f: Facility, d: dict,
                   dt: float, umul: float = 1.0) -> None:
        """放空塔（手段②）：销毁列表中的副产物，解除积压（材料被浪费）。"""
        pwr = float(d.get("power_use", 0.0)) * dt * umul
        if pwr > 0 and self._consume(engine, {"electricity": pwr}, d) <= 0:
            if not self._shed_note(engine, f, d, {"electricity": pwr}):
                self._report_stall(engine, f, True, "缺电", "no_power")
            return
        rate = float(d.get("vent_rate", 1.0)) * dt * umul
        total = 0.0
        for rid in d.get("vent_substances", []):
            stock = engine.economy.get(rid)
            if stock <= 0:
                continue
            # 优先处理最"超限"的那种；其它也顺带放掉一部分
            take = min(rate, stock)
            if take > 0:
                engine.economy.take(rid, take)
                total += take
                stat_bump(engine, "vented", take)
        if total <= 0:
            self._report_stall(engine, f, True, "无可放空物", "no_vent_target")
            return
        self._report_stall(engine, f, False, "")

    def _tick_sink(self, engine: object, f: Facility, d: dict,
                   dt: float, umul: float = 1.0) -> None:
        """回注井/堆场（手段③）：把副产物压进地下，容量满了就停。"""
        cap = float(d.get("capacity", 1000.0))
        if f.stored >= cap:
            self._report_stall(engine, f, True, "堆场已满", "sink_full")
            return
        pwr = float(d.get("power_use", 0.0)) * dt * umul
        if pwr > 0 and self._consume(engine, {"electricity": pwr}, d) <= 0:
            if not self._shed_note(engine, f, d, {"electricity": pwr}):
                self._report_stall(engine, f, True, "缺电", "no_power")
            return
        rate = float(d.get("sink_rate", 1.0)) * dt * umul
        moved = 0.0
        for rid in d.get("sink_substances", []):
            if f.stored >= cap:
                break
            stock = engine.economy.get(rid)
            if stock <= 0:
                continue
            take = min(rate - moved, stock, cap - f.stored)
            if take <= 0:
                break
            engine.economy.take(rid, take)
            f.stored += take
            moved += take
            stat_bump(engine, "sunk", take)
        if moved <= 0:
            self._report_stall(engine, f, True, "无可回注物", "no_sink_target")
            return
        self._report_stall(engine, f, False, "")

    def _tick_unit_factory(self, engine: object, f: Facility, d: dict,
                           dt: float, pmul: float = 1.0,
                           umul: float = 1.0) -> None:
        """执行单元装配厂（P1 ②b1）：消耗钢/铜/电，攒够进度产出一个单元。"""
        recipe = self.recipes.get(d.get("recipe", ""))
        rate = float(d.get("unit_rate", 0.0))
        if recipe is None or rate <= 0:
            self._report_stall(engine, f, True, "未配置单元产能", "no_unit_rate")
            return
        rate_mul = dt * umul
        need = {rid: float(r) * rate_mul
                for rid, r in recipe.get("inputs", {}).items()}
        factor = self._consume(engine, need, d)
        missing = not need or factor <= 0
        if missing:
            if not self._shed_note(engine, f, d, need):
                self._report_stall(engine, f, missing, "输入不足", "no_input")
        if factor <= 0:
            return
        f.unit_progress += rate * dt * umul * factor * pmul
        made = 0
        while f.unit_progress >= 1.0:
            f.unit_progress -= 1.0
            engine.units.add_unit("执行器-装配")
            made += 1
        f.produced_any = True
        if made:
            stat_bump(engine, "units_built", made)
            engine.log(f"[工业] {f.name} 装配完成 {made} 个执行单元"
                       f"（现共 {engine.units.count()} 个）。",
                       recover=f"fac:{f.id}")

    def _consume(self, engine: object, need: Dict[str, float],
                 d: Optional[dict] = None) -> float:
        """按需按可用比例尽力消耗，返回实际比例 [0,1]。

        批次4 起：**电力**还要再乘"该设施优先级档位的供电比例"（缺电时从低到高
        降载，而不是全厂一起半速）。`d` 传设施 def 才能拿到 power_priority。
        """
        factor = 1.0
        for rid, amt in need.items():
            if amt <= 0:
                continue
            avail = engine.economy.get(rid)
            factor = min(factor, avail / amt)
        if d is not None and float(need.get("electricity", 0.0) or 0.0) > 0.0:
            p = self._power_sys(engine)
            if p is not None:
                factor = min(factor, p.factor(p.priority_of(d)))
        if factor <= 0:
            return 0.0
        for rid, amt in need.items():
            engine.economy.take(rid, amt * factor)
        return factor

    # ---- 电力体系（批次4）-------------------------------------------
    @staticmethod
    def _power_sys(engine: object):
        getter = getattr(engine.registry, "get", None)
        return getter("power") if getter is not None else None

    def _power_headroom(self, engine: object) -> Optional[float]:
        p = self._power_sys(engine)
        return None if p is None else p.headroom(engine)

    def _shed_note(self, engine: object, f: Facility, d: dict,
                   need: Dict[str, float]) -> bool:
        """缺电时给出更准确的原因：是被"优先级降载"停的，还是单纯没电。"""
        p = self._power_sys(engine)
        if p is None or float(need.get("electricity", 0.0) or 0.0) <= 0.0:
            return False
        pr = p.priority_of(d)
        if p.factor(pr) <= 0.0:
            self._report_stall(engine, f, True,
                               f"缺电（{p.tier_name(pr)}档降载）", "shed")
            return True
        return False

    def _grid_note(self, engine: object, f: Facility, want: float) -> float:
        """发电设施受电网容量限制（A13）：返回可消纳比例 [0,1]。

        余量小于容量的 `grid_full_ratio`（死区）时判"电网已满"并降载停机；
        判满后要等余量涨回 `grid_resume_ratio` 才恢复（滞后，避免死区边缘抖动刷屏）。
        """
        p = self._power_sys(engine)
        if p is None or want <= 0.0:
            return 1.0
        if p.grid_full(engine, f.id):
            self._report_stall(engine, f, True, "电网已满（消纳不了）",
                              "grid_full")
            return 0.0
        return max(0.0, min(1.0, p.headroom(engine) / want))

    def _tick_recipe(self, engine: object, f: Facility, d: dict,
                     dt: float, pmul: float = 1.0,
                     umul: float = 1.0) -> None:
        recipe = self.recipes.get(d.get("recipe", ""))
        if recipe is None:
            self._report_stall(engine, f, True, "未配置配方", "no_recipe")
            return
        # 单元效能提高节拍：输入与输出同乘（化学配比不变，只是更快）
        rate_mul = dt * umul
        # 电网容量（A13）：产出电力的配方受"还能消纳多少"限制（不白烧燃料）
        elec_rate = float(recipe.get("outputs", {}).get("electricity", 0.0))
        pre = 1.0
        if elec_rate > 0:
            pre = self._grid_note(engine, f, elec_rate * rate_mul * pmul)
            if pre <= 0.0:
                return
        need = {rid: float(r) * rate_mul * pre
                for rid, r in recipe.get("inputs", {}).items()}
        factor = self._consume(engine, need, d) * pre
        missing = not need or factor <= 0
        if missing:
            if not self._shed_note(engine, f, d, need):
                self._report_stall(engine, f, missing, "输入不足", "no_input")
        if factor <= 0:
            return
        self._report_stall(engine, f, False, "")
        # 副产物积压限产（批次3）：超限则整台设施降速（含主产物）
        bfac = self.backlog_factor(engine, recipe)
        if bfac < 1.0:
            worst = max(
                (rid for rid in (recipe.get("byproducts") or {})
                 if self.backlog_limits.get(rid)
                 and engine.economy.get(rid) > self.backlog_limits[rid]),
                key=lambda rid: engine.economy.get(rid) / self.backlog_limits[rid],
                default=None)
            self._note_backlog(engine, f, worst or "", bfac)
        else:
            self._note_backlog(engine, f, "")
        factor *= bfac
        for rid, rate in recipe.get("outputs", {}).items():
            engine.economy.add(rid, float(rate) * rate_mul * factor * pmul)
        # 副产物（byproducts: id→每产出主产物*时间的量；此处按 dt 产率）
        for rid, rate in recipe.get("byproducts", {}).items():
            if float(rate) > 0:
                engine.economy.add(rid, float(rate) * rate_mul * factor * pmul)
        f.produced_any = True

    def _tick_extractor(self, engine: object, f: Facility, d: dict,
                        dt: float, pmul: float = 1.0,
                        umul: float = 1.0) -> None:
        plot = engine.world.get(f.plot_id)
        if plot is None:
            return
        # 电力消耗（随效能同比：干得更快也更费电）
        pwr = float(d.get("power_use", 0.0)) * dt * umul
        need = {"electricity": pwr} if pwr > 0 else {}
        factor = self._consume(engine, need, d)
        if need and factor <= 0:
            if not self._shed_note(engine, f, d, need):
                self._report_stall(engine, f, True, "缺电", "no_power")
            return
        self._report_stall(engine, f, False, "")
        rate = float(d.get("extract_rate", 0.0))
        if plot.kind == Plot.KIND_WATER:
            gmul = 1.0
        else:
            gmul = self.grade_factor(plot.substance, plot.grade)
        out = rate * dt * pmul * umul * factor * gmul
        if plot.kind == Plot.KIND_WATER:
            sub = "water"
        else:
            sub = plot.substance or "scrap_alloy"
            out = min(out, max(0.0, plot.reserve))   # 储量封顶
        if out <= 0:
            return
        engine.economy.add(sub, out)
        # 顺带产出（批次5 A6）：拆残骸时按比例附带拆出回收部件等。
        # 比例以"主产物"为基准，随同一 factor/效能/环境缩放，不吃储量。
        for rid, ratio in (d.get("extract_extra") or {}).items():
            extra = out * float(ratio)
            if extra > 0:
                engine.economy.add(rid, extra)
        f.produced_any = True
        if plot.kind != Plot.KIND_WATER:
            plot.reserve -= out
            plot.refine()   # 开采逐步揭示真实品位/储量（向真值收敛）
            if plot.reserve <= 1e-6:
                plot.reserve = 0.0
                self._deplete(engine, f, plot, d)

    def _tick_burner(self, engine: object, f: Facility, d: dict,
                     dt: float, pmul: float = 1.0,
                     umul: float = 1.0) -> None:
        """燃烧发电：烧当前燃料，产电 = 消耗×热值×效率。"""
        if not f.fuel:
            self._report_stall(engine, f, True, "未设置燃料(set fuel)", "no_fuel_set")
            return
        hv = self.heat_values.get(f.fuel)
        if not hv or hv <= 0:
            self._report_stall(engine, f, True, "该燃料不可燃")
            return
        # 燃料类别白名单（固体/液体/气体按设施限定）
        allowed = d.get("fuel_classes")
        if allowed:
            cls = self.fuel_classes.get(f.fuel)
            if cls not in allowed:
                self._report_stall(engine, f, True,
                                   f"该设施只接受 {','.join(allowed)} 燃料")
                return
        burn_rate = float(d.get("burn_rate", 0.5))   # t/s 消耗
        # 电网容量（A13）：烧出来的电得有人要 —— 满了就少烧，不白烧燃料
        hv0 = self.heat_values.get(f.fuel, 0.0)
        want = burn_rate * dt * umul * hv0 * float(d.get("burn_efficiency", 0.4)) \
            * pmul
        pre = self._grid_note(engine, f, want)
        if pre <= 0.0:
            return
        # 尽力烧（仓库不足按比例）
        avail = engine.economy.get(f.fuel)
        if avail <= 0:
            self._report_stall(engine, f, True, f"缺燃料 {f.fuel}", "no_fuel")
            return
        self._report_stall(engine, f, False, "")
        consumed = min(burn_rate * dt * umul * pre, avail)
        engine.economy.take(f.fuel, consumed)
        eff = float(d.get("burn_efficiency", 0.4))
        out = consumed * hv * eff * pmul
        engine.economy.add("electricity", out)
        f.produced_any = True

    def _tick_renewable(self, engine: object, f: Facility, d: dict,
                        dt: float, pmul: float = 1.0,
                        umul: float = 1.0) -> None:
        """可再生能源（光热/光伏/风电）：受昼夜与事件调制，无燃料成本。

        power_kind: solar=光热(晴日强度)；pv=光伏(更受云/沙削减)；
        wind=风电(沙暴增强)。
        """
        getter = getattr(engine.registry, "get", None)
        env = getter("environment") if getter else None
        dl = getter("daylight") if getter else None
        pk = d.get("power_kind", "solar")
        # 事件倍率（每类独立键：solar/pv/wind；缺省 1.0）
        emul = env.effect(pk) if env is not None else 1.0
        # 光照类还要乘昼夜日照系数
        dmul = dl.effect("solar") if dl is not None and pk in ("solar", "pv") \
            else 1.0
        cap = float(d.get("capacity", 0.0))
        if cap <= 0:
            self._report_stall(engine, f, True, "未配置产能(capacity)",
                               "no_capacity")
            return
        # 可再生发电只随其专属事件键(昼夜)波动，不再叠乘通用 production
        # （避免沙暴 production×0.6 与 wind×1.5 互相抵消）；效能按运维水平折算
        out = cap * dt * emul * dmul * umul
        # 光照归零(夜)不算停摆——自然节律；但事件压到 0 也不报停摆
        if out <= 0:
            self._report_stall(engine, f, False, "")
            return
        # 电网容量（A13）：消纳不了就少发（可再生没有燃料成本，只损失出力）
        pre = self._grid_note(engine, f, out)
        if pre <= 0.0:
            return
        out *= pre
        self._report_stall(engine, f, False, "")
        engine.economy.add("electricity", out)
        f.produced_any = True

    def set_fuel(self, engine: object, fac_id: str,
                 fuel: str) -> Optional[str]:
        """给 burner 设施设置燃料（须可燃且符合设施的燃料类别白名单）。"""
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        d = self.defs.get(f.def_id, {})
        if d.get("kind") != "burner":
            return f"{f.name} 不是燃烧发电设施。"
        hv = self.heat_values.get(fuel)
        if not hv or hv <= 0:
            return f"物质 {fuel} 不可燃。"
        allowed = d.get("fuel_classes")
        if allowed:
            cls = self.fuel_classes.get(fuel)
            if cls not in allowed:
                kinds = "、".join(allowed)
                return f"{f.name} 只能烧 {kinds} 类燃料，" \
                       f"{fuel} 属 {cls or '?'} 类。"
        f.fuel = fuel
        engine.log(f"[工业] {f.name} 改用燃料: {fuel}。")
        return None

    def fuel_options(self) -> List[str]:
        """列出所有可燃物质 id（供 UI/命令提示）。"""
        return sorted(self.heat_values.keys())

    def fuel_options_for(self, def_id: str) -> List[str]:
        """按设施的燃料类别白名单过滤可燃物（无白名单=全部）。"""
        allowed = self.defs.get(def_id, {}).get("fuel_classes")
        if not allowed:
            return self.fuel_options()
        return sorted(f for f, cls in self.fuel_classes.items()
                      if f in self.heat_values and cls in allowed)

    def _deplete(self, engine: object, f: Facility, plot: Plot,
                 d: dict) -> None:
        plot.state = Plot.STATE_DEPLETED
        self.facilities.pop(f.id, None)
        for uid in f.assigned:
            u = engine.units.get(uid)
            if u is not None:
                u.release()
        # 枯竭告警（试玩提案 §5）：原来只有一行"停止并拆除"，煤矿采空后玩家
        # 根本不知道"煤供给已归零"。关键资源单独给醒目告警。
        sub = plot.substance or ("water" if plot.kind == Plot.KIND_WATER else "")
        if sub in KEY_SUBSTANCES:
            name = KEY_SUBSTANCES[sub]
            rest = [p.id for p in engine.world.plots.values()
                    if p.substance == sub and p.id != plot.id
                    and p.state in ("known", "claimed", "developed")]
            tail = (f"还有 {len(rest)} 处可采（{'、'.join(rest[:3])}…）"
                    if rest else "**已无其他已知矿点，赶紧 survey / claim**")
            engine.log(
                f"⚠ {name} {plot.id} 采空 —— {name}供给中断，"
                f"请立即指派新矿点（{tail}）。",
                level="warn", category="industry")
        if d.get("reward_unit_on_deplete"):
            nu = engine.units.add_unit("执行器-回收")
            engine.log(f"[工业] 残骸拆解完毕，回收出完整执行单元 {nu.id}！")
        engine.log(f"[工业] {plot.id} 资源枯竭，{f.name} 停止并拆除。")

    def _report_stall(self, engine: object, f: Facility, stalled: bool,
                      reason: str, code: str = "") -> None:
        """停摆/恢复的日志（只在状态变化时写）+ **抖动限流**。

        试玩提案 §6：电网满/复电会每几秒翻一次状态，单局能刷出上万行日志，
        把研究完成、枯竭告警这类真信息全埋掉。这里对"同一设施同一原因
        在 stall_log_gap 秒内重复进入"不再写日志（状态本身照旧更新）。
        """
        cat = f"fac:{f.id}"
        if stalled and not f.stalled_reported:
            f.stalled_reported = True
            f.stall_reason = reason
            f.stall_code = code or None
            stat_bump(engine, "stall_events")
            now = float(engine.clock.time)
            key = code or reason
            last = self._stall_log.get(f.id)
            if not (last and last[0] == key
                    and now - last[1] < self.stall_log_gap):
                self._stall_log[f.id] = (key, now)
                engine.log(f"[工业] {f.name} 停摆：{reason}。",
                           level="warn", category=cat)
        elif not stalled and f.stalled_reported:
            f.stalled_reported = False
            f.stall_reason = None
            f.stall_code = None
            self._stall_log.pop(f.id, None)
            engine.log(f"[工业] {f.name} 恢复运转。", recover=cat)

    # ---- 查询 ------------------------------------------------------
    def facility_names(self) -> List[str]:
        return list(self.facilities.keys())

    def power_balance(self, engine: object) -> dict:
        """全厂电力预算：产电 vs 耗电（用于 UI 诊断）。

        **实际 vs 额定**（POWER-1 修复）：停摆的设施不再计入 actual —— 否则会
        出现"面板显示盈余、设施却报缺电"的自相矛盾。返回：
            produce/consume          实际值（停摆设施已排除）
            produce_rated/consume_rated  若全部设施都正常运转的额定值
            consumers/producers      实际明细（供 UI 列最大耗电者）
        """
        getter = getattr(engine.registry, "get", None)
        env = getter("environment") if getter else None
        dl = getter("daylight") if getter else None
        produce = consume = 0.0
        produce_rated = consume_rated = 0.0
        consumers = []
        producers = []
        now = float(engine.clock.time)
        for f in self.facilities.values():
            if not f.assigned:
                continue                    # 未运转不耗电
            d = self.defs[f.def_id]
            if self._require_recovery(engine, f.def_id) is not None:
                continue
            if f.under_construction:
                continue                    # 建造中不耗电/不发电
            if f.mothballed or f.halt_until > now:
                continue                    # 封存/停机检修不耗电、不发电
            # live=False 表示"这一拍它其实是停摆的"（缺输入/缺燃料/缺电/知识丢失）
            live = not f.stalled_reported
            sf = self._staff_factor(engine, f, d)     # 单元效能倍率
            if d.get("extract_rate"):
                c = float(d.get("power_use", 0.0)) * sf
                if c > 0:
                    consume_rated += c
                    if live:
                        consume += c
                        consumers.append((f.name, c))
            elif d.get("kind") == "burner":
                # 燃烧发电：燃料热值 × 燃速 × 效率 × 环境生产倍率
                hv = self.heat_values.get(f.fuel or "", 0.0)
                allowed = d.get("fuel_classes")
                cls_ok = (not allowed
                          or self.fuel_classes.get(f.fuel) in allowed)
                if hv > 0 and cls_ok:
                    pmul = env.effect("production") if env is not None else 1.0
                    out = (float(d.get("burn_rate", 0.5)) * hv
                           * float(d.get("burn_efficiency", 0.4)) * pmul * sf)
                    produce_rated += out
                    if live:
                        produce += out
                        producers.append((f.name, out))
            elif d.get("kind") == "renewable":
                pk = d.get("power_kind", "solar")
                emul = env.effect(pk) if env is not None else 1.0
                dmul = (dl.effect("solar")
                        if dl is not None and pk in ("solar", "pv") else 1.0)
                out = float(d.get("capacity", 0.0)) * emul * dmul * sf
                produce_rated += out
                if live and out > 0:
                    produce += out
                    producers.append((f.name, out))
            elif d.get("kind") == "unit_factory":
                r = self.recipes.get(d.get("recipe", ""), {})
                for rid, rate in r.get("inputs", {}).items():
                    if rid == "electricity" and float(rate) > 0:
                        consume_rated += float(rate) * sf
                        if live:
                            consume += float(rate) * sf
                            consumers.append((f.name, float(rate) * sf))
            elif d.get("kind") in ("vent", "sink"):
                c = float(d.get("power_use", 0.0)) * sf
                if c > 0:
                    consume_rated += c
                    if live:
                        consume += c
                        consumers.append((f.name, c))
            else:
                r = self.recipes.get(d.get("recipe", ""), {})
                for rid, rate in r.get("inputs", {}).items():
                    if rid == "electricity" and float(rate) > 0:
                        consume_rated += float(rate) * sf
                        if live:
                            consume += float(rate) * sf
                            consumers.append((f.name, float(rate) * sf))
                for rid, rate in r.get("outputs", {}).items():
                    if rid == "electricity":
                        produce_rated += float(rate) * sf
                        if live:
                            produce += float(rate) * sf
                            producers.append((f.name, float(rate) * sf))
        return {"produce": produce, "consume": consume,
                "produce_rated": produce_rated,
                "consume_rated": consume_rated,
                "produce_actual": produce, "consume_actual": consume,
                "consumers": consumers, "producers": producers}

    # ---- 死锁体检（READLOCK-1：读档落到"无燃料 + 无电 + 采矿需电"）------
    def power_deadlock(self, engine: object) -> Optional[str]:
        """返回死锁描述；不处于死锁则 None。"""
        bal = self.power_balance(engine)
        if bal["produce"] > 0:
            return None
        if engine.economy.get("electricity") > 30.0:
            return None                     # 储备还够撑一阵
        burnable = [s for s in self.heat_values if engine.economy.get(s) > 1.0]
        if burnable:
            return None                     # 还有燃料可切（燃料类别可能不符，但玩家可试）
        mines = [f for f in self.facilities.values()
                 if self.defs[f.def_id].get("extract_rate")
                 and not f.under_construction]
        if not mines:
            return None
        return ("无人在发电、电力储备见底、库存里没有任何可燃物，"
                "而采掘设施需要电才能采煤 —— 形成闭环死锁")

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"seq": self._seq,
                "backlog_policy": self.backlog_policy,
                "facilities": [f.to_dict() for f in self.facilities.values()]}

    def load(self, data: dict) -> None:
        self._seq = int(data.get("seq", 0))
        self.backlog_policy = str(data.get("backlog_policy",
                                           self.backlog_policy))
        self.facilities = {}
        for fd in data.get("facilities", []):
            f = Facility.from_dict(fd)
            self.facilities[f.id] = f
