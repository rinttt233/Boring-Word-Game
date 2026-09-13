#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ladder_policy.py —— 目标驱动的「阶梯」盲玩策略（只用文档化接口）。

设计约束（与 `AGENTS.md` 的硬约束一致）：
- **只读 `report`（固定 schema 的 JSON）+ 只发游戏命令**：不 import `core/`、`systems/`，
  不读源码、不碰内部字段、不用 `dbg`、不注入资源、不锁天气。
- 内容（条目 / 设施 / 配方 / 数据库工程 / 勘探池）从 `content/*.json` 读取 ——
  这与玩家查百科等价，属于**文档化内容**，不是源码。

策略 = 「阶段机 + 资源账本」：
1. **紧急项优先**：终局迁移 → 施工完成 → 固化临时条目 → 记忆护栏 → 燃料 → 副产物积压；
2. **资源账本**：把"通关还差多少"（数据库 5 项 + 主线固化 + 计划内设施建造）按配方
   逐层展开成原料需求，得到每种物质的**目标库存**，据此决定谁该派员、该建什么；
3. **设施计划表 `PLAN`**：按胜利路径列出要建什么、建在哪类地块、什么条件下才需要它，
   顺序补齐（电 → 煤 → 残骸合金 → 铁/石灰 → 焦炭 → 生铁 → 钢 → 硫/硫酸 → 氨 → 铜 → 维护件 → 单元厂 → 放空塔）；
4. **单元账本**：单元不足时按"当前不需要"的次序释放（`unassign`），保证关键动作能开工。

用法（一般由 `tools/blind_test.py --policy ladder` 调用）：
    python -X utf8 tools/blind_test.py --policy ladder --runs 1 --seed-base 100 --max-minutes 300 --verbose
