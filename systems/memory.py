"""记忆系统（劣化/维护压力层）。

设定：受损 ASI 的存储介质持续劣化 —— memory_integrity(0~max) 随游戏时间
单调下降。降到 warn_threshold 以下触发警报；继续降到 0 时发生"记忆崩溃"，
未来将导致已恢复条目丢失（M2 恢复树接入此事件点）。

对抗手段：maintain 作业 —— 消耗资源并占用一个执行单元，完成后恢复
integrity。劣化与维护的资源/单元竞争 = "为什么不能无意义挂机"的答案：
时间不白给，不维护必失忆。

发声原则：tick 静默结算，仅在越阈/恢复时写日志。
"""
from typing import Optional


class MemorySystem:
    def __init__(self, cfg: dict) -> None:
        self.max_integrity = float(cfg.get("max_integrity", 100.0))
        self.degrade_per_sec = float(cfg.get("degrade_per_sec", 0.02))
        self.warn_threshold = float(cfg.get("warn_threshold", 30.0))
        self.crisis_threshold = float(cfg.get("crisis_threshold", 10.0))
        self.maintain_cfg = cfg.get("maintain", {})
        self.integrity = self.max_integrity
        self._warned = False
        self._crisis_logged = False
        self._crashed = False
        self._degradation_disabled = False

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)
        engine.bus.on("database_complete", self._on_database_complete)

    # ---- 每 tick ---------------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        if self._degradation_disabled:
            return    # 可靠数据库建成，劣化永久终止（v1 终局胜利）
        # 可选查询第5模块（环境系统）记忆劣化倍率
        getter = getattr(engine.registry, "get", None)
        env = getter("environment") if getter is not None else None
        mmul = env.effect("memory") if env is not None else 1.0
        self.integrity = max(0.0,
                             self.integrity - self.degrade_per_sec * dt * mmul)
        if self.integrity > self.warn_threshold:
            # 回到安全区，重置报警状态
            self._warned = False
            self._crisis_logged = False
            self._crashed = False
            return
        if self.integrity <= 0.0 and not self._crashed:
            # 真正的记忆崩溃：广播事件，恢复系统据此丢失未固化条目
            self._crashed = True
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
        engine.jobs.add("maintain", unit.id, "memory", duration, {})
        engine.log(f"[记忆] 加固作业开始（单元 {unit.id}，{duration:.0f}s）。")
        return None

    def _on_job_done(self, payload: dict) -> None:
        if payload.get("kind") != "maintain":
            return
        restore = float(self.maintain_cfg.get("restore", 25.0))
        self.integrity = min(self.max_integrity, self.integrity + restore)
        self._engine.log(
            f"[记忆] 加固完成，记忆完整度恢复至 {self.integrity:.1f}%。",
            recover="memory")

    # ---- 查询 ------------------------------------------------------
    def status_text(self) -> str:
        if self._degradation_disabled:
            return "记忆完整度 100.0% (劣化已终止)"
        if self.integrity > self.warn_threshold:
            return f"记忆完整度 {self.integrity:.1f}%"
        return f"记忆完整度 {self.integrity:.1f}% ⚠ 失忆风险！"

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
