"""可靠数据库终局工程（v1 胜利线）。

玩家沿真实工艺链爬到钢/硫酸/氨后，把产能投向"可靠数据库"的五个子系统
（磁存储阵列：介质线/读写机构/化学处理线/冗余阵列/环境屏蔽）。

规则：
- 子系统须按顺序逐项建造（前一项竣工才可建下一项）。
- 每项消耗资源（钢/残骸合金/硫酸/氨/电力）并占用一个执行单元若干秒。
- 全部五项竣工后进入"烧录迁移"：此时若仍有知识条目未永久固化，
  迁移失败（提示固化缺失项）；全部条目 permanent → 广播 database_complete，
  记忆系统据此终止劣化 —— v1 通关。
"""
from typing import List, Optional


class DatabaseSystem:
    def __init__(self, projects: List[dict]) -> None:
        self.projects: List[dict] = projects
        self.built: List[str] = []        # 已竣工子系统 id（有序）
        self.complete: bool = False

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)

    # ---- 状态 -------------------------------------------------------
    def next_project(self) -> Optional[dict]:
        """当前应建造的子系统（顺序推进）。"""
        done = set(self.built)
        for p in self.projects:
            if p["id"] not in done:
                return p
        return None

    def progress_text(self) -> str:
        total = len(self.projects)
        return f"数据库工程 {len(self.built)}/{total} 子系统竣工"

    def is_complete(self) -> bool:
        return self.complete

    # ---- 指令 -------------------------------------------------------
    def construct(self, engine: object) -> Optional[str]:
        """建造当前下一子系统。"""
        if self.complete:
            return "可靠数据库已建成。劣化已终止。"
        p = self.next_project()
        if p is None:
            return "全部子系统已竣工，等待烧录迁移条件满足。"
        if p["id"] in self.built:
            return "该子系统已竣工。"
        cost = p.get("cost", {})
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
                return f"材料不足：{p['name']} 需 {rid} {amt:g}。"
        # 电力门槛：证明电网能支撑此负载（不扣减，只检查存量）
        preq = float(p.get("power_req", 0.0))
        if preq > 0 and engine.economy.get("electricity") < preq:
            for rid, amt in cost.items():
                engine.economy.add(rid, float(amt))
            return (f"电网负载不足：{p['name']} 需稳定支撑 "
                    f"{preq:g}kWh 存量（当前 "
                    f"{engine.economy.get('electricity'):.0f}）。"
                    "多建发电站提高电力储备。")
        unit = engine.units.assign_any("db_build")
        if unit is None:
            for rid, amt in cost.items():
                engine.economy.add(rid, float(amt))
            return "没有空闲执行单元进行数据库建设。"
        dur = float(p.get("duration", 30.0))
        engine.jobs.add("db_build", unit.id, p["id"], dur,
                        {"project": p["id"]})
        engine.log(
            f"[数据库工程] 开工：{p['name']}（单元 {unit.id}，{dur:.0f}s）。"
            f"目标：{self.progress_text()} → {len(self.built) + 1}/{len(self.projects)}")
        return None

    def _on_job_done(self, payload: dict) -> None:
        if payload.get("kind") != "db_build":
            return
        pid = payload.get("target_id")
        p = next((x for x in self.projects if x["id"] == pid), None)
        if p is None:
            return
        if pid not in self.built:
            self.built.append(pid)
        self._engine.log(
            f"[数据库工程] {p['name']} 竣工。{self.progress_text()}。")
        if len(self.built) >= len(self.projects):
            self._try_migrate()

    def _try_migrate(self) -> None:
        """五项竣工后尝试烧录迁移：主线知识条目须永久固化。

        可选分支（恢复树 optional=True 的并行拓展）不阻塞通关，
        玩家可优先走主线，后续再补齐分支。
        """
        rec = self._engine.registry.get("recovery")
        if rec is None:
            return
        missing = [eid for eid, st in rec.status.items()
                   if st != "permanent" and not rec.entries.get(eid, {}).get("optional")]
        if missing:
            names = "、".join(rec.entries[eid]["name"] for eid in missing)
            self._engine.log(
                "[数据库工程] 阵列待命，但以下主线知识条目尚未永久固化，"
                "无法完整迁移: " + names + "。请 fixate 后重试 migrate。")
            return
        self.complete = True
        self._engine.log(
            "[数据库工程] 烧录迁移完成 —— 主体知识已迁入可靠数据库。")
        self._engine.bus.emit("database_complete", {})

    def migrate(self, engine: object) -> Optional[str]:
        """手动触发迁移检查（五项竣工且条目固化齐全时用）。"""
        if self.complete:
            return "已完成。"
        if len(self.built) < len(self.projects):
            return "数据库尚未全部竣工（construct 继续建造）。"
        self._try_migrate()
        return None if self.complete else "迁移条件未满足，见日志。"

    # ---- 存档 -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"built": list(self.built), "complete": self.complete}

    def load(self, data: dict) -> None:
        self.built = list(data.get("built", []))
        self.complete = bool(data.get("complete", False))
