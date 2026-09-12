"""右区面板：资源/单元/恢复/工艺/终局/环境（P1 拆分）。"""
import tkinter as tk

from ui import theme as T
from ui.commands import fmt_time
from ui.panels.base import Panel, Refreshable, _RightPanel, style_tree


class ResourcesPanel(_RightPanel):
    key = "resources"
    title = "资源"

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              font=T.FONT_MONO, fg=T.TEXT, bg=T.PANEL)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        res = engine.economy.snapshot()
        if not res:
            self.lines.configure(text="（空）")
            return
        from ui.commands import fmt_amt
        rows = [f"{router.rname(k):<8} {fmt_amt(v)}{router.runit(k)}"
                for k, v in sorted(res.items(), key=lambda kv: -kv[1])]
        self.lines.configure(text="\n".join(rows))


class UnitsPanel(_RightPanel):
    key = "units"
    title = "执行单元"

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              font=T.FONT_MONO, fg=T.TEXT, bg=T.PANEL)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        eff = float(getattr(engine.units, "efficiency", 1.0) or 1.0)
        busy = sum(1 for u in engine.units.units if u.status == "busy")
        head = (f"效能 ×{eff:.2f}  (在役 {len(engine.units.units)} · "
                f"运转 {busy} · 空闲 {engine.units.count_idle()})")
        if eff > 1.0001:
            head += "\n  └ 产能与作业速度 ×%.2f（恢复的调度知识）" % eff
        rows = [head, ""]
        for u in engine.units.units:
            t = f" → {u.task}" if u.task else ""
            rows.append(f"{u.id} {u.name:<8} {u.status}{t}")
        self.lines.configure(text="\n".join(rows) if len(rows) > 2
                             else head + "\n（无单元）")


