"""百科（WIKI）数据层：把散落在 content 里的知识汇成可检索词条。

设计原则（与项目一致：全部数据驱动 + 模块化）：
- **自动词条**：物质 / 设施 / 配方 / 恢复条目 四类由现有 content 运行时生成，
  与游戏内容天然同步（新增一个设施，百科立刻有它的条目，零维护）。
- **手写词条**：content/wiki/*.json 提供设定 / 背景 / 教程 / 科普等叙述性条目。
- 本模块只做"数据 + 检索"，不做界面；UI 面板与控制台 `wiki` 命令共用同一实例。
- 纯派生数据，不参与 tick、不落存档（to_dict 恒为空）。
"""
import json
import os
from typing import Dict, List, Optional

CATEGORY_ORDER = ["设定", "教程", "阶段", "科普", "资源", "建筑", "配方", "科技"]
CATEGORY_NAMES = {
    "设定": "设定与背景",
    "教程": "上手教程",
    "阶段": "发展阶段导读",
    "科普": "化工与工艺科普",
    "资源": "物质与资源",
    "建筑": "设施与建筑",
    "配方": "配方与产线",
    "科技": "数据库科技条目",
}


class WikiSystem:
    def __init__(self, docs: Optional[List[dict]] = None,
                 substances: Optional[List[dict]] = None,
                 facilities: Optional[List[dict]] = None,
                 recipes: Optional[List[dict]] = None,
                 recovery_entries: Optional[List[dict]] = None,
                 bootstrap: Optional[dict] = None) -> None:
        self.entries: Dict[str, dict] = {}
        self._extra_docs = docs or []
        self._substances = {s["id"]: s for s in (substances or [])}
        self._facilities = facilities or []
        self._recipes = {r["id"]: r for r in (recipes or [])}
        self._recovery = {e["id"]: e for e in (recovery_entries or [])}
        self._bootstrap = bootstrap or {}

    def start(self, engine: object) -> None:
        self._engine = engine
        self.build()

    # ---- 构建词条 --------------------------------------------------
    def build(self) -> None:
        self.entries = {}
        self._add_docs(self._extra_docs)
        self._add_substances()
        self._add_facilities()
        self._add_recipes()
        self._add_recovery()

    def _put(self, eid: str, title: str, category: str, body: str,
             tags: Optional[List[str]] = None, source: str = "auto") -> None:
        if not eid or eid in self.entries:
            return
        self.entries[eid] = {
            "id": eid, "title": title, "category": category,
            "body": body.strip(), "tags": tags or [], "source": source,
        }

    def _add_docs(self, docs: List[dict]) -> None:
        for d in docs:
            self._put(d.get("id", ""), d.get("title", ""),
                      d.get("category", "设定"), d.get("body", ""),
                      d.get("tags"), source="doc")

    # ---- 自动：物质 -------------------------------------------------
    def _who_uses(self, rid: str):
        """返回 (生产者设施名列表, 消费者设施名列表)。"""
        makers, users = [], []
        fac_names = {}
        for d in self._facilities:
            fac_names[d.get("recipe", "")] = d.get("name", d["id"])
        for r in self._recipes.values():
            name = fac_names.get(r["id"], r.get("name", r["id"]))
            if rid in r.get("outputs", {}) or rid in r.get("byproducts", {}):
                makers.append(name)
            if rid in r.get("inputs", {}):
                users.append(name)
        return makers, users

    def _add_substances(self) -> None:
        for sid, s in self._substances.items():
            lines = [s.get("desc", "")]
            meta = [f"类别：{s.get('category', '—')}",
                    f"单位：{s.get('unit', '—')}"]
            if s.get("heat_value"):
                meta.append(f"热值：{s['heat_value']:g} MJ/kg")
            if s.get("fuel_class"):
                cls = {"solid": "固体", "liquid": "液体",
                       "gas": "气体"}.get(s["fuel_class"], s["fuel_class"])
                meta.append(f"燃料类别：{cls}")
            lines.append("")
            lines.append(" · ".join(meta))
            makers, users = self._who_uses(sid)
            if makers:
                lines.append("产出：" + "、".join(sorted(set(makers))))
            if users:
                lines.append("消耗：" + "、".join(sorted(set(users))))
            self._put(f"sub:{sid}", s.get("name", sid), "资源",
                      "\n".join(l for l in lines if l is not None),
                      tags=[s.get("category", "")])

    # ---- 自动：设施 -------------------------------------------------
    def _add_facilities(self) -> None:
        for d in self._facilities:
            fid = d["id"]
            lines = [d.get("desc", "")]
            cost = d.get("build_cost", {})
            cost_txt = "、".join(f"{self._sname(k)} {v:g}{self._sunit(k)}"
                                for k, v in cost.items()) or "无"
            lines.append("")
            lines.append(f"建造：{cost_txt}｜占地："
                         + "/".join(d.get("allowed_plot_kinds", []))
                         + f"｜执行单元位：{d.get('slots', 1)}")
            if d.get("build_time"):
                lines.append(f"建造耗时：{d['build_time']:g}s")
            if d.get("extract_rate"):
                lines.append(f"提取：{d['extract_rate']:g}/s"
                             f"｜耗电 {d.get('power_use', 0):g}/s")
            if d.get("kind") == "burner":
                cls = "/".join(d.get("fuel_classes", []) or ["任意"])
                lines.append(f"燃烧发电：燃速 {d.get('burn_rate', 0):g}/s"
                             f"｜效率 {d.get('burn_efficiency', 0):g}"
                             f"｜燃料类别 {cls}")
            if d.get("kind") == "renewable":
                pk = {"solar": "光热", "pv": "光伏",
                      "wind": "风电"}.get(d.get("power_kind"), "可再生")
                lines.append(f"可再生发电（{pk}）："
                             f"额定 {d.get('capacity', 0):g}/s")
            rec = self._recipes.get(d.get("recipe", ""))
            if rec is not None:
                lines.append("")
                lines.append(f"配方：{rec.get('name', rec['id'])}")
                ins = "、".join(f"{self._sname(k)} {v:g}/s"
                                for k, v in rec.get("inputs", {}).items())
                outs = "、".join(f"{self._sname(k)} {v:g}/s"
                                 for k, v in rec.get("outputs", {}).items())
                if ins:
                    lines.append(f"  消耗：{ins}")
                if outs:
                    lines.append(f"  产出：{outs}")
                byp = rec.get("byproducts", {})
                if byp:
                    lines.append("  副产：" + "、".join(
                        f"{self._sname(k)} {v:g}/s" for k, v in byp.items()))
                if rec.get("desc"):
                    lines.append(f"  说明：{rec['desc']}")
            need = d.get("requires_recovery")
            if need:
                e = self._recovery.get(need, {})
                lines.append("")
                lines.append(f"解锁科技：{e.get('name', need)}（{need}）")
            self._put(f"fac:{fid}", d.get("name", fid), "建筑",
                      "\n".join(lines),
                      tags=[d.get("kind", "recipe")])

    # ---- 自动：配方 -------------------------------------------------
    def _add_recipes(self) -> None:
        owners = {}
        for d in self._facilities:
            if d.get("recipe"):
                owners.setdefault(d["recipe"], []).append(d.get("name", d["id"]))
        for rid, r in self._recipes.items():
            lines = [r.get("desc", "")]
            ins = "、".join(f"{self._sname(k)} {v:g}/s"
                            for k, v in r.get("inputs", {}).items()) or "无"
            outs = "、".join(f"{self._sname(k)} {v:g}/s"
                             for k, v in r.get("outputs", {}).items()) or "无"
            lines.append("")
            lines.append(f"输入：{ins}")
            lines.append(f"输出：{outs}")
            byp = r.get("byproducts", {})
            if byp:
                lines.append("副产：" + "、".join(
                    f"{self._sname(k)} {v:g}/s" for k, v in byp.items()))
            if rid in owners:
                lines.append(f"承载设施：{'、'.join(owners[rid])}")
            self._put(f"rec:{rid}", r.get("name", rid), "配方",
                      "\n".join(lines))

    # ---- 自动：恢复条目 ---------------------------------------------
    def _add_recovery(self) -> None:
        for eid, e in self._recovery.items():
            lines = [e.get("desc", "")]
            cost = "、".join(f"{self._sname(k)} {v:g}"
                             for k, v in e.get("cost", {}).items()) or "无"
            fix = "、".join(f"{self._sname(k)} {v:g}"
                            for k, v in e.get("fixate_cost", {}).items()) or "无"
            lines.append("")
            lines.append(f"恢复成本：{cost}")
            lines.append(f"固化成本：{fix}")
            if e.get("depends_on"):
                deps = "、".join(self._recovery.get(d, {}).get("name", d)
                                for d in e["depends_on"])
                lines.append(f"前置：{deps}")
            if e.get("unlocks_facility"):
                lines.append("解锁：" + "、".join(
                    self._fac_name(x) for x in e["unlocks_facility"]))
            grants = e.get("grants") or {}
            if grants:
                names = {"unit_efficiency": "单元效能"}
                lines.append("效果：" + "、".join(
                    f"{names.get(k, k)} +{float(v):g}"
                    for k, v in grants.items())
                    + "（恢复即可生效，条目丢失则失效）")
            lines.append("性质：" + ("可选分支（不影响通关迁移）"
                                     if e.get("optional") else "主线知识"))
            self._put(f"db:{eid}", e.get("name", eid), "科技",
                      "\n".join(lines))

    # ---- 名称工具 ---------------------------------------------------
    def _sname(self, rid: str) -> str:
        s = self._substances.get(rid)
        return s["name"] if s else rid

    def _sunit(self, rid: str) -> str:
        s = self._substances.get(rid)
        return s.get("unit", "") if s else ""

    def _fac_name(self, fid: str) -> str:
        for d in self._facilities:
            if d["id"] == fid:
                return d.get("name", fid)
        return fid

    # ---- 检索 ------------------------------------------------------
    def categories(self) -> List[str]:
        present = {e["category"] for e in self.entries.values()}
        ordered = [c for c in CATEGORY_ORDER if c in present]
        ordered += sorted(present - set(ordered))
        return ordered

    def list_by_category(self, category: str) -> List[dict]:
        items = [e for e in self.entries.values() if e["category"] == category]
        return sorted(items, key=lambda e: e["title"])

    def get(self, eid: str) -> Optional[dict]:
        if eid in self.entries:
            return self.entries[eid]
        # 允许用标题/别名命中
        for e in self.entries.values():
            if e["title"] == eid:
                return e
        low = eid.lower()
        for e in self.entries.values():
            if e["id"].lower() == low:
                return e
        return None

    def search(self, keyword: str, limit: int = 30) -> List[dict]:
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        hits = []
        for e in self.entries.values():
            score = 0
            if kw == e["title"].lower() or kw == e["id"].lower():
                score = 100
            elif kw in e["title"].lower():
                score = 60
            elif kw in e["id"].lower():
                score = 40
            elif any(kw in t.lower() for t in e["tags"]):
                score = 25
            elif kw in e["body"].lower():
                score = 10
            if score:
                hits.append((score, e))
        hits.sort(key=lambda x: (-x[0], x[1]["title"]))
        return [e for _s, e in hits[:limit]]

    def count(self) -> int:
        return len(self.entries)

    # ---- 存档（纯派生：无需持久化）--------------------------------
    def to_dict(self) -> dict:
        return {}

    def load(self, data: dict) -> None:
        pass


def load_wiki_docs(root: str) -> List[dict]:
    """读取 content/wiki/*.json 的手写词条（按文件名排序合并）。"""
    docs: List[dict] = []
    wiki_dir = os.path.join(root, "content", "wiki")
    if not os.path.isdir(wiki_dir):
        return docs
    for name in sorted(os.listdir(wiki_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(wiki_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        items = data.get("entries", data if isinstance(data, list) else [])
        docs.extend(items)
    return docs
