"""勘探模块：派出执行单元侦察未知区域，作业完成后按圈层概率池生成新地块。

设计：
- 玩家指定要勘探的圈层 ring，单元执行 'survey' 作业（耗时随圈层增长）。
- 完成后从 content/regions.json 对应圈层的概率池抽取一块地，标记 known。
- 生成地块品位/储量从池内范围随机；物流惩罚随圈层走高（需求里的远圈惩罚）。
- 圈内补偿（动态权重）：按"该圈层已可见地块中各类占比 vs 池内配置占比"
  修正抽取权重 —— 某类(含空地)在该圈越稀缺，越容易被勘探到，配比长期趋向配置值。
"""
import random
from typing import Dict, List, Optional, Tuple

from core.world import Plot


class SurveySystem:
    def __init__(self, regions_cfg: dict) -> None:
        self.regions = regions_cfg.get("regions", [])
        self.surveys_per_ring = int(regions_cfg.get("surveys_per_ring", 3))
        self._rng = random.Random()          # 可注入种子以便测试

    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)

    # ---- 查询 ------------------------------------------------------
    def ring_cfg(self, ring: int) -> Optional[dict]:
        for r in self.regions:
            if r["ring"] == ring:
                return r
        return None

    def max_survey_ring(self) -> int:
        return max((r["ring"] for r in self.regions), default=0)

    # ---- 指令 ------------------------------------------------------
    def survey(self, engine: object, ring: int) -> Optional[str]:
        """派发勘探作业。成功返回 None，失败返回错误信息。"""
        if ring < 1 or ring > self.max_survey_ring():
            return f"该圈层超出可勘探范围 (1~{self.max_survey_ring()})。"
        unit = engine.units.assign_any("survey")
        if unit is None:
            return "没有空闲执行单元，无法勘探。"
        duration = 4.0 + ring * 4.0          # 越远耗时越长
        engine.jobs.add("survey", unit.id, f"ring{ring}", duration,
                        {"ring": ring})
        engine.log(f"[勘探] 单元 {unit.id} 出发勘察 圈{ring}，"
                   f"预计 {duration:.0f}s 回报。")
        return None

    # ---- 作业完成 --------------------------------------------------
    def _on_job_done(self, payload: dict) -> None:
        if payload.get("kind") != "survey":
            return
        ring = int(payload["payload"].get("ring", 1))
        cfg = self.ring_cfg(ring)
        if cfg is None:
            return
        plot = self._roll_plot(self._engine, ring, cfg)
        self._engine.log(
            f"[勘探] 圈{ring} 回报：{plot.describe()}")

    # ---- 圈内补偿权重 ----------------------------------------------
    def _pool_key(self, p: dict) -> Tuple[str, Optional[str]]:
        """池条目类别键：矿/残骸按 kind+substance，空地/水源仅 kind。"""
        if p.get("kind") in (Plot.KIND_ORE, Plot.KIND_WRECK):
            return (p.get("kind"), p.get("substance"))
        return (p.get("kind"), None)

    def _ring_visible_counts(self, engine: object,
                             ring: int) -> Dict[Tuple, int]:
        """统计该圈层已可见(已勘探/已占/已开发/已枯竭)地块各类数量。"""
        counts: Dict[Tuple, int] = {}
        for p in engine.world.plots.values():
            if p.state == Plot.STATE_UNKNOWN or p.ring != ring:
                continue
            key = (p.kind, p.substance if p.kind in
                   (Plot.KIND_ORE, Plot.KIND_WRECK) else None)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _adjusted_pool(self, engine: object, ring: int,
                       cfg: dict) -> List[Tuple[dict, float]]:
        """返回 [(池条目, 修正权重)]：越稀缺(相对配置占比)权重越高。

        修正：eff = base * clamp((target_share + eps) / (actual_share + eps),
                                 0.5, 3.0)；eps=0.005 防除零与过度摆动。
        """
        pool = cfg["pools"]
        total_w = sum(p.get("weight", 1) for p in pool)
        counts = self._ring_visible_counts(engine, ring)
        seen = sum(counts.values())
        eps = 0.005
        out = []
        for p in pool:
            base = float(p.get("weight", 1))
            target = base / total_w
            key = self._pool_key(p)
            actual = (counts.get(key, 0) / seen) if seen else 0.0
            ratio = (target + eps) / (actual + eps)
            eff = base * max(0.5, min(3.0, ratio))
            out.append((p, eff))
        return out

    def _roll_plot(self, engine: object, ring: int, cfg: dict) -> Plot:
        weighted = self._adjusted_pool(engine, ring, cfg)
        total = sum(w for _p, w in weighted)
        roll = self._rng.uniform(0, total)
        acc = 0.0
        chosen = weighted[0][0]
        for p, w in weighted:
            acc += w
            if roll <= acc:
                chosen = p
                break
        kind = chosen.get("kind", Plot.KIND_EMPTY)
        if kind == Plot.KIND_ORE or kind == Plot.KIND_WRECK:
            sub = chosen["substance"]
            g0, g1 = chosen.get("grade", [0, 1])
            r0, r1 = chosen.get("reserve", [0, 1])
            plot = engine.world.spawn(
                ring=ring, kind=kind, substance=sub,
                grade=round(self._rng.uniform(g0, g1), 1),
                reserve=round(self._rng.uniform(r0, r1), 0),
                state=Plot.STATE_KNOWN)
            # 勘探误差：随圈层增大（圈1小而准，圈5大而糊）
            err = min(0.4, 0.05 + 0.07 * ring)
            ge = self._rng.uniform(-err, err)
            re = self._rng.uniform(-err, err)
            plot.set_survey(grade_err=ge, reserve_err=re)
            return plot
        return engine.world.spawn(ring=ring, kind=kind,
                                  state=Plot.STATE_KNOWN)

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {}

    def load(self, data: dict) -> None:
        pass