class RecoveryPanel(Refreshable, Panel):
    """恢复树：分组 + 前置查看 + 自动排序（类建筑树模式）。

    分组（自上而下）：
    - 已恢复（permanent）→ 置顶，默认折叠
    - 可研发（locked 且前置满足且材料齐）→ 中间，材料齐/解锁的前移
    - 锁定（依赖未满足/材料不足）→ 末尾，标灰
    点击条目 → 详情区显示：状态/成本/前置依赖。
    """
    key = "recovery"
    title = "恢复"

    def unlock(self, engine):
        return engine.registry.get("recovery") is not None

    def __init__(self):
        self.selected = None
        self._open_by_key = {}

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap
        from tkinter import ttk
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.tree.pack(side="top", fill="both", expand=True)
        style = ttk.Style()
        style_tree(self.tree, "Recovery.Treeview")
        self.tree.tag_configure("group", foreground=T.TEXT_SUB)
        self.tree.tag_configure("done", foreground=T.TEXT_SUB)
        self.tree.tag_configure("available", foreground=T.TEXT)
        self.tree.tag_configure("locked", foreground=T.TEXT_DIM)
        tsb = tk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", self._record_open)
        self.tree.bind("<<TreeviewClose>>", self._record_open)
        self.tree.bind("<Double-Button-1>", self._on_double)
        self._tree_rows = {}   # iid -> entry_id

        # 详情区
        self.detail = tk.Frame(wrap, bg=T.PANEL)
        self.detail.pack(side="bottom", fill="x", pady=(4, 0))
        self.detail_label = tk.Label(self.detail, text="点击条目查看详情",
                                     justify="left", anchor="w",
                                     font=T.FONT_SMALL, fg=T.TEXT_SUB,
                                     bg=T.PANEL, wraplength=520)
        self.detail_label.pack(side="top", fill="x", padx=6, pady=4)
        # 操作按钮
        brow = tk.Frame(self.detail, bg=T.PANEL)
        brow.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
        for txt, cmd in [("recover", "recover"), ("fixate", "fixate")]:
            b = tk.Label(brow, text=txt, font=T.FONT_UI, fg=T.TEXT,
                         bg=T.PANEL_ALT, padx=8, pady=2, cursor="hand2")
            b.pack(side="left", padx=4)
            b.bind("<Button-1>", lambda e, c=cmd: self._act(c))
        self.refresh(engine, router)

    def _entry_state(self, rec, e):
        return e.get("state", "locked")

    def _prereq_ok(self, rec, e):
        """前置是否满足（已永久固化）。返回 (ok, missing_names)。"""
        missing = []
        for dep in e.get("depends_on", []):
            if rec.status.get(dep) != "permanent":
                name = rec.entries.get(dep, {}).get("name", dep)
                missing.append(name)
        return (not missing), missing

    def _cost_ok(self, e):
        for rid, amt in e.get("cost", {}).items():
            if self.engine.economy.get(rid) < float(amt):
                return False
        return True

    def _group_for(self, rec, e):
        """条目分组：permanent→已恢复；前置满足→可研发；否则→锁定。"""
        st = e.get("state", "locked")
        if st == "permanent":
            return "done", "已恢复"
        ok, _missing = self._prereq_ok(rec, e)
        if ok:
            return "available", "可研发"
        return "locked", "锁定"

    def _rebuild(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_rows = {}
        rec = self.engine.registry.get("recovery")
        if rec is None:
            return
        # 分组：done 置顶、available 中间、locked 末尾
        groups = {"done": [], "available": [], "locked": []}
        for e in rec.entry_list():
            g, _name = self._group_for(rec, e)
            groups[g].append(e)
        # 组内排序：available 材料齐的前，locked 依赖排列靠前
        def avail_key(needs_cost):
            # 材料齐 → 前
            return (0 if self._cost_ok(needs_cost) else 1)
        group_defs = [
            ("done", "已恢复", False),      # 默认折叠
            ("available", "可研发", True),
            ("locked", "锁定", True),
        ]
        for gname, label, default_open in group_defs:
            items = groups[gname]
            if not items:
                continue
            gkey = f"recovery:{gname}"
            g_open = self._open_by_key.get(gkey, default_open)
            gid = self.tree.insert("", "end", iid=gkey, text=label,
                                   open=g_open, tags=("group",))
            self._open_by_key[gkey] = g_open
            if gname == "available":
                items = sorted(items, key=avail_key)
            for e in items:
                iid = self.tree.insert(
                    gid, "end", text=self._entry_label(e),
                    tags=(gname,), values=(e["id"],))
                self._tree_rows[iid] = e["id"]
                self._open_by_key[iid] = True

    def _entry_label(self, e):
        st = e.get("state", "locked")
        stmap = {"permanent": "√", "active": "临时", "locked": ""}
        mark = stmap.get(st, "")
        name = e["name"]
        if st == "permanent":
            return f"{name} [{mark}]"
        ok, missing = self._prereq_ok(self.engine.registry.get("recovery"), e)
        if not ok:
            return f"{name} {T.S_LOCK}需{','.join(missing)}"
        # 材料齐与否
        cost_ok = self._cost_ok(e)
        cost_txt = "" if cost_ok else " (材料不足)"
        return f"{name}{cost_txt}"

    def _record_open(self, _e=None):
        def walk(p):
            for iid in self.tree.get_children(p):
                self._open_by_key[iid] = bool(self.tree.item(iid, "open"))
                walk(iid)
        walk("")

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        eid = self._tree_rows.get(iid)
        if eid is None:
            return
        self.selected = eid
        self._render_detail()

    def _on_double(self, _evt=None):
        """双击条目 → 打开百科该科技词条。"""
        sel = self.tree.selection()
        if not sel:
            return
        eid = self._tree_rows.get(sel[0])
        cb = getattr(self, "open_wiki", None)
        if eid and cb is not None:
            cb(f"db:{eid}")

    def _render_detail(self):
        rec = self.engine.registry.get("recovery")
        e = rec.entries.get(self.selected)
        if e is None:
            return
        st = rec.status.get(self.selected, "locked")
        stmap = {"locked": "未恢复", "active": "临时(待固化)",
                 "permanent": "已固化"}
        lines = [f"{e['name']} [{stmap.get(st, st)}]"]
        # 前置依赖
        ok, missing = self._prereq_ok(rec, e)
        if e.get("depends_on"):
            m = "、".join(missing) if missing else "全部满足"
            lines.append(f"前置: {m}")
        # 成本
        cost = ", ".join(f"{self.router.rname(k)} {v:g}{self.router.runit(k)}"
                         for k, v in e.get("cost", {}).items())
        lines.append(f"恢复成本: {cost or '无'}")
        fix = ", ".join(f"{self.router.rname(k)} {v:g}{self.router.runit(k)}"
                        for k, v in e.get("fixate_cost", {}).items())
        lines.append(f"固化成本: {fix or '无'}")
        unlocks = e.get("unlocks_facility", [])
        if unlocks:
            lines.append(f"解锁: {', '.join(unlocks)}")
        if e.get("desc"):
            lines.append(f"说明: {e['desc']}")
        locked_btn = st == "permanent"
        self.detail_label.configure(text="\n".join(lines),
                                    fg=T.TEXT if not locked_btn else T.TEXT_SUB)
        self.detail_label.after_idle(self._fit_wrap)

    def _fit_wrap(self):
        try:
            w = self.detail_label.winfo_width()
            if w > 40:
                self.detail_label.configure(wraplength=w - 12)
        except tk.TclError:
            pass

    def _act(self, verb):
        if self.selected is None:
            self.engine.log("[恢复] 请先点击选择一个条目。")
            return
        self.router.execute(f"{verb} {self.selected}")

    def _refresh_sig(self, engine):
        rec = self.engine.registry.get("recovery")
        if rec is None:
            return ()
        return tuple(sorted(
            (eid, rec.status.get(eid), self._cost_ok(rec.entries[eid]))
            for eid in rec.entries))

    def _do_rebuild(self):
        self._rebuild()

    def _after_refresh(self, engine, router):
        if self.selected is not None:
            self._render_detail()


class ProcessesPanel(_RightPanel):
    """物料平衡总览：各物质全厂潜在产能/消耗 vs 净额，标出瓶颈设施。

    与"电力"页配对：电力=能量视角，本页=物质视角（谁产能不足一眼可见）。
    """
    key = "processes"
    title = "物料"

    def unlock(self, engine):
        rec = engine.registry.get("recovery")
        return rec is not None and rec.is_unlocked("db_coking")

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              anchor="nw", font=T.FONT_MONO_SMALL,
                              fg=T.TEXT, bg=T.PANEL, wraplength=430)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        ind = engine.registry.get("industry")
        if ind is None:
            self.lines.configure(text="（无工业模块）")
            return
        made = {}
        used = {}
        stalled = []
        active = 0
        power_only = 0
        for f in ind.facilities.values():
            d = ind.defs[f.def_id]
            if not f.assigned:
                continue                    # 未分配单元 = 不计入潜在产能
            if f.stalled_reported:
                stalled.append(f.id)
                continue
            if d.get("kind") in ("burner", "renewable"):
                power_only += 1             # 纯发电设施 → 见"电力"页
                continue
            active += 1
            if d.get("extract_rate"):
                plot = engine.world.get(f.plot_id)
                sub = ("water" if plot is not None
                       and plot.kind == "water"
                       else (plot.substance if plot is not None else None))
                if sub:
                    made[sub] = made.get(sub, 0.0) + float(d["extract_rate"])
                continue
            r = ind.recipes.get(d.get("recipe", ""), {})
            for k, v in r.get("outputs", {}).items():
                made[k] = made.get(k, 0.0) + float(v)
            for k, v in r.get("byproducts", {}).items():
                made[k] = made.get(k, 0.0) + float(v)
            for k, v in r.get("inputs", {}).items():
                used[k] = used.get(k, 0.0) + float(v)
        if not made and not used:
            msg = "（暂无物料产出的运转设施）"
            if power_only:
                msg += f"\n发电设施 {power_only} 座 → 见「电力」页。"
            self.lines.configure(text=msg)
            return
        keys = sorted(set(made) | set(used),
                      key=lambda k: -(made.get(k, 0.0) - used.get(k, 0.0)))
        rows = [f"物料设施 {active} 座"
                + (f" | 发电 {power_only} 座" if power_only else "")
                + (f" | 停摆 {len(stalled)} 座" if stalled else "")]
        rows.append("物质        产/s    耗/s    净/s")
        for k in keys:
            m = made.get(k, 0.0)
            u = used.get(k, 0.0)
            net = m - u
            flag = "缺" if net < -1e-9 else ("余" if net > 1e-9 else "")
            rows.append(f"{router.rname(k):<8} {m:>6.2f} {u:>6.2f} "
                        f"{net:>+7.2f} {flag}")
        if stalled:
            rows.append("")
            rows.append("停摆: " + " ".join(stalled[:8]))
        self.lines.configure(text="\n".join(rows))


