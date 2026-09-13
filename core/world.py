"""地块世界模型：量化"资源格"，无地图。

设计决策（来自需求）：
- 地块 = 数据条目（id / 圈层 / 类型 / 矿种 / 品位 / 储量 / 状态）。
- 圈层 ring 表达"离基地的距离感"：物流惩罚 = f(ring)，可由科技/设施降低。
- 类型：empty(空地可建厂) / ore(矿脉) / water(水源) / wreck(坠毁残骸)。
- 品位 grade：百分比数值(铁 58、铜 1.5)；储量 reserve：吨。
"""
from typing import Dict, List, Optional


def fmt_grade(v: Optional[float]) -> str:
    """品位显示：≥10 取整（58）、<10 保留 1 位（铜 1.5）。

    避免把勘探估值打成 83.9121% 这类长尾数字（试玩时发现的显示瑕疵）。
    """
    if v is None:
        return "?"
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"


class Plot:
    KIND_EMPTY = "empty"
    KIND_ORE = "ore"
    KIND_WATER = "water"
    KIND_WRECK = "wreck"

    # 状态机（本里程碑只用到部分，后续里程碑扩展）
    STATE_UNKNOWN = "unknown"     # 未勘探
    STATE_KNOWN = "known"         # 已勘探已知
    STATE_CLAIMED = "claimed"     # 已占领
    STATE_DEVELOPED = "developed" # 已开发(建厂/开采)
    STATE_DEPLETED = "depleted"   # 已枯竭

    def __init__(self, plot_id: str, ring: int = 0, kind: str = KIND_EMPTY,
                 substance: Optional[str] = None, grade: float = 0.0,
                 reserve: float = 0.0, state: str = STATE_UNKNOWN) -> None:
        self.id = plot_id
        self.ring = ring
        self.kind = kind
        self.substance = substance      # ore/wreck 时有效
        self.grade = grade              # 品位(%)；empty 时无意义（真值）
        self.reserve = reserve          # 吨；empty 时无意义（真值）
        self.state = state
        # 情报误差（勘探估值）：仅矿脉/残骸有意义
        self.known_grade: Optional[float] = None    # 估值品位；None=未勘察
        self.known_reserve: Optional[float] = None  # 估值储量
        self.grade_err: float = 0.0                 # 品位误差比例（±）
        self.reserve_err: float = 0.0               # 储量误差比例（±）
        self.surveyed = False                       # 是否已勘察（有估值）

    # ---- 查询 ------------------------------------------------------
    def is_mineral(self) -> bool:
        return self.kind in (self.KIND_ORE, self.KIND_WRECK)

    def set_survey(self, grade_err: float = 0.0,
                   reserve_err: float = 0.0) -> None:
        """勘探后生成估值（带误差），并从估值区段初始。</summary>"""
        self.known_grade = self.grade * (1.0 + grade_err)
        self.known_reserve = self.reserve * (1.0 + reserve_err)
        self.grade_err = abs(grade_err)
        self.reserve_err = abs(reserve_err)
        self.surveyed = True

    def refine(self, grade_frac: float = 0.25,
               reserve_frac: float = 0.25) -> None:
        """开采向真值收敛（估值逼近真值）。"""
        if self.known_grade is not None:
            self.known_grade += (self.grade - self.known_grade) * grade_frac
        if self.known_reserve is not None:
            self.known_reserve += (self.reserve - self.known_reserve) * reserve_frac

    def known_text(self) -> str:
        """估值展示文本，供 UI/日志用。"""
        if not self.surveyed or self.known_grade is None:
            return "未知"
        g = self.known_grade
        r = self.known_reserve
        return f"~{fmt_grade(g)}%(±{self.grade_err * 100:.0f}%) ~{r:,.0f}t"

    def describe(self) -> str:
        """生成供日志/界面展示的地块描述（用估值）。"""
        kind_names = {self.KIND_EMPTY: "空地", self.KIND_WATER: "水源",
                      self.KIND_ORE: "矿脉", self.KIND_WRECK: "残骸"}
        head = f"{kind_names.get(self.kind, self.kind)}"
        if self.substance:
            head += f"[{self.substance}]"
            if self.kind == self.KIND_ORE:
                head += f" 品位{self.known_text()}"
        return f"{self.id} (圈{self.ring}, {head})"

    def logistics_multiplier(self, base_ring_free: int = 0) -> float:
        """圈层 → 物流成本乘数（>圈0 的地块越远越贵）。

        公式可后续被科技/设施调制，此处保留简单增长模型：
        惩罚倍率 = 2^(ring - base_ring_free)，圈0内免惩罚。
        """
        over = max(0, self.ring - base_ring_free)
        return 2.0 ** over

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "ring": self.ring, "kind": self.kind,
            "substance": self.substance, "grade": self.grade,
            "reserve": self.reserve, "state": self.state,
            "known_grade": self.known_grade, "known_reserve": self.known_reserve,
            "grade_err": self.grade_err, "reserve_err": self.reserve_err,
            "surveyed": self.surveyed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plot":
        p = cls(
            plot_id=data["id"], ring=int(data.get("ring", 0)),
            kind=data.get("kind", cls.KIND_EMPTY),
            substance=data.get("substance"),
            grade=float(data.get("grade", 0.0)),
            reserve=float(data.get("reserve", 0.0)),
            state=data.get("state", cls.STATE_UNKNOWN),
        )
        p.known_grade = data.get("known_grade")
        p.known_reserve = data.get("known_reserve")
        if p.known_grade is not None:
            p.known_grade = float(p.known_grade)
        if p.known_reserve is not None:
            p.known_reserve = float(p.known_reserve)
        p.grade_err = float(data.get("grade_err", 0.0))
        p.reserve_err = float(data.get("reserve_err", 0.0))
        p.surveyed = bool(data.get("surveyed", False))
        return p


class World:
    def __init__(self) -> None:
        self.plots: Dict[str, Plot] = {}     # 保持插入顺序（有序字典）
        self._seq = 0

    # ---- 生成/添加 -------------------------------------------------
    def add_plot(self, plot: Plot) -> Plot:
        self.plots[plot.id] = plot
        return plot

    def spawn(self, ring: int = 0, kind: str = Plot.KIND_EMPTY,
              substance: Optional[str] = None, grade: float = 0.0,
              reserve: float = 0.0, state: str = Plot.STATE_UNKNOWN) -> Plot:
        self._seq += 1
        p = Plot(f"P{self._seq}", ring=ring, kind=kind, substance=substance,
                 grade=grade, reserve=reserve, state=state)
        return self.add_plot(p)

    def get(self, plot_id: str) -> Optional[Plot]:
        return self.plots.get(plot_id)

    def list_plots(self) -> List[Plot]:
        return list(self.plots.values())

    def visible_plots(self) -> List[Plot]:
        return [p for p in self.plots.values() if p.state != Plot.STATE_UNKNOWN]

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"plots": [p.to_dict() for p in self.plots.values()],
                "seq": self._seq}

    @classmethod
    def from_dict(cls, data: dict) -> "World":
        w = cls()
        w._seq = int(data.get("seq", 0))
        for pd in data.get("plots", []):
            w.add_plot(Plot.from_dict(pd))
        return w