"""
import io
import json
import os
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 公用消耗保底（煤/水这类"连续吃"的物资，账本里算不全，给一个下限）
UTILITY_FLOOR = {"coal": 220.0, "water": 120.0, "maintenance_kit": 25.0}
# 目标库存缓冲（避免"刚好够"导致设施反复启停）
BUFFER = {"scrap_alloy": 15.0, "iron_ore": 30.0, "limestone": 20.0, "coke": 15.0,
          "pig_iron": 10.0, "steel": 20.0, "sulfur": 10.0, "sulfuric_acid": 8.0,
          "ammonia": 8.0, "copper_ore": 15.0, "copper": 6.0}
# 主线推进优先序（db_upkeep 故意靠后：维护件一开就持续吃合金，先让其它条目就位）
MAINLINE_ORDER = [
    "db_coking", "db_blast_furnace", "db_steel", "db_unit_bus", "db_unit_parallel",
    "db_units", "db_sulfuric", "db_ammonia", "db_petrol", "db_copper",
    "db_distill", "db_refractory", "db_rare_earth", "db_upkeep",
]

# 设施计划表：按胜利路径排列（越靠前越先满足）
PLAN = [
    dict(key="power", def_id="power_plant", plot_kind="empty", out=None,
         staff="always", count="power", why="发电"),
    dict(key="coal", def_id="extractor", sub="coal", out="coal",
         staff="always", count="coal", why="煤（发电与焦化的口粮）"),
    dict(key="scrap", def_id="salvager", plot_kind="wreck", out="scrap_alloy",
         staff="need", count=1, why="残骸合金（建造与固化的大头）"),
    dict(key="iron", def_id="extractor", sub="iron_ore", out="iron_ore",
         staff="need", count=1, why="铁矿石"),
    dict(key="lime", def_id="extractor", sub="limestone", out="limestone",
         staff="need", count=1, why="石灰石（高炉熔剂）"),
    dict(key="water", def_id="extractor", plot_kind="water", out="water",
         staff="need", count=1, why="水（硫酸与合成氨）"),
    dict(key="coke", def_id="cokery", plot_kind="empty", out="coke",
         staff="need", count=1, rec="db_coking", why="焦炭"),
    dict(key="pig", def_id="blast_furnace", plot_kind="empty", out="pig_iron",
         staff="need", count=1, rec="db_blast_furnace", why="生铁"),
    dict(key="steel", def_id="steel_mill", plot_kind="empty", out="steel",
         staff="need", count=1, rec="db_steel", why="钢（数据库主体材料）"),
    dict(key="sulfur", def_id="extractor", sub="sulfur", out="sulfur",
         staff="need", count=1, why="硫磺"),
    dict(key="acid", def_id="sulfuric_plant", plot_kind="empty",
         out="sulfuric_acid", staff="need", count=1, rec="db_sulfuric", why="硫酸"),
    dict(key="ammonia", def_id="ammonia_synth", plot_kind="empty", out="ammonia",
         staff="need", count=1, rec="db_ammonia", why="合成氨"),
    dict(key="copperore", def_id="extractor", sub="copper_ore", out="copper_ore",
         staff="need", count=1, why="铜矿石（单元装配）"),
    dict(key="copper", def_id="copper_refiner", plot_kind="empty", out="copper",
         staff="need", count=1, rec="db_copper", why="铜（单元装配）"),
    dict(key="kit", def_id="maintenance_depot", plot_kind="empty",
         out="maintenance_kit", staff="need", count=1, rec="db_upkeep",
         why="维护件"),
    dict(key="unitf", def_id="unit_factory", plot_kind="empty", out=None,
         staff="units", count=1, rec="db_units", why="执行单元装配"),
    dict(key="gasgen", def_id="gas_generator", plot_kind="empty", out=None,
         staff="gas", count=1, why="燃气发电（烧副产煤气的出口）"),
    dict(key="vent", def_id="vent_tower", plot_kind="empty", out=None,
         staff="backlog", count=1, rec="db_coking", why="副产物放空"),
]
PRIORITY = {r["key"]: i for i, r in enumerate(PLAN)}
# 收尾阶段（主线全固化后）只维护与数据库直接相关的产线，不再铺新摊子
ENDGAME_ROLES = {"power", "coal", "scrap", "iron", "lime", "water", "coke",
                 "pig", "steel", "sulfur", "acid", "ammonia", "kit", "vent",
                 "unitf"}


def _load(name: str) -> dict:
    with io.open(os.path.join(ROOT, "content", name), encoding="utf-8") as f:
        return json.load(f)


class LadderPolicy:
    """目标驱动的阶段机。`next_action(rep)` 返回 {'cmd','why'} 或 None。"""

    def __init__(self, root: str = ROOT) -> None:
        self.root = root
        rec = _load("recovery.json")
        self.entries: Dict[str, dict] = {e["id"]: e for e in rec["entries"]}
        self.mainline = [e for e in rec["entries"] if not e.get("optional")]
        self.mainline_ids = [e["id"] for e in self.mainline]
        self.fac_defs: Dict[str, dict] = {
            f["id"]: f for f in _load("facilities.json")["facilities"]}
        self.recipes: Dict[str, dict] = {
            r["id"]: r for r in _load("recipes.json")["recipes"]}
        self.db_projects = _load("database.json")["projects"]
        self.regions = _load("regions.json")["regions"]
        self.backlog_limits = _load("backlog.json")["limits"]
        self.mem_cost = _load("memory.json").get("maintain", {}).get("cost", {})
        self.plan = PLAN
        # 物质 → 生产它的配方（取第一个可用的基础配方）
        self.producer: Dict[str, str] = {}
        for rid, r in self.recipes.items():
            for sub in r.get("outputs", {}):
                self.producer.setdefault(sub, rid)
        self.log: List[str] = []
        self._fails: Dict[str, int] = {}
        self._last_cmd: Optional[str] = None
        self._n = 0

    # ================= 小工具 =================
    def _stock(self, rep: dict, sub: str) -> float:
        return float((rep.get("resources") or {}).get(sub, 0.0))

    @staticmethod
    def _plot(rep: dict, pid: str) -> Optional[dict]:
        for p in rep.get("plots", []):
            if p["id"] == pid:
                return p
        return None

    def _role_of(self, fac: dict, rep: dict) -> Optional[str]:
        p = self._plot(rep, fac["plot"]) or {}
        for role in self.plan:
            if fac["def"] != role["def_id"]:
                continue
            if role.get("sub"):
                if p.get("substance") == role["sub"]:
                    return role["key"]
            elif role.get("plot_kind"):
                if p.get("kind") == role["plot_kind"]:
                    return role["key"]
            else:
                return role["key"]
        return None

    def _count(self, rep: dict, key: str) -> int:
        return sum(1 for f in rep.get("facilities", [])
                   if self._role_of(f, rep) == key)

    def _role(self, key: str) -> dict:
        return next(r for r in self.plan if r["key"] == key)

    def _affordable(self, cost: dict, rep: dict, frac: float = 1.0) -> bool:
        for sub, amt in (cost or {}).items():
            if self._stock(rep, sub) + 1e-9 < float(amt) * frac:
                return False
        return True

    def _jobs(self, rep: dict, kind: str) -> List[dict]:
        return [j for j in rep.get("jobs", []) if j.get("kind") == kind]

    # ================= 资源账本 =================
    def _remaining_direct(self, rep: dict) -> Dict[str, float]:
        """还没付的账单：数据库工程 + 主线恢复/固化 + 计划内设施建造。"""
        need: Dict[str, float] = {}

        def add(cost: dict, k: float = 1.0) -> None:
            for sub, amt in (cost or {}).items():
                need[sub] = need.get(sub, 0.0) + float(amt) * k

        db = rep.get("database") or {}
        for p in self.db_projects[int(db.get("built", 0)):]:
            add(p.get("cost", {}))
        ent = rep.get("entries") or {}
        for bucket in ("locked", "active"):
            for e in ent.get(bucket, []):
                if e.get("optional"):
                    continue
                add(e.get("fixate_cost", {}))
                if bucket == "locked":
                    add(e.get("cost", {}))
        built = {self._role_of(f, rep) for f in rep.get("facilities", [])}
        for role in self.plan:
            if role["key"] in built:
                continue
            if role.get("count") != 1:          # 动态数量的角色（电/煤）另算
                continue
            add(self.fac_defs[role["def_id"]].get("build_cost", {}))
        return need

    def _expand(self, sub: str, amount: float, out: Dict[str, float],
                depth: int = 0) -> None:
        """把"要产出 amount 的 sub"沿配方展开成上游原料需求。"""
        if amount <= 1e-9 or depth > 6 or sub == "electricity":
            return
        rid = self.producer.get(sub)
        if rid is None:                          # 直接开采
            out[sub] = out.get(sub, 0.0) + amount
            return
        r = self.recipes[rid]
        out_q = float(r["outputs"].get(sub, 0.0))
        if out_q <= 0:
            return
        k = amount / out_q
        for inp, q in r.get("inputs", {}).items():
            need = float(q) * k
            out[inp] = out.get(inp, 0.0) + need
            self._expand(inp, need, out, depth + 1)

    def targets(self, rep: dict) -> Dict[str, float]:
        """每种物质的**目标库存**（含配方展开），用于判断"还缺不缺"。"""
        t: Dict[str, float] = {}
        for sub, amt in self._remaining_direct(rep).items():
            t[sub] = t.get(sub, 0.0) + amt
            self._expand(sub, amt, t)
        # 单元装配的持续消耗（钢/铜/电）按"还想造几个单元"折算
        extra_units = max(0, self.unit_gap(rep))
        if extra_units > 0:
            r = self.recipes.get("assemble_unit", {})
            t["steel"] = t.get("steel", 0.0) + 16.0 * extra_units
            t["copper"] = t.get("copper", 0.0) + 10.0 * extra_units
        for sub, floor in UTILITY_FLOOR.items():
            if sub == "maintenance_kit" and not (rep.get("maintenance") or {}).get("enabled"):
                continue
            t[sub] = max(t.get(sub, 0.0), floor)
        return t

    def unit_gap(self, rep: dict) -> int:
        """还想补几个单元：让"设施数 + 3 个周转"不超过现有单元。"""
        built = len(rep.get("facilities", []))
        total = int((rep.get("units") or {}).get("total", 0))
        return max(0, built + 3 - total)

    def _needed(self, role: dict, rep: dict, targets: dict) -> bool:
        kind = role.get("staff")
        if kind == "always":
            if role["key"] == "power":
                return self._power_needed(rep)
            return True
        if kind == "units":
            # 单元不够（设施 + 3 个周转）就补；另外"钢明显富余且单元还不到 12 个"
            # 时也补产能 —— 单元越多能同时开的产线越多（本作核心瓶颈）。
            # 用"库存 > 剩余计划需求 + 40"当闸门，避免抢掉数据库的钢。
            surplus = self._stock(rep, "steel") > targets.get("steel", 0.0) + 40.0
            return (self.unit_gap(rep) > 0
                    or (int((rep.get("units") or {}).get("total", 0)) < 12
                        and surplus))
        if kind == "backlog":
            return self._backlog_over(rep)
        if kind == "gas":
            return self._stock(rep, "coalgas") > 120.0
        out = role.get("out")
        if out is None:
            return True
        return self._stock(rep, out) < targets.get(out, 0.0) + BUFFER.get(out, 0.0)

    def _power_needed(self, rep: dict) -> bool:
        """电力节流：电量够高就停机省煤（煤少时更省）——向导也建议"不急的设施调离"。

        - 煤少（<300）：充到 90kWh 就停机；
        - 煤多：充到 400kWh（为数据库的 power_req 与后续产线留余量）。
        """
        stored = float((rep.get("power") or {}).get("stored", 0.0))
        coal = self._stock(rep, "coal")
        return stored < (90.0 if coal < 300.0 else 400.0)

    def _backlog_over(self, rep: dict) -> List[str]:
        return [k for k, v in (rep.get("backlog") or {}).items()
                if isinstance(v, dict) and v.get("over")]

    # ================= 数量目标 =================
    def _count_target(self, role: dict, rep: dict) -> int:
        want = role.get("count")
        if want == "power":
            consume = float((rep.get("power") or {}).get("consume", 0.0))
            return max(1, min(4, int((consume + 0.8) / 1.6) + 1))
        if want == "coal":
            plants = self._count(rep, "power")
            demand = plants * 0.4
            if self._count(rep, "coke"):
                demand += 0.5
            if self._count(rep, "ammonia"):
                demand += 0.3
            return max(1, min(3, int(demand / 0.7 + 0.5)))
        return int(want or 1)

    # ================= 地块选择 =================
    def _plot_free(self, plot: dict, rep: dict) -> bool:
        return not plot.get("facility")

    def _candidates(self, role: dict, rep: dict) -> List[dict]:
        """该角色可用的地块：优先"已占领/可重建"，其次"已知待占领"。"""
        want_kind = role.get("plot_kind")
        want_sub = role.get("sub")
        out = []
        for p in rep.get("plots", []):
            if not self._plot_free(p, rep):
                continue
            if want_sub:
                if p.get("substance") != want_sub:
                    continue
            elif want_kind:
                if p.get("kind") != want_kind:
                    continue
                if want_kind == "wreck" and float(p.get("reserve") or 0) <= 0:
                    continue
            if p.get("state") not in ("claimed", "developed", "known", "depleted"):
                continue
            out.append(p)
        # 距离近的优先（圈层小 = 便宜）
        return sorted(out, key=lambda p: (p.get("ring", 0), p["id"]))

    def survey_ring(self, role: dict) -> int:
        """该角色缺地块时，最该勘探哪一圈。

        评分 = 命中占比 ÷ 距离代价：远圈虽然更富（残骸更密），但勘探/占领/物流
        都更贵，所以按 (1 + 0.35×(圈-1)) 折价，避免为一块矿跑去圈5。
        """
        want_kind = role.get("plot_kind")
        want_sub = role.get("sub")
        best, best_score = 1, -1.0
        for reg in self.regions:
            ring = int(reg["ring"])
            total = sum(float(p.get("weight", 1)) for p in reg["pools"]) or 1.0
            share = 0.0
            for p in reg["pools"]:
                if want_sub and p.get("substance") == want_sub:
                    share += float(p.get("weight", 1)) / total
                elif not want_sub and want_kind and p.get("kind") == want_kind:
                    share += float(p.get("weight", 1)) / total
            score = share / (1.0 + 0.35 * (ring - 1))
            if score > best_score + 1e-9:
                best, best_score = ring, score
        return best

    # ================= 主决策 =================
    def next_action(self, rep: dict) -> Optional[dict]:
        db = rep.get("database") or {}
        mem = rep.get("memory") or {}
        units = rep.get("units") or {}
        idle = int(units.get("idle", 0))
        facs = rep.get("facilities", [])
        targets = self.targets(rep)

        # 1) 终局：子系统建完 → 迁移
        if int(db.get("built", 0)) >= int(db.get("projects", 5)):
            if not db.get("complete"):
                return self._act("migrate", "5/5 子系统竣工，执行烧录迁移",
                                 need_unit=False)
            return None

        # 2) 施工中：等完工（同时避免重复下指令）
        building = [f for f in facs if f.get("state") == "building"]
        if building:
            left = max(float(f.get("build_left") or 0) for f in building)
            return self._act(f"@tick {int(left) + 2}",
                             f"{building[0]['name']} 施工中（剩约 {left:.0f}s）",
                             need_unit=False)

        # 3) 临时条目优先固化（90s 窗口，丢了要重来）
        active = [e for e in (rep.get("entries") or {}).get("active", [])
                  if not e.get("optional")]
        if active and not self._jobs(rep, "fixate"):
            active.sort(key=lambda e: (0 if e["id"] in self.mainline_ids else 1,
                                       e["id"]))
            e = active[0]
            if self._affordable(e.get("fixate_cost"), rep) and idle > 0:
                return self._act(f"fixate {e['id']}",
                                 f"条目「{e['name']}」是临时的，先烧录固化")

        # 4) 记忆护栏
        integ = float(mem.get("integrity", 100.0))
        if mem and not mem.get("disabled") and integ < 55.0 and idle > 0 \
                and self._affordable(self.mem_cost, rep):
            return self._act("maintain",
                             f"记忆完整度 {integ:.0f}%，加固（劣化 "
                             f"{mem.get('degrade', 0):.3f}/s）")

        # 5) 电力节流：电量够高且煤不多 → 停一台电站省煤
        act = self._throttle_step(rep)
        if act:
            return act

        # 6) 紧急建设：电、煤、残骸合金必须先行（它们决定其它一切能不能转）
        for key in ("power", "coal", "scrap"):
            act = self._urgent_build(key, rep, idle, targets)
            if act:
                return act

        # 7) 恢复/固化/数据库作业进行中 → 等
        for kind in ("recover", "db_build"):
            js = self._jobs(rep, kind)
            if js:
                left = max(float(j.get("left") or 0) for j in js)
                return self._act(f"@tick {int(left) + 2}",
                                 f"{kind} 作业进行中（剩约 {left:.0f}s）",
                                 need_unit=False)

        # 8) 燃烧设施没设燃料（优先烧"会积压的副产物"，其次煤）
        #    注意：只有"已派员且未封存"的燃烧设施才能设燃料，否则命令会被拒，
        #    这条分支会永远命中并饿死后面的派员/建造步骤。
        for f in facs:
            if f.get("reason_code") != "no_fuel_set":
                continue
            if not f.get("assigned") or f.get("state") == "mothballed":
                continue
            fuel = self._pick_fuel(rep)
            cmd = f"fuel {f['id']} {fuel}"
            if idle <= 0 or self._failed_too_often(cmd):
                continue
            return self._act(cmd, f"{f['name']} 未设置燃料 → 用 {fuel}")

        # 9) 副产物积压 → 建/派放空塔
        over = self._backlog_over(rep)
        if over:
            act = self._build_role("vent", rep, idle, targets, force=True)
            if act:
                return act

        # 10) 派员：空闲单元给"当前需要"的设施（按计划表优先级）
        if idle > 0:
            act = self._staff_step(rep, targets)
            if act:
                return act

        # 11) 单元不够：释放一个"当前不需要"的（调离）
        if idle <= 0:
            act = self._release_step(rep, targets)
            if act:
                return act
            # 有作业在跑 → 等它交还单元
            if rep.get("jobs"):
                left = max(float(j.get("left") or 0) for j in rep["jobs"])
                return self._act(f"@tick {int(left) + 2}",
                                 f"单元全忙，等作业交还（剩约 {left:.0f}s）",
                                 need_unit=False)
        # 12) 主线推进（恢复下一个可研究的条目）
        endgame = self._endgame(rep)
        if not endgame:
            act = self._recover_step(rep, idle)
            if act:
                return act

        # 13) 数据库工程（主线全固化后优先于新建设施：材料要留给数据库）
        if endgame:
            act = self._construct_step(rep, idle)
            if act:
                return act

        # 13b) 建造计划内设施（含占领前置地块）
        for role in self.plan:
            if role["key"] == "vent" and not over:
                continue
            if role["key"] in ("power", "coal"):
                continue                    # 已在紧急步骤处理
            if endgame and role["key"] not in ENDGAME_ROLES:
                continue                    # 收尾阶段不再铺新摊子
            act = self._build_role(role["key"], rep, idle, targets)
            if act:
                return act

        # 14) 勘探缺的地块（收尾阶段只为"数据库相关产线"勘探：残骸/煤/铁/石灰…）
        act = self._survey_step(rep, idle, targets,
                                allowed=ENDGAME_ROLES if endgame else None)
        if act:
            return act

        # 15) 数据库工程（未到收尾阶段时也允许，条件是主线全固化）
        act = self._construct_step(rep, idle)
        if act:
            return act

        # 16) 无事可做 → 先强行腾一个单元（防死等），否则等产出/等作业
        if idle <= 0:
            act = self._release_step(rep, targets, force=True)
            if act:
                return act
        return self._act("@tick 60", "暂时没有可执行的动作（等产出/作业）",
                         need_unit=False)

    # ---- 各步骤 ----------------------------------------------------
    def _pick_fuel(self, rep: dict) -> str:
        """优先把"会积压的副产物"烧掉，其次煤；再看别的可燃物。"""
        pref = ["coalgas", "bfgas", "crack_gas", "coal", "coke", "coaltar"]
        for sub in pref:
            if self._stock(rep, sub) > 40.0:
                return sub
        return "coal"

    def _throttle_step(self, rep: dict) -> Optional[dict]:
        """电量够了就停电站省煤（煤多时不必省，直接充满）。"""
        stored = float((rep.get("power") or {}).get("stored", 0.0))
        coal = self._stock(rep, "coal")
        if coal >= 200.0 or stored < 120.0:
            return None
        plants = [f for f in rep.get("facilities", [])
                  if self._role_of(f, rep) == "power" and f.get("assigned")]
        if not plants:
            return None
        f = sorted(plants, key=lambda x: x["id"])[-1]
        return self._act(f"unassign {f['id']}",
                         f"电力存量 {stored:.0f}kWh 已够、煤仅 {coal:.0f} → "
                         f"停电站省煤")

    def _force_release(self, rep: dict, urgent_key: str) -> Optional[dict]:
        """紧急建设（电/煤）没单元时，强行调离一个最低优先级的设施。"""
        cands = []
        for f in rep.get("facilities", []):
            if not f.get("assigned"):
                continue
            key = self._role_of(f, rep)
            if key is None or key in ("power", urgent_key):
                continue
            cands.append((PRIORITY[key], f["id"], f["name"], key))
        if not cands:
            return None
        cands.sort(reverse=True)
        _p, fid, name, key = cands[0]
        return self._act(f"unassign {fid}",
                         f"腾单元给「{urgent_key}」（先停 {name}/{key}）")

    def _urgent_build(self, key: str, rep: dict, idle: int,
                      targets: dict) -> Optional[dict]:
        """电与煤的先手：没到位就先建/占领/勘探，必要时强行腾单元。"""
        role = self._role(key)
        if self._count(rep, key) >= self._count_target(role, rep):
            return None
        act = self._build_role(key, rep, idle, targets, force=True)
        if act:
            return act
        if idle > 0 and not self._candidates(role, rep):
            ring = self.survey_ring(role)
            cmd = f"survey {ring}"
            if not self._failed_too_often(cmd):
                return self._act(cmd, f"{role['why']}：缺地块 → 勘探圈{ring}")
        if idle <= 0:
            return self._force_release(rep, key)
        return None

    def _act(self, cmd: str, why: str, need_unit: bool = True) -> dict:
        self._n += 1
        self._last_cmd = cmd
        self.log.append(f"[{self._n}] {cmd}  ← {why}")
        return {"cmd": cmd, "why": why, "need_unit": need_unit}

    def note_result(self, cmd: str, ok: bool) -> None:
        """blind_test 回执：命令失败 → 记一次，避免死循环。"""
        if ok:
            self._fails.pop(cmd, None)
        else:
            self._fails[cmd] = self._fails.get(cmd, 0) + 1

    def _failed_too_often(self, cmd: str) -> bool:
        return self._fails.get(cmd, 0) >= 3

    def _staff_step(self, rep: dict, targets: dict) -> Optional[dict]:
        """把空闲单元派给"该转但没人"的设施（计划表优先级）。"""
        cands = []
        for f in rep.get("facilities", []):
            # 用"有没有单元"判定，而不是 state == unstaffed：
            # 停摆/故障停机的设施同样是"没单元"，也必须能补员，
            # 否则它们会永远瘫在那里（实测：硫酸厂因维护故障停机后无人补员）。
            if f.get("assigned") or f.get("state") in ("building", "mothballed"):
                continue
            key = self._role_of(f, rep)
            if key is None:
                continue
            role = self._role(key)
            if not self._needed(role, rep, targets):
                continue
            cands.append((PRIORITY[key], f["id"], f["name"], key))
        if cands:
            cands.sort()
            _p, fid, name, key = cands[0]
            return self._act(f"assign {fid}", f"{name} 没有单元（{key}）")
        # 没有待派员的设施：看有没有"封存但当前需要"的（解封需一个单元）
        moth = []
        for f in rep.get("facilities", []):
            if f.get("state") != "mothballed":
                continue
            key = self._role_of(f, rep)
            if key is None:
                continue
            if not self._needed(self._role(key), rep, targets):
                continue
            moth.append((PRIORITY[key], f["id"], f["name"], key))
        if moth:
            moth.sort()
            _p, fid, name, key = moth[0]
            return self._act(f"mothball {fid} off",
                             f"{name} 封存中但当前又需要它 → 解封重启（{key}）")
        return None

    def _release_step(self, rep: dict, targets: dict,
                      force: bool = False) -> Optional[dict]:
        """没有空闲单元：调离一个"当前不需要"的设施（越靠计划表末尾越先放）。

        force=True 时连"always"角色也能放（但绝不放掉最后一台电站/煤矿，
        除非对应库存已经很充裕）—— 这是防"单元全占 → 4 小时干等"的兜底。
        """
        plants = self._count(rep, "power")
        mines = self._count(rep, "coal")
        stored = float((rep.get("power") or {}).get("stored", 0.0))
        coal = self._stock(rep, "coal")
        cands = []
        for f in rep.get("facilities", []):
            if not f.get("assigned"):
                continue
            key = self._role_of(f, rep)
            if key is None:
                continue
            role = self._role(key)
            if role.get("staff") == "always" or key == "power":
                if not force:
                    continue
                # force：只有在"不缺"的前提下才动
                if key == "power" and (plants <= 1 or stored < 200.0):
                    continue
                if key == "coal" and (mines <= 1 or coal < 400.0):
                    continue
            else:
                if self._needed(role, rep, targets):
                    continue                   # 当前还需要它 → 不动
            cands.append((PRIORITY[key], f["id"], f["name"], key))
        if not cands:
            return None
        cands.sort(reverse=True)                   # 最低优先级先释放
        _p, fid, name, key = cands[0]
        # 产量远超需求 → 直接封存（零维护消耗），需要时再解封；否则只是调离
        role = self._role(key)
        out = role.get("out")
        surplus = (out is not None
                   and self._stock(rep, out) >= 2.0 * max(targets.get(out, 0.0), 1.0))
        verb = "mothball" if surplus else "unassign"
        why = (f"{out} 库存远超需求 → 封存 {name}/{key}（零维护消耗）"
               if surplus else f"腾一个单元出来（先停 {name}/{key}）")
        if force and not surplus:
            why = f"单元全被占住，强行腾一个（先停 {name}/{key}）"
        return self._act(f"{verb} {fid}", why)

    def _recover_step(self, rep: dict, idle: int) -> Optional[dict]:
        """恢复下一个主线条目：前置已固化 + 材料够 + 有单元。"""
        ent = rep.get("entries") or {}
        locked = {e["id"]: e for e in ent.get("locked", [])}
        perm = {e["id"] for e in ent.get("permanent", [])}
        if any(e["id"] in self.mainline_ids for e in ent.get("active", [])):
            return None                        # 还有临时条目没固化，先固化
        order = {eid: i for i, eid in enumerate(MAINLINE_ORDER)}
        ready = []
        for eid in self.mainline_ids:
            e = locked.get(eid)
            if e is None:
                continue
            if any(d not in perm for d in e.get("depends_on", [])):
                continue
            # 恢复只是"临时可用"（约 90s）：必须**同时**付得起固化费，
            # 否则等于白烧一次恢复（丢条目 + 浪费材料）。
            both: Dict[str, float] = dict(e.get("cost", {}))
            for sub, amt in (e.get("fixate_cost") or {}).items():
                both[sub] = both.get(sub, 0.0) + float(amt)
            if not self._affordable(both, rep):
                continue
            ready.append((order.get(eid, 99), eid, e))
        if not ready or idle <= 0:
            return None
        ready.sort()
        _o, eid, e = ready[0]
        cmd = f"recover {eid}"
        if self._failed_too_often(cmd):
            return None
        return self._act(cmd, f"可恢复知识「{e['name']}」"
                               + (f"（解锁 {'、'.join(e.get('unlocks_facility', []))}）"
                                  if e.get("unlocks_facility") else "（提升单元效能）"))

    def _build_role(self, key: str, rep: dict, idle: int, targets: dict,
                    force: bool = False) -> Optional[dict]:
        role = self._role(key)
        if self._count(rep, key) >= self._count_target(role, rep):
            return None
        if not force and not self._needed(role, rep, targets):
            return None
        d = self.fac_defs[role["def_id"]]
        rec = d.get("requires_recovery")
        if rec:
            perm = {e["id"] for e in
                    (rep.get("entries") or {}).get("permanent", [])}
            if rec not in perm:
                return None
        if idle <= 0:
            return None
        if not self._affordable(d.get("build_cost"), rep):
            return None
        cands = self._candidates(role, rep)
        if not cands:
            return None
        p = cands[0]
        if p.get("state") in ("claimed", "developed", "depleted"):
            cmd = f"build {p['id']} {role['def_id']}"
            why = f"{role['why']}：在 {p['id']} 建 {d['name']}"
        else:
            cmd = f"claim {p['id']}"
            why = f"{role['why']}：先占领 {p['id']}（{p.get('substance') or p.get('kind')}）"
        if self._failed_too_often(cmd):
            return None
        return self._act(cmd, why)

    def _survey_step(self, rep: dict, idle: int, targets: dict,
                     allowed: Optional[set] = None) -> Optional[dict]:
        """计划内角色差地块 → 勘探最可能有它的圈层。"""
        if idle <= 0:
            return None
        for role in self.plan:
            if allowed is not None and role["key"] not in allowed:
                continue
            if self._count(rep, role["key"]) >= self._count_target(role, rep):
                continue
            if not self._needed(role, rep, targets):
                continue
            d = self.fac_defs[role["def_id"]]
            rec = d.get("requires_recovery")
            perm = {e["id"] for e in
                    (rep.get("entries") or {}).get("permanent", [])}
            if rec and rec not in perm:
                continue
            if self._candidates(role, rep):
                continue
            ring = self.survey_ring(role)
            cmd = f"survey {ring}"
            if self._failed_too_often(cmd):
                continue
            return self._act(cmd, f"缺「{role['why']}」的地块 → 勘探圈{ring}")
        return None

    def _endgame(self, rep: dict) -> bool:
        """主线条目是否已全部永久固化（可以开始建数据库了）。"""
        perm = {e["id"] for e in (rep.get("entries") or {}).get("permanent", [])}
        return all(eid in perm for eid in self.mainline_ids)

    def _construct_step(self, rep: dict, idle: int) -> Optional[dict]:
        """所有主线条目固化 → 建数据库子系统（含电力存量门槛）。"""
        ent = rep.get("entries") or {}
        perm = {e["id"] for e in ent.get("permanent", [])}
        if any(eid not in perm for eid in self.mainline_ids):
            return None
        if any(e["id"] in self.mainline_ids for e in ent.get("active", [])):
            return None
        if self._jobs(rep, "db_build"):
            return None
        db = rep.get("database") or {}
        idx = int(db.get("built", 0))
        if idx >= len(self.db_projects):
            return None
        p = self.db_projects[idx]
        if idle <= 0 or not self._affordable(p.get("cost"), rep):
            return None
        preq = float(p.get("power_req", 0.0))
        stored = float((rep.get("power") or {}).get("stored", 0.0))
        if preq > 0 and stored < preq:
            return self._act("@tick 60",
                             f"等待电网存量（{stored:.0f}/{preq:.0f}kWh）",
                             need_unit=False)
        return self._act("construct", f"建造数据库子系统：{p['name']}")