class DatabasePanel(_RightPanel):
    key = "database"
    title = "终局"

    def unlock(self, engine):
        db = engine.registry.get("database")
        return db is not None and len(db.built) > 0

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              font=T.FONT_MONO, fg=T.TEXT, bg=T.PANEL)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)
        row = tk.Frame(self.body, bg=T.PANEL)
        row.pack(side="bottom", fill="x", padx=4, pady=4)
        for txt, cmd in [("construct", "construct"), ("migrate", "migrate")]:
            b = tk.Label(row, text=txt, font=T.FONT_UI, fg=T.TEXT,
                         bg=T.PANEL_ALT, padx=8, pady=2, cursor="hand2")
            b.pack(side="left", padx=4)
            b.bind("<Button-1>", lambda e, c=cmd: self.router.execute(c))

    def refresh(self, engine, router):
        db = engine.registry.get("database")
        if db is None:
            self.lines.configure(text="（无终局系统）")
            return
        if db.is_complete():
            self.lines.configure(text="√ 可靠数据库已建成，劣化终止。")
            return
        rows = []
        for p in db.projects:
            st = "√" if p["id"] in db.built else "·"
            rows.append(f"{st} {p['name']} ({db.progress_text()})")
        self.lines.configure(text="\n".join(rows))


class WikiPanel(Panel):
    """百科：搜索 + 分类树 + 词条详情（数据来自 systems.wiki，含自动生成的资源/建筑/配方/科技词条）。"""
    key = "wiki"
    title = "百科"

    def unlock(self, engine):
        return engine.registry.get("wiki") is not None

    def __init__(self):
        self.selected = None
        self._open_by_key = {}

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        wiki = engine.registry.get("wiki")
        self.wiki = wiki
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap

        # 搜索行
        srow = tk.Frame(wrap, bg=T.BG)
        srow.pack(side="top", fill="x", padx=2, pady=(2, 0))
        tk.Label(srow, text="搜索:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.BG).pack(side="left", padx=(2, 4))
        self.search_entry = tk.Entry(srow, font=T.FONT_SMALL, bg=T.BG,
                                     fg=T.TEXT, insertbackground=T.TEXT,
                                     relief="flat", highlightthickness=1,
                                     highlightbackground=T.BORDER_DARK,
                                     highlightcolor=T.ACCENT)
        self.search_entry.pack(side="left", fill="x", expand=True,
                               padx=(0, 4), ipady=1)
        self.search_entry.bind("<Return>", lambda e: self._do_search())
        sbtn = tk.Label(srow, text="查找", font=T.FONT_SMALL, fg=T.TEXT,
                        bg=T.PANEL, padx=6, pady=1, cursor="hand2")
        sbtn.pack(side="left")
        sbtn.bind("<Button-1>", lambda e: self._do_search())
        clr = tk.Label(srow, text="清空", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                       bg=T.PANEL, padx=6, pady=1, cursor="hand2")
        clr.pack(side="left", padx=2)
        clr.bind("<Button-1>", lambda e: self._clear_search())

        # 分类树
        from tkinter import ttk
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="browse",
                                 height=10)
        self.tree.pack(side="top", fill="both", expand=True, pady=(2, 0))
        style_tree(self.tree, "Wiki.Treeview")
        self.tree.tag_configure("cat", foreground=T.TEXT_SUB)
        self.tree.tag_configure("ent", foreground=T.TEXT)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        tsb = tk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)

        # 详情（可滚动只读文本）
        det = tk.Frame(wrap, bg=T.PANEL)
        det.pack(side="bottom", fill="both", expand=True, pady=(3, 0))
        self.detail = tk.Text(det, font=T.FONT_MONO_SMALL, bg=T.PANEL,
                              fg=T.TEXT, relief="flat", state="disabled",
                              wrap="word", padx=6, pady=4,
                              highlightthickness=1,
                              highlightbackground=T.BORDER, height=10)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb = tk.Scrollbar(det, orient="vertical", command=self.detail.yview)
        dsb.pack(side="right", fill="y")
        self.detail.configure(yscrollcommand=dsb.set)
        # 轻量排版标签：标题加粗、元信息灰、反应式缩进等宽、要点缩进
        self.detail.tag_configure("title", font=T.FONT_UI_BOLD,
                                  foreground=T.TEXT, spacing3=2)
        self.detail.tag_configure("meta", font=T.FONT_MONO_SMALL,
                                  foreground=T.TEXT_DIM, spacing3=4)
        self.detail.tag_configure("reaction", font=T.FONT_MONO,
                                  foreground=T.TEXT, spacing1=2, spacing3=2,
                                  lmargin1=8, lmargin2=8)
        self.detail.tag_configure("bullet", lmargin1=6, lmargin2=14)
        self.detail.tag_configure("section", font=T.FONT_UI_BOLD,
                                  foreground=T.TEXT_SUB, spacing1=3)
        from ui.panels.base import bind_mousewheel
        bind_mousewheel(self.detail)
        self._rebuild_tree()

    # ---- 树构建 ----------------------------------------------------
    def _clear_tree(self):
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._tree_rows = {}
        self._open_by_key = getattr(self, "_open_by_key", {})

    def _rebuild_tree(self, entries=None, grouped=True):
        self._clear_tree()
        self._entries = entries if entries is not None else None
        if entries is None:
            for cat in self.wiki.categories():
                items = self.wiki.list_by_category(cat)
                cid = self.tree.insert("", "end", iid=f"cat:{cat}",
                                       text=f"{cat}（{len(items)}）",
                                       tags=("cat",), open=True)
                self._open_by_key[cid] = True
                for e in items:
                    iid = self.tree.insert(cid, "end", text=e["title"],
                                           tags=("ent",), values=(e["id"],))
                    self._tree_rows[iid] = e["id"]
        else:
            for e in entries:
                iid = self.tree.insert("", "end",
                                       text=f"{e['title']}  [{e['category']}]",
                                       tags=("ent",), values=(e["id"],))
                self._tree_rows[iid] = e["id"]

    def _do_search(self):
        kw = self.search_entry.get().strip()
        if not kw:
            self._clear_search()
            return
        hits = self.wiki.search(kw, limit=60)
        self._rebuild_tree(hits)
        if hits:
            self._show(hits[0]["id"])
        else:
            self._set_detail(f"没有匹配「{kw}」的词条", "", "")

    def _clear_search(self):
        self.search_entry.delete(0, "end")
        self._rebuild_tree()

    # ---- 选择与详情 -------------------------------------------------
    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        eid = self._tree_rows.get(sel[0])
        if eid:
            self.selected = eid
            self._show(eid)

    def _show(self, eid):
        e = self.wiki.get(eid)
        if e is None:
            return
        self.selected = eid
        src = "手册" if e["source"] == "doc" else "自动生成"
        head = f"【{e['title']}】  {e['category']} · {src}"
        meta = ""
        if e["tags"]:
            meta = "标签: " + " ".join(t for t in e["tags"] if t)
        self._set_detail(head, meta, e["body"])

    # ---- 轻量排版：标题/元信息/反应式/项目符号 --------------------
    def _set_detail(self, title, meta, body):
        """渲染词条：标题加粗、元信息灰字、反应式独立缩进、要点缩进。"""
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", title + "\n", ("title",))
        if meta:
            self.detail.insert("end", meta + "\n", ("meta",))
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                self.detail.insert("end", "\n")
                continue
            # 反应式/化学方程式：含箭头（→ / --> / --条件-->）
            if "→" in stripped or "--" in stripped or "⇌" in stripped:
                self.detail.insert("end", "    " + stripped + "\n",
                                   ("reaction",))
            # 要点行（· / • / 数字序号）
            elif stripped[0] in "·•" or (
                    stripped[0].isdigit() and stripped[1:3] in (".)", ") ")):
                self.detail.insert("end", " " + stripped + "\n", ("bullet",))
            # 小节标题（以「关键节点：」这类冒号结尾的短行）
            elif stripped.endswith("：") and len(stripped) <= 14:
                self.detail.insert("end", stripped + "\n", ("section",))
            else:
                self.detail.insert("end", line + "\n")
        self.detail.configure(state="disabled")

    def show_entry(self, query):
        """外部跳转入口：接受词条 id / #id / 标题 / 关键词。"""
        if not query:
            return False
        key = query[1:] if query.startswith("#") else query
        e = self.wiki.get(key)
        if e is None:
            hits = self.wiki.search(key, limit=1)
            e = hits[0] if hits else None
        if e is None:
            return False
        # 回到分类视图并选中该词条所在节点
        self.search_entry.delete(0, "end")
        self._rebuild_tree()
        for iid, eid in self._tree_rows.items():
            if eid == e["id"]:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                # 展开所属分类
                parent = self.tree.parent(iid)
                if parent:
                    self.tree.item(parent, open=True)
                break
        self._show(e["id"])
        try:
            self.detail.see("1.0")
        except tk.TclError:
            pass
        return True

    def refresh(self, engine, router):
        pass    # 词条为静态数据（内容变更需重启加载）


