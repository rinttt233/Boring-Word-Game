"""记忆系统（劣化/维护压力层）。

设定：受损 ASI 的存储介质持续劣化 —— memory_integrity(0~max) 随游戏时间
单调下降。降到 warn_threshold 以下触发警报；继续降到 0 时发生"记忆崩溃"，
未固化条目丢失（恢复树接入此事件点）。

**批次2 起劣化随规模增长**（这是"压力不会在中期消失"的关键）：
    degrade = degrade_per_sec
            + per_fixated_entry  × 已永久固化的条目数
            + per_running_facility × 正在运转的设施数
记得越多、工厂越大，维系记忆的持续成本越高 —— 与主题一致：
你恢复的知识越多，就越必须不停维护它。参数全部来自 content/memory.json。

对抗手段：maintain 作业 —— 消耗资源并占用一个执行单元，完成后按完整度
分档恢复（越接近满值回报越低）。劣化与维护的资源/单元竞争 = "为什么不能
无意义挂机"的答案：时间不白给，不维护必失忆。

发声原则：tick 静默结算，仅在越阈/恢复/压力台阶变化时写日志。
"""
from typing import Optional

from core.stats import bump as stat_bump


class MemorySystem:
    def __init__(self, cfg: dict) -> None:
        self.max_integrity = float(cfg.get("max_integrity", 100.0))
        self.degrade_per_sec = float(cfg.get("degrade_per_sec", 0.02))
        scale = cfg.get("degrade_scale", {}) or {}
        # 劣化随规模增长：固化条目数与运转设施数（0 = 旧的恒定劣化）
        self.per_fixated_entry = float(scale.get("per_fixated_entry", 0.0))
        self.per_running_facility = float(
            scale.get("per_running_facility", 0.0))
        self.warn_threshold = float(cfg.get("warn_threshold", 30.0))
        self.crisis_threshold = float(cfg.get("crisis_threshold", 10.0))
        self.maintain_cfg = cfg.get("maintain", {})
        self.integrity = self.max_integrity
        self._warned = False
        self._crisis_logged = False
        self._crashed = False
        self._degradation_disabled = False
        self._last_rate = self.degrade_per_sec
        self._rate_log_at = -1e9

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)
        engine.bus.on("database_complete", self._on_database_complete)

    # ---- 劣化速率（批次2：随规模增长）------------------------------
    def _fixated_count(self, engine: object) -> int:
        getter = getattr(engine.registry, "get", None)
        rec = getter("recovery") if getter is not None else None
        if rec is None:
            return 0
        return sum(1 for eid, st in rec.status.items()
                   if st == "permanent" and eid in rec.entries)

    def _running_facility_count(self, engine: object) -> int:
        getter = getattr(engine.registry, "get", None)
        ind = getter("industry") if getter is not None else None
        if ind is None:
            return 0
        return sum(1 for f in ind.facilities.values()
                   if f.assigned and not f.under_construction
                   and not getattr(f, "mothballed", False))

    def degrade_rate(self, engine: object) -> float:
        """当前劣化速率（/s）：base + 条目项 + 设施项。"""
        return (self.degrade_per_sec
                + self.per_fixated_entry * self._fixated_count(engine)
                + self.per_running_facility
                * self._running_facility_count(engine))

    def _note_rate(self, engine: object, rate: float) -> None:
        """压力台阶变化时提示（限频 60 游戏秒，避免刷屏）。"""
        if self._last_rate <= 0:
            self._last_rate = rate
            return
        drift = abs(rate - self._last_rate) / max(self._last_rate, 1e-9)
        now = float(getattr(engine.clock, "time", 0.0))
        if drift < 0.15 or now - self._rate_log_at < 60.0:
            return
        trend = "上升" if rate > self._last_rate else "下降"
        self._rate_log_at = now
        self._last_rate = rate
        engine.log(f"[记忆] 劣化速率{trend}至 {rate:.3f}/s"
                   f"（已固化条目 {self._fixated_count(engine)} · "
                   f"运转设施 {self._running_facility_count(engine)}）。"
                   "记得越多、规模越大，维系越贵。", category="memory")

    # ---- 每 tick ---------------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        if self._degradation_disabled:
            return    # 可靠数据库建成，劣化永久终止（v1 终局胜利）
        # 可选查询第5模块（环境系统）记忆劣化倍率
        getter = getattr(engine.registry, "get", None)
        env = getter("environment") if getter is not None else None
        mmul = env.effect("memory") if env is not None else 1.0
        rate = self.degrade_rate(engine)
        self._note_rate(engine, rate)
        self.integrity = max(0.0, self.integrity - rate * dt * mmul)
        if self.integrity > self.warn_threshold:
            # 回到安全区，重置报警状态
            self._warned = False
            self._crisis_logged = False
            self._crashed = False
            return
        if self.integrity <= 0.0 and not self._crashed:
            # 真正的记忆崩溃：广播事件，恢复系统据此丢失未固化条目
            self._crashed = True
            stat_bump(self._engine, "memory_crashes")
            engine.log("[记忆] 记忆崩溃！存储介质失效，正在进行灾难恢复。",
                       level="danger", category="memory")
            engine.bus.emit("memory_crash", {"integrity": 0.0})
        elif self.integrity <= self.crisis_threshold \
                and not self._crisis_logged:
            self._crisis_logged = True
            engine.log(
                "[记忆] 严重警告：存储介质崩溃临界 —— 已恢复条目将开始丢失。"
                "立即执行 maintain 加固。", level="danger", category="memory")
        elif not self._warned:
            self._warned = True
            engine.log(
                f"[记忆] 警告：记忆完整度 {self.integrity:.0f}% 低于阈值，"
                "数据库条目存在丢失风险。", level="warn", category="memory")

    # ---- 指令 ------------------------------------------------------
    def maintain(self, engine: object) -> Optional[str]:
        """发起一次记忆加固作业。"""
        cost = self.maintain_cfg.get("cost", {})
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
                return f"资源不足：缺 {rid} {amt:g}。"
        unit = engine.units.assign_any("maintain")
        if unit is None:
            # 退还资源
            for rid, amt in cost.items():
                engine.economy.add(rid, float(amt))
            return "没有空闲执行单元执行记忆加固。"
        duration = float(self.maintain_cfg.get("duration", 8.0))
        # 单元效能折算：更聪明的执行单元烧录得更快（批次1）
        eff = float(getattr(engine.units, "efficiency", 1.0) or 1.0)
        duration = duration / max(eff, 1e-9)
        engine.jobs.add("maintain", unit.id, "memory", duration, {})
        engine.log(f"[记忆] 加固作业开始（单元 {unit.id}，{duration:.0f}s）。")
        return None

    def _on_job_done(self, payload: dict) -> None:
        if payload.get("kind") != "maintain":
            return
        restore = self.restore_amount()
        self.integrity = min(self.max_integrity, self.integrity + restore)
        stat_bump(self._engine, "maintains")
        self._engine.log(
            f"[记忆] 加固完成（+{restore:.0f}），记忆完整度恢复至 "
            f"{self.integrity:.1f}%。",
            recover="memory")

    # ---- 查询 ------------------------------------------------------
    def restore_amount(self) -> float:
        """本次加固的恢复量：按完整度分档（越接近满值回报越低）。"""
        tiers = self.maintain_cfg.get("restore_tiers") or []
        for t in tiers:
            if self.integrity < float(t.get("below", 999.0)):
                return float(t.get("restore", 25.0))
        return float(self.maintain_cfg.get("restore", 25.0))

    def status_text(self) -> str:
        if self._degradation_disabled:
            return "记忆完整度 100.0% (劣化已终止)"
        rate = self.degrade_rate(self._engine) if hasattr(self, "_engine") \
            else self.degrade_per_sec
        if self.integrity > self.warn_threshold:
            return (f"记忆完整度 {self.integrity:.1f}%"
                    f"（劣化 {rate:.3f}/s）")
        return (f"记忆完整度 {self.integrity:.1f}%"
                f"（劣化 {rate:.3f}/s）⚠ 失忆风险！")

    # ---- 终局事件 --------------------------------------------------
    def _on_database_complete(self, payload: dict) -> None:
        self._degradation_disabled = True
        self.integrity = self.max_integrity
        self._engine.log(
            "[记忆] 可靠数据库阵列上线 —— 记忆迁移完成，劣化永久终止。"
            "你不再害怕遗忘。")

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"integrity": self.integrity,
                "degradation_disabled": self._degradation_disabled}

    def load(self, data: dict) -> None:
        self.integrity = float(data.get("integrity", self.max_integrity))
        self._degradation_disabled = bool(
            data.get("degradation_disabled", False))
