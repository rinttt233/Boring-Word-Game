"""百科（WIKI）测试：自动词条生成、分类、检索、命令。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from systems.wiki import WikiSystem, load_wiki_docs   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUBS = [
    {"id": "coal", "name": "煤", "category": "矿物", "unit": "t",
     "desc": "能源。", "heat_value": 24.0, "fuel_class": "solid"},
    {"id": "scrap_alloy", "name": "残骸合金", "category": "回收",
     "unit": "t", "desc": "结构材料。"},
    {"id": "electricity", "name": "电力", "category": "能源",
     "unit": "kWh", "desc": "通用输入。"},
]
FACS = [
    {"id": "power_plant", "name": "燃煤发电站",
     "allowed_plot_kinds": ["empty"], "build_cost": {"scrap_alloy": 15.0},
     "recipe": "burn_coal", "slots": 1, "build_time": 8,
     "desc": "烧煤产电。"},
    {"id": "gpu", "name": "测试炉", "allowed_plot_kinds": ["empty"],
     "build_cost": {}, "kind": "burner", "burn_rate": 0.25,
     "burn_efficiency": 0.3, "fuel_classes": ["solid"], "slots": 1,
     "requires_recovery": "db_x", "desc": "炉。"},
]
RECIPES = [
    {"id": "burn_coal", "name": "燃煤发电", "inputs": {"coal": 0.4},
     "outputs": {"electricity": 1.6}, "desc": "烧煤。"},
]
RECOVERY = [
    {"id": "db_x", "name": "测试工艺包", "desc": "测试。",
     "cost": {"coal": 10.0}, "fixate_cost": {"coal": 5.0},
     "unlocks_facility": ["gpu"], "optional": True},
]


def make_wiki():
    w = WikiSystem(docs=[{"id": "doc_a", "title": "手写条目",
                          "category": "设定", "body": "正文", "tags": ["x"]}],
                   substances=SUBS, facilities=FACS, recipes=RECIPES,
                   recovery_entries=RECOVERY)
    w.build()
    return w


class TestWiki(unittest.TestCase):
    def test_auto_entries(self):
        w = make_wiki()
        # 物质/设施/配方/条目 各生成一条（fac 两条）
        self.assertIn("sub:coal", w.entries)
        self.assertIn("fac:power_plant", w.entries)
        self.assertIn("fac:gpu", w.entries)
        self.assertIn("rec:burn_coal", w.entries)
        self.assertIn("db:db_x", w.entries)
        self.assertIn("doc_a", w.entries)
        self.assertEqual(w.count(), 8)   # 3 物质 + 2 设施 + 1 配方 + 1 科技 + 1 手写

    def test_substance_body_has_uses(self):
        w = make_wiki()
        body = w.entries["sub:coal"]["body"]
        self.assertIn("热值：24 MJ/kg", body)
        self.assertIn("燃料类别：固体", body)
        self.assertIn("消耗：燃煤发电站", body)   # 被谁消耗

    def test_facility_body_details(self):
        w = make_wiki()
        body = w.entries["fac:power_plant"]["body"]
        self.assertIn("建造：残骸合金 15t", body)
        self.assertIn("产出：电力 1.6/s", body)
        body2 = w.entries["fac:gpu"]["body"]
        self.assertIn("燃料类别 solid", body2)
        self.assertIn("解锁科技：测试工艺包", body2)

    def test_recovery_entry_marks_optional(self):
        w = make_wiki()
        body = w.entries["db:db_x"]["body"]
        self.assertIn("可选分支", body)
        self.assertIn("解锁：测试炉", body)

    def test_categories_and_search(self):
        w = make_wiki()
        cats = w.categories()
        self.assertIn("设定", cats)
        self.assertIn("资源", cats)
        self.assertIn("建筑", cats)
        # 精确名优先
        hits = w.search("煤")
        self.assertEqual(hits[0]["id"], "sub:coal")
        # 正文命中
        self.assertTrue(w.search("烧煤"))
        self.assertEqual(w.search("不存在的词"), [])

    def test_get_by_id_and_title(self):
        w = make_wiki()
        self.assertIsNotNone(w.get("sub:coal"))
        self.assertIsNotNone(w.get("煤"))          # 按标题
        self.assertIsNone(w.get("nope"))

    def test_load_docs_from_content(self):
        docs = load_wiki_docs(ROOT)
        self.assertGreaterEqual(len(docs), 50)
        ids = [d["id"] for d in docs]
        self.assertEqual(len(ids), len(set(ids)), "手写词条 id 重复")
        for d in docs:
            self.assertTrue(d.get("title"), f"缺标题: {d.get('id')}")
            self.assertTrue(d.get("body"), f"缺正文: {d.get('id')}")
            self.assertIn(d.get("category"), ("设定", "教程", "阶段", "科普"))


if __name__ == "__main__":
    unittest.main()