class LayersPanel(_RightPanel):
    """圈层构成仪表：各圈已见地块构成 vs 配置目标，并显示勘探补偿强度。"""
    key = "layers"
    title = "圈层"

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              anchor="nw", font=T.FONT_MONO_SMALL,
                              fg=T.TEXT, bg=T.PANEL, wraplength=430)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        survey = engine.registry.get("survey")
        kind_names = {"empty": "空地", "ore": "矿脉", "wreck": "残骸",
                      "water": "水源"}
        from collections import defaultdict
        seen = defaultdict(lambda: defaultdict(int))
        for p in engine.world.visible_plots():
            seen[p.ring][p.kind] += 1
        if not seen:
            self.lines.configure(text="（还没有勘察地块，先 survey <圈层>）")
            return
        rows = ["各圈构成 vs 目标(配置权重)："]
        for ring in sorted(seen.keys()):
            total = sum(seen[ring].values())
            rows.append("")
            rows.append(f"圈{ring}  已见 {total} 块")
            kind_target = defaultdict(float)
            has_target = False
            if survey is not None:
                cfg = survey.ring_cfg(ring)
                if cfg:
                    has_target = True
                    pool = cfg.get("pools", [])
                    tw = sum(float(x.get("weight", 1)) for x in pool) or 1.0
                    for x in pool:
                        kind_target[x.get("kind")] += \
                            float(x.get("weight", 1)) / tw
            if not has_target:
                rows.append("  （出生地/非勘探圈，无配置目标）")
                continue
            for k in sorted(seen[ring].keys(),
                            key=lambda x: -seen[ring][x]):
                n = seen[ring][k]
                act = n / total if total else 0.0
                tgt = kind_target.get(k, 0.0)
                mark = ""
                if tgt > 0:
                    diff = act - tgt
                    if diff > 0.08:
                        mark = " ↑偏高"
                    elif diff < -0.08:
                        mark = " ↓偏低(勘探会补)"
                rows.append(f"  {kind_names.get(k, k):<4} {n:>2} "
                            f"{act * 100:>3.0f}%  目标{tgt * 100:>3.0f}%{mark}")
        if survey is not None:
            rows.append("")
            rows.append("勘探补偿(有效/基础权重，仅列偏离>5%)：")
            any_comp = False
            for ring in sorted(seen.keys()):
                cfg = survey.ring_cfg(ring)
                if not cfg:
                    continue
                try:
                    weighted = survey._adjusted_pool(engine, ring, cfg)
                except Exception:
                    continue
                parts = []
                for pool_entry, eff in weighted:
                    base = float(pool_entry.get("weight", 1))
                    if base <= 0:
                        continue
                    ratio = eff / base
                    if abs(ratio - 1.0) < 0.05:
                        continue
                    sub = pool_entry.get("substance")
                    label = (self.router.rname(sub) if sub
                             else kind_names.get(pool_entry.get("kind"), "?"))
                    parts.append(f"{label}×{ratio:.2f}")
                if parts:
                    any_comp = True
                    rows.append(f"  圈{ring}: " + " ".join(parts))
            if not any_comp:
                rows.append("  （当前各圈均接近目标配比）")
        self.lines.configure(text="\n".join(rows))


