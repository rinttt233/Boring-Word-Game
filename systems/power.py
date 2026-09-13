"""电力体系（批次4）：电网容量 / 自放电 / 优先级降载 / 储能充放。

设计（见 `docs/roadmap.md` 批次4）：

- **4a 优先级降载**：缺电不再"全厂一起半速"，而是按设施 def 的 `power_priority`
  **从低到高降载**（0 可选 → 1 生产 → 2 关键）。高优先级设施尽量满速，低优先级先停。
- **4b 储能**：`kind="storage"` 的设施（电池组 / 蓄热罐）**余电入库、缺口放电**，
  自带 `capacity` / `max_rate` / `charge_efficiency`；电量记在设施 `stored` 上，随存档。
- **A13 电网容量上限 + 自放电**：存量不能超过 `base_capacity`（多出来的发电量
  **消纳不了** → 发电设施按"电网已满"降载，不再无限堆电），另有固定速率的小幅自放电。

**调度顺序**：本模块必须在 `industry` 之前 tick（`main.py` 的注册顺序即 tick 顺序），
这样本拍算好的档位系数与容量余量，industry 在同一个 tick 内就能用上。
"""
from typing import Dict, List, Optional

from core.stats import bump as stat_bump


class PowerSystem:
    def __init__(self, cfg: Optional[dict] = None) -> None:
        c = cfg or {}
        self.base_capacity = float(c.get("base_capacity", 200.0))
        self.self_discharge = float(c.get("self_discharge_per_sec", 0.05))
        self.priority_default = int(c.get("priority_default", 1))
        self.priority_names: Dict[int, str] = {
            int(k): str(v) for k, v in (c.get("priority_names") or {}).items()}
        self.charge_high = float(c.get("charge_high_ratio", 0.75))
        self.charge_low = float(c.get("charge_low_ratio", 0.25))
        # 电网"满了"的死区（占容量比例）：余量小于它就当作满，发电设施直接
        # 降载停机并给出 grid_full 提示，而不是滑稽地按 3% 微功率空转。
        self.grid_full_ratio = float(c.get("grid_full_ratio", 0.02))
        # 复电滞后（试玩提案 §6）：已判"满"的设施要等余量涨回这个比例才恢复 ——
        # 否则电网会在死区边缘每几秒翻一次状态，把日志刷爆。
        self.grid_resume_ratio = float(c.get("grid_resume_ratio", 0.15))
        self._grid_stalled = set()          # 当前因"电网满"被降载的设施 id
        self.timeline_seconds = float(c.get("timeline_seconds", 90.0))
        self.timeline_samples = max(2, int(c.get("timeline_samples", 30)))
        # 本拍状态（供 industry / report / UI 查询）
        self._factors: Dict[int, float] = {}
        self._demand: Dict[int, float] = {}          # 每档需求（/s）
        self._storage_state: Dict[str, str] = {}     # 设施 id → 充电/放电/待机
        self.timeline: List[float] = []              # 存量采样（4c 电量时间线）
        self._next_sample = 0.0
        self.total_self_discharged = 0.0
        self.total_charged = 0.0
        self.total_discharged = 0.0

    # ================= 查询 =================
    @staticmethod
    def _registry(engine: object):
        return getattr(engine, "registry", None)

    def _industry(self, engine: object):
        reg = self._registry(engine)
        return reg.get("industry") if reg is not None else None

    def priority_of(self, defn: dict) -> int:
        try:
            return int(defn.get("power_priority", self.priority_default))
        except (TypeError, ValueError):
            return self.priority_default

    def tier_name(self, priority: int) -> str:
        return self.priority_names.get(int(priority), f"{priority} 档")

    def capacity(self, engine: object) -> float:
        """电网存量上限（储能设施的电量记在设施自己身上，不并进电网）。"""
        return max(0.0, self.base_capacity)

    def storage_capacity(self, engine: object) -> float:
        ind = self._industry(engine)
        if ind is None:
            return 0.0
        return sum(float(ind.defs.get(f.def_id, {}).get("capacity", 0.0))
                   for f in ind.facilities.values()
                   if ind.defs.get(f.def_id, {}).get("kind") == "storage")

    def stored(self, engine: object) -> float:
        return float(engine.economy.get("electricity"))

    def headroom(self, engine: object) -> float:
        """电网还能吃下多少电（A13：超出的发电量消纳不了）。"""
        return max(0.0, self.capacity(engine) - self.stored(engine))

    def grid_full(self, engine: object, fac_id: str = "") -> bool:
        """电网是否已满（带**滞后**：判满后要等余量涨回 grid_resume_ratio 才恢复）。

        `fac_id` 用于按设施记忆状态：同一台发电设施不会被卡在死区边缘反复起停。
        """
        cap = self.capacity(engine)
        head = self.headroom(engine)
        if fac_id in self._grid_stalled:
            if head < cap * self.grid_resume_ratio:
                return True
            self._grid_stalled.discard(fac_id)
            return False
        if head <= cap * self.grid_full_ratio:
            if fac_id:
                self._grid_stalled.add(fac_id)
            return True
        return False

    def factor(self, priority: int) -> float:
        """本拍该优先级档位的供电比例（1.0 满供 / 0.0 降载停机）。"""
        return float(self._factors.get(int(priority), 1.0))

    def shed_tiers(self) -> Dict[int, float]:
        return {p: f for p, f in self._factors.items() if f < 1.0 - 1e-9}

    def status(self, engine: object) -> dict:
        ind = self._industry(engine)
        storage = []
        if ind is not None:
            for f in sorted(ind.facilities.values(), key=lambda x: x.id):
                d = ind.defs.get(f.def_id, {})
                if d.get("kind") != "storage":
                    continue
                storage.append({
                    "id": f.id, "name": f.name,
                    "stored": round(float(f.stored), 1),
                    "capacity": float(d.get("capacity", 0.0)),
                    "max_rate": float(d.get("max_rate", 0.0)),
                    "efficiency": float(d.get("charge_efficiency", 1.0)),
                    "state": self._storage_state.get(f.id, "idle"),
                    "assigned": len(f.assigned),
                })
        return {
            "capacity": round(self.capacity(engine), 1),
            "stored": round(self.stored(engine), 1),
            "headroom": round(self.headroom(engine), 1),
            "self_discharge_per_sec": self.self_discharge,
            "storage_capacity": round(self.storage_capacity(engine), 1),
            "storage": storage,
            "demand": {str(p): round(v, 3) for p, v in sorted(self._demand.items())},
            "factors": {str(p): round(v, 3) for p, v in sorted(self._factors.items())},
            "shed": {self.tier_name(p): round(v, 3)
                     for p, v in self.shed_tiers().items()},
            "timeline": list(self.timeline),
            "stats": {"self_discharged": round(self.total_self_discharged, 1),
                      "charged": round(self.total_charged, 1),
                      "discharged": round(self.total_discharged, 1)},
        }

    # ================= tick =================
    def start(self, engine: object) -> None:
        self._engine = engine

    def install_unbounded(self) -> None:
        """调试/端点脚本用：把电网容量放到很大（不再"消纳不了"）。

        与 `IndustrySystem.install_instant` 同一类旁路：端点脚本聚焦**产线数值**
        时需要它（否则测到一半电网满了、发电被降载，数值就不干净了）；
        `gameplay_test` / `victory_test` / 盲测走真实路径。
        """
        self.base_capacity = 1e9

    def tick(self, engine: object, dt: float) -> None:
        ind = self._industry(engine)
        ep = engine.economy
        cap = self.capacity(engine)
        # 1) 自放电（A13）：固定速率的小幅流失，让"躺着不用的电"变贵
        stock = float(ep.get("electricity"))
        if stock > 0.0 and self.self_discharge > 0.0:
            loss = min(stock, self.self_discharge * dt)
            ep.set("electricity", stock - loss)
            self.total_self_discharged += loss
            stat_bump(engine, "self_discharged", loss)
            stock -= loss
        # 2) 本拍各档位需求（按设施 def 的 power_priority 分档）
        demand: Dict[int, float] = {}
        if ind is not None:
            for f in ind.facilities.values():
                d = ind.defs.get(f.def_id, {})
                if d.get("kind") == "storage":
                    continue
                if not self._live(ind, engine, f, d):
                    continue
                draw = self._draw(ind, engine, f, d)
                if draw > 0.0:
                    p = self.priority_of(d)
                    demand[p] = demand.get(p, 0.0) + draw
        # 3) 预算 = 当前存量 + 本拍预计发电（发电设施在同一 tick 稍后出力）
        produce_est = 0.0
        if ind is not None and hasattr(ind, "power_balance"):
            produce_est = float(ind.power_balance(engine).get("produce_rated",
                                                             0.0))
        budget = stock + produce_est * dt
        # 4) 从高优先级往低分配：低优先级先被降载
        factors: Dict[int, float] = {}
        remain = budget
        for p in sorted(demand, reverse=True):
            need = demand[p] * dt
            if need <= 1e-9:
                factors[p] = 1.0
                continue
            if remain >= need - 1e-9:
                factors[p] = 1.0
                remain -= need
            else:
                factors[p] = max(0.0, remain / need)
                remain = 0.0
        self._factors = factors
        self._demand = demand
        # 5) 储能充放
        self._storage_tick(engine, dt, ind, cap)
        # 6) 电量时间线采样（4c）
        self._sample(engine)

    # ---- 内部 ----
    def _live(self, ind, engine, f, d) -> bool:
        """这台设施这一拍是否真的在运转（与 power_balance 的口径一致）。"""
        if not f.assigned or f.under_construction or f.mothballed:
            return False
        if f.halt_until > float(engine.clock.time):
            return False
        if ind._require_recovery(engine, f.def_id) is not None:
            return False
        return True

    def _draw(self, ind, engine, f, d) -> float:
        """该设施每秒耗电（含单元效能倍率）：提取/放空/回流/装配/配方。"""
        sf = ind._staff_factor(engine, f, d)
        kind = d.get("kind")
        if d.get("extract_rate") or kind in ("vent", "sink"):
            return float(d.get("power_use", 0.0)) * sf
        if kind in ("burner", "renewable", "storage"):
            return 0.0
        recipe = ind.recipes.get(d.get("recipe", ""), {})
        return float(recipe.get("inputs", {}).get("electricity", 0.0)) * sf

    def _storage_tick(self, engine: object, dt: float, ind, cap: float) -> None:
        """余电入库、缺口放电（4b）。"""
        if ind is None:
            return
        ep = engine.economy
        for f in sorted(ind.facilities.values(), key=lambda x: x.id):
            d = ind.defs.get(f.def_id, {})
            if d.get("kind") != "storage":
                continue
            if not self._live(ind, engine, f, d):
                self._storage_state[f.id] = "idle"
                continue
            sf = ind._staff_factor(engine, f, d)
            max_rate = float(d.get("max_rate", 1.0)) * sf
            store_cap = float(d.get("capacity", 0.0))
            eff = float(d.get("charge_efficiency", 1.0))
            stock = float(ep.get("electricity"))
            hi = cap * self.charge_high
            lo = cap * self.charge_low
            if stock >= hi and f.stored < store_cap - 1e-6:
                # 充电：受"充放速率 / 剩余库容 / 电网余量 / 不抽干到 lo 以下"四重限制
                room = min(max_rate * dt, store_cap - f.stored,
                           self.headroom(engine), max(0.0, stock - lo))
                if room > 1e-9:
                    ep.take("electricity", room)
                    f.stored = min(store_cap, f.stored + room * eff)
                    self.total_charged += room * eff
                    stat_bump(engine, "stored_charged", room * eff)
                    self._storage_state[f.id] = "charging"
                    self._idle(engine, f)
                    continue
            if stock <= lo and f.stored > 1e-6:
                amt = min(max_rate * dt, f.stored, self.headroom(engine))
                if amt > 1e-9:
                    f.stored -= amt
                    ep.add("electricity", amt)
                    self.total_discharged += amt
                    stat_bump(engine, "stored_discharged", amt)
                    self._storage_state[f.id] = "discharging"
                    self._idle(engine, f)
                    continue
            self._storage_state[f.id] = "idle"
            self._idle(engine, f)

    def _idle(self, engine: object, f) -> None:
        """储能设施不算"停摆"（待机就是它的正常工作态）。"""
        f.stalled_reported = False
        if f.stall_code in (None, "shed", "no_power", "grid_full"):
            f.stall_reason = None
            f.stall_code = None

    def _sample(self, engine: object) -> None:
        now = float(engine.clock.time)
        if now < self._next_sample:
            return
        self._next_sample = now + max(1.0, self.timeline_seconds
                                      / self.timeline_samples)
        self.timeline.append(round(float(engine.economy.get("electricity")), 1))
        if len(self.timeline) > self.timeline_samples:
            del self.timeline[:-self.timeline_samples]

    # ================= 存档 =================
    def to_dict(self) -> dict:
        return {"timeline": list(self.timeline),
                "next_sample": self._next_sample,
                "totals": {"self_discharged": self.total_self_discharged,
                           "charged": self.total_charged,
                           "discharged": self.total_discharged}}

    def load(self, data: dict) -> None:
        self.timeline = [float(x) for x in data.get("timeline", [])]
        self._next_sample = float(data.get("next_sample", 0.0) or 0.0)
        t = data.get("totals") or {}
        self.total_self_discharged = float(t.get("self_discharged", 0.0) or 0.0)
        self.total_charged = float(t.get("charged", 0.0) or 0.0)
        self.total_discharged = float(t.get("discharged", 0.0) or 0.0)
