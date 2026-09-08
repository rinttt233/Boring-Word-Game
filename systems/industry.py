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
        self.fuel: Optional[str] = None      # burner 当前燃料
        self.produced_any = False            # 是否已产出过（撤销建造的安全闸）

    def to_dict(self) -> dict:
        return {"id": self.id, "plot_id": self.plot_id, "def_id": self.def_id,
                "name": self.name, "assigned": list(self.assigned),
                "stall_reason": self.stall_reason,
                "produced_any": self.produced_any,
                "fuel": self.fuel}

    @classmethod
    def from_dict(cls, data: dict) -> "Facility":
        f = cls(data["id"], data["plot_id"], data["def_id"], data["name"])
        f.assigned = list(data.get("assigned", []))
        f.stall_reason = data.get("stall_reason")
        f.produced_any = bool(data.get("produced_any", False))
        f.fuel = data.get("fuel")
        return f


class IndustrySystem:
    def __init__(self, facility_defs: List[dict], recipes: List[dict],
                 heat_values: Optional[Dict[str, float]] = None,
                 fuel_classes: Optional[Dict[str, str]] = None) -> None:
        self.defs: Dict[str, dict] = {d["id"]: d for d in facility_defs}
        self.recipes: Dict[str, dict] = {r["id"]: r for r in recipes}
        self.facilities: Dict[str, Facility] = {}
        self.heat_values: Dict[str, float] = heat_values or {}
        self.fuel_classes: Dict[str, str] = fuel_classes or {}
        self.last_build: Optional[dict] = None   # 最近一次建造快照（撤销用）
        self._seq = 0

    def start(self, engine: object) -> None:
        self._engine = engine

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
        plot = engine.world.get(plot_id)
        if plot is None:
            return f"地块不存在: {plot_id}"
        if plot.state != Plot.STATE_CLAIMED:
            return "该地块尚未占领（先 claim）。"
        d = self.defs.get(def_id)
        if d is None:
            return f"未知设施: {def_id}"
        if plot.kind not in d.get("allowed_plot_kinds", []):
            return f"{d['name']} 不能建在 {plot.kind} 地块上。"
        missing = self._require_recovery(engine, def_id)
        if missing is not None:
            return f"知识缺失：需先从数据库恢复 {missing} 才能建造。"
        if any(f.plot_id == plot_id for f in self.facilities.values()):
            return "该地块已有设施。"
        cost = d.get("build_cost", {})
        for rid, amt in cost.items():
            if not engine.economy.take(rid, float(amt)):
                # 退还本次已扣资源（避免失败建造造成净消耗）
                for rid2, amt2 in cost.items():
                    if rid2 == rid:
                        break
                    engine.economy.add(rid2, float(amt2))
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
        engine.log(f"[工业] {d['name']} 建于 {plot_id} "
                   f"(成本 {cost_txt})。assign 单元后开工。")
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
                   f"返还 {cost_txt}，地块恢复可建。")
        return None

    def assign(self, engine: object, fac_id: str) -> Optional[str]:
        f = self.facilities.get(fac_id)
        if f is None:
            return f"设施不存在: {fac_id}"
        d = self.defs[f.def_id]
        if len(f.assigned) >= int(d.get("slots", 1)):
            return f"{f.name} 单元已满 (上限 {d.get('slots', 1)})。"
        unit = engine.units.assign_any(fac_id)
        if unit is None:
            return "没有空闲执行单元。"
        f.assigned.append(unit.id)
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
        for f in list(self.facilities.values()):
            if not f.assigned:
                continue                    # 无单元 = 停摆
            d = self.defs[f.def_id]
            # 恢复条目丢失 → 设施失忆停摆（需重新恢复才能开工）
            if self._require_recovery(engine, f.def_id) is not None:
                self._report_stall(engine, f, True, "知识条目已丢失")
                continue
            self._report_stall(engine, f, False, "")
            if d.get("extract_rate"):
                self._tick_extractor(engine, f, d, dt, pmul)
            elif d.get("kind") == "burner":
                self._tick_burner(engine, f, d, dt, pmul)
            elif d.get("kind") == "renewable":
                self._tick_renewable(engine, f, d, dt, pmul)
            else:
                self._tick_recipe(engine, f, d, dt, pmul)

    def _consume(self, engine: object, need: Dict[str, float]) -> float:
        """按需按可用比例尽力消耗，返回实际比例 [0,1]。"""
        factor = 1.0
        for rid, amt in need.items():
            if amt <= 0:
                continue
            avail = engine.economy.get(rid)
            factor = min(factor, avail / amt)
        if factor <= 0:
            return 0.0
        for rid, amt in need.items():
            engine.economy.take(rid, amt * factor)
        return factor

    def _tick_recipe(self, engine: object, f: Facility, d: dict,
                     dt: float, pmul: float = 1.0) -> None:
        recipe = self.recipes.get(d.get("recipe", ""))
        if recipe is None:
            return
        need = {rid: float(r) * dt for rid, r in recipe.get("inputs", {}).items()}
        factor = self._consume(engine, need)
        missing = not need or factor <= 0
        self._report_stall(engine, f, missing, "输入不足")
        if factor <= 0:
            return
        for rid, rate in recipe.get("outputs", {}).items():
            engine.economy.add(rid, float(rate) * dt * factor * pmul)
        # 副产物（byproducts: id→每产出主产物*时间的量；此处按 dt 产率）
        for rid, rate in recipe.get("byproducts", {}).items():
            if float(rate) > 0:
                engine.economy.add(rid, float(rate) * dt * factor * pmul)
        f.produced_any = True

    def _tick_extractor(self, engine: object, f: Facility, d: dict,
                        dt: float, pmul: float = 1.0) -> None:
        plot = engine.world.get(f.plot_id)
        if plot is None:
            return
        # 电力消耗
        pwr = float(d.get("power_use", 0.0)) * dt
        need = {"electricity": pwr} if pwr > 0 else {}
        factor = self._consume(engine, need)
        self._report_stall(engine, f, (need and factor <= 0), "缺电")
        if need and factor <= 0:
            return
        rate = float(d.get("extract_rate", 0.0))
        out = rate * dt * pmul
        if plot.kind == Plot.KIND_WATER:
            sub = "water"
        else:
            sub = plot.substance or "scrap_alloy"
            out = min(out, max(0.0, plot.reserve))   # 储量封顶
        if out <= 0:
            return
        engine.economy.add(sub, out)
        f.produced_any = True
        if plot.kind != Plot.KIND_WATER:
            plot.reserve -= out
            plot.refine()   # 开采逐步揭示真实品位/储量（向真值收敛）
            if plot.reserve <= 1e-6:
                plot.reserve = 0.0
                self._deplete(engine, f, plot, d)

    def _tick_burner(self, engine: object, f: Facility, d: dict,
                     dt: float, pmul: float = 1.0) -> None:
        """燃烧发电：烧当前燃料，产电 = 消耗×热值×效率。"""
        if not f.fuel:
            self._report_stall(engine, f, True, "未设置燃料(set fuel)")
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
        # 尽力烧（仓库不足按比例）
        avail = engine.economy.get(f.fuel)
        if avail <= 0:
            self._report_stall(engine, f, True, f"缺燃料 {f.fuel}")
            return
        self._report_stall(engine, f, False, "")
        consumed = min(burn_rate * dt, avail)
        engine.economy.take(f.fuel, consumed)
        eff = float(d.get("burn_efficiency", 0.4))
        out = consumed * hv * eff * pmul
        engine.economy.add("electricity", out)
        f.produced_any = True

    def _tick_renewable(self, engine: object, f: Facility, d: dict,
                        dt: float, pmul: float = 1.0) -> None:
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
            self._report_stall(engine, f, True, "未配置产能(capacity)")
            return
        # 可再生发电只随其专属事件键(昼夜)波动，不再叠乘通用 production
        # （避免沙暴 production×0.6 与 wind×1.5 互相抵消）
        out = cap * dt * emul * dmul
        # 光照归零(夜)不算停摆——自然节律；但事件压到 0 也不报停摆
        if out <= 0:
            self._report_stall(engine, f, False, "")
            return
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
        if d.get("reward_unit_on_deplete"):
            nu = engine.units.add_unit("执行器-回收")
            engine.log(f"[工业] 残骸拆解完毕，回收出完整执行单元 {nu.id}！")
        engine.log(f"[工业] {plot.id} 资源枯竭，{f.name} 停止并拆除。")

    def _report_stall(self, engine: object, f: Facility, stalled: bool,
                      reason: str) -> None:
        cat = f"fac:{f.id}"
        if stalled and not f.stalled_reported:
            f.stalled_reported = True
            f.stall_reason = reason
            engine.log(f"[工业] {f.name} 停摆：{reason}。",
                       level="warn", category=cat)
        elif not stalled and f.stalled_reported:
            f.stalled_reported = False
            f.stall_reason = None
            engine.log(f"[工业] {f.name} 恢复运转。", recover=cat)

    # ---- 查询 ------------------------------------------------------
    def facility_names(self) -> List[str]:
        return list(self.facilities.keys())

    def power_balance(self, engine: object) -> dict:
        """全厂电力预算：产电 vs 耗电（用于 UI 诊断，M3 建议落实）。

        返回 {"produce": float/s, "consume": float/s,
              "consumers": [(name, amount), ...],
              "producers": [(name, amount), ...]}。
        burner/renewable 按其当前燃料/环境×昼夜折算，便于状态栏与电力专页。
        """
        getter = getattr(engine.registry, "get", None)
        env = getter("environment") if getter else None
        dl = getter("daylight") if getter else None
        produce = 0.0
        consume = 0.0
        consumers = []
        producers = []
        for f in self.facilities.values():
            if not f.assigned:
                continue                    # 未运转不耗电
            d = self.defs[f.def_id]
            if self._require_recovery(engine, f.def_id) is not None:
                continue
            if d.get("extract_rate"):
                c = float(d.get("power_use", 0.0))
                if c > 0:
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
                           * float(d.get("burn_efficiency", 0.4)) * pmul)
                    produce += out
                    producers.append((f.name, out))
            elif d.get("kind") == "renewable":
                pk = d.get("power_kind", "solar")
                emul = env.effect(pk) if env is not None else 1.0
                dmul = (dl.effect("solar")
                        if dl is not None and pk in ("solar", "pv") else 1.0)
                out = float(d.get("capacity", 0.0)) * emul * dmul
                produce += out
                producers.append((f.name, out))
            else:
                r = self.recipes.get(d.get("recipe", ""), {})
                for rid, rate in r.get("inputs", {}).items():
                    if rid == "electricity" and float(rate) > 0:
                        consume += float(rate)
                        consumers.append((f.name, float(rate)))
                for rid, rate in r.get("outputs", {}).items():
                    if rid == "electricity":
                        produce += float(rate)
                        producers.append((f.name, float(rate)))
        return {"produce": produce, "consume": consume,
                "consumers": consumers, "producers": producers}

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"seq": self._seq,
                "facilities": [f.to_dict() for f in self.facilities.values()]}

    def load(self, data: dict) -> None:
        self._seq = int(data.get("seq", 0))
        self.facilities = {}
        for fd in data.get("facilities", []):
            f = Facility.from_dict(fd)
            self.facilities[f.id] = f