class PowerPanel(_RightPanel):
    """电力专页：产/耗/净 + 最大耗电者 + 可再生实时倍率(昼夜×事件)。"""
    key = "power"
    title = "电力"

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              anchor="nw", font=T.FONT_MONO, fg=T.TEXT,
                              bg=T.PANEL, wraplength=420)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        ind = engine.registry.get("industry")
        if ind is None:
            self.lines.configure(text="（无工业模块）")
            return
        try:
            pb = ind.power_balance(engine)
        except Exception:
            self.lines.configure(text="（电力数据不可用）")
            return
        net = pb["produce"] - pb["consume"]
        rows = [f"产电 {pb['produce']:.2f}/s | 耗电 {pb['consume']:.2f}/s",
                f"净值 {net:+.2f}/s " + ("(盈余)" if net >= 0 else "(缺电!)")]
        cons = sorted(pb.get("consumers", []), key=lambda x: -x[1])[:5]
        if cons:
            rows.append("")
            rows.append("最大耗电:")
            for n, v in cons:
                rows.append(f"  {n} {v:.2f}/s")
        prods = sorted(pb.get("producers", []), key=lambda x: -x[1])[:6]
        if prods:
            rows.append("")
            rows.append("发电机组:")
            for n, v in prods:
                rows.append(f"  {n} {v:.2f}/s")
        env = engine.registry.get("environment")
        dl = engine.registry.get("daylight")
        if env is not None:
            rows.append("")
            parts = []
            for key, label in (("solar", "光热"), ("pv", "光伏"),
                               ("wind", "风电")):
                mul = env.effect(key)
                if dl is not None and key in ("solar", "pv"):
                    mul *= dl.effect("solar")
                parts.append(f"{label}×{mul:.2f}")
            rows.append("可再生实时: " + " ".join(parts))
            cur = env.current()
            if cur is not None:
                rows.append(f"当前事件: {cur.get('name', '?')}")
        if dl is not None:
            rows.append(f"昼夜: {dl.phase_text()}"
                        f"（日照×{dl.effect('solar'):.2f}）")
        self.lines.configure(text="\n".join(rows))


class EnvironmentPanel(_RightPanel):
    """环境事件面板：当前环境、效果、持续时间。"""
    key = "environment"
    title = "环境"

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_shell(parent, engine, router)
        self.lines = tk.Label(self.body, text="", justify="left",
                              font=T.FONT_MONO, fg=T.TEXT, bg=T.PANEL,
                              wraplength=420)
        self.lines.pack(side="top", anchor="nw", padx=6, pady=4)

    def refresh(self, engine, router):
        env = engine.registry.get("environment")
        if env is None:
            self.lines.configure(text="（无环境系统）")
            return
        cur = env.current()
        if cur is None:
            self.lines.configure(text="（无数据）")
            return
        name = cur.get("name", "?")
        desc = cur.get("desc", "")
        # 剩余持续
        until = getattr(env, "_until", 0.0)
        remain = max(0.0, until - engine.clock.time)
        eff = cur.get("effects", {})
        prod = eff.get("production", 1.0)
        mem = eff.get("memory", 1.0)
        rows = [f"当前: {name}",
                f"剩余: {fmt_time(remain)}"]
        if prod != 1.0:
            rows.append(f"生产 ×{prod:g}")
        if mem != 1.0:
            rows.append(f"记忆劣化 ×{mem:g}")
        if desc:
            rows.append(f"描述: {desc}")
        self.lines.configure(text="\n".join(rows))


class ChainPanel(Panel):
    """生产链溯源：点选资源 → 看谁产出它、谁消耗它（配方图鉴）。"""
    key = "chain"
    title = "溯源"

    def unlock(self, engine):
        return True

    def build(self, parent, engine, router):
        self.engine = engine
        self.router = router
        self._build_index()
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap

        from tkinter import ttk
        # 左：资源列表
        left = tk.Frame(wrap, bg=T.BG)
        left.pack(side="left", fill="both", expand=True)
        tk.Label(left, text="资源/物质", font=T.FONT_SMALL,
                 fg=T.TEXT_SUB, bg=T.BG).pack(side="top", anchor="w",
                                              padx=4, pady=2)
        self.res_list = tk.Listbox(left, font=T.FONT_SMALL, bg=T.PANEL,
                                   fg=T.TEXT, selectbackground=T.ACCENT,
                                   selectforeground=T.WARN_FG, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground=T.BORDER,
                                   activestyle="none", exportselection=False)
        self.res_list.pack(side="left", fill="both", expand=True, padx=(2, 0))
        tsb = tk.Scrollbar(left, orient="vertical",
                           command=self.res_list.yview)
        tsb.pack(side="right", fill="y")
        self.res_list.configure(yscrollcommand=tsb.set)
        self.res_list.bind("<<ListboxSelect>>", self._on_pick)
        # 双击资源 → 打开百科该物质词条
        self.res_list.bind("<Double-Button-1>", self._on_double)
        from ui.panels.base import bind_mousewheel
        bind_mousewheel(self.res_list)
        # 填资源（按中文名排序）
        for rid in sorted(self._index.keys(),
                          key=lambda r: self.router.rname(r)):
            self.res_list.insert("end", f"{self.router.rname(rid)}  ({rid})")
        self._rids = [r for r in sorted(
            self._index.keys(), key=lambda r: self.router.rname(r))]

        # 右：溯源详情（可滚动只读文本）
        detail_frame = tk.Frame(wrap, bg=T.PANEL)
        detail_frame.pack(side="right", fill="both", padx=4, pady=2)
        self.detail = tk.Text(
            detail_frame,
            font=T.FONT_MONO_SMALL,
            bg=T.PANEL, fg=T.TEXT,
            relief="flat", state="disabled",
            wrap="word", padx=8, pady=6,
            highlightthickness=1, highlightbackground=T.BORDER,
            width=46)
        self.detail.pack(side="left", fill="both", expand=True)
        dtsb = tk.Scrollbar(detail_frame, orient="vertical",
                            command=self.detail.yview)
        dtsb.pack(side="right", fill="y")
        self.detail.configure(yscrollcommand=dtsb.set)
        from ui.panels.base import bind_mousewheel
        bind_mousewheel(self.detail)
        self._detail_text = "← 点选资源查看生产链"
        self._render_detail_text()

    # ---- 详情文本写入（Text 只读封装）----------------------------
    def _render_detail_text(self):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", self._detail_text)
        self.detail.configure(state="disabled")

    # ---- 静态索引：物质 → 生产者/消费者 -------------------------
    def _build_index(self):
        """遍历设施定义+配方，构建 rid → {make:[], use:[]}。"""
        ind = self.engine.registry.get("industry")
        index = {}
        if ind is None:
            self._index = index
            return
        def note(rid, role, fac_id, recipe_id, name):
            entry = index.setdefault(rid, {"make": [], "use": []})
            entry[role].append((fac_id, recipe_id, name))
        for fid, d in ind.defs.items():
            name = d.get("name", fid)
            if d.get("extract_rate"):
                # 提取设施：产出物为 plot 物质（无法静态枚举）→ 跳过
                continue
            r = ind.recipes.get(d.get("recipe", ""))
            if r is None:
                continue
            for rid in r.get("inputs", {}):
                note(rid, "use", fid, r["id"], name)
            for rid in r.get("outputs", {}):
                note(rid, "make", fid, r["id"], name)
            for rid in r.get("byproducts", {}):
                note(rid, "make", fid, r["id"], name)
        self._index = index

    def _on_pick(self, _evt=None):
        sel = self.res_list.curselection()
        if not sel:
            return
        rid = self._rids[sel[0]]
        info = self._index.get(rid, {"make": [], "use": []})
        lines = [f"{self.router.rname(rid)}  ({rid})", ""]
        if info["make"]:
            lines.append("谁产出:")
            for fid, rpid, nm in info["make"]:
                lines.append(f"  {nm} ({fid}) 配方 {rpid}")
        else:
            lines.append("谁产出: （矿藏/起始物或不可生产）")
        if info["use"]:
            lines.append("")
            lines.append("谁消耗:")
            for fid, rpid, nm in info["use"]:
                lines.append(f"  {nm} ({fid}) 配方 {rpid}")
        if not info["make"] and not info["use"]:
            lines.append("（未用于任何已知配方）")
        self._detail_text = "\n".join(lines)
        self._render_detail_text()

    def _on_double(self, _evt=None):
        """双击资源 → 打开百科该物质词条。"""
        sel = self.res_list.curselection()
        cb = getattr(self, "open_wiki", None)
        if sel and cb is not None:
            cb(f"sub:{self._rids[sel[0]]}")

    def refresh(self, engine, router):
        pass    # 静态索引，无动态刷新
