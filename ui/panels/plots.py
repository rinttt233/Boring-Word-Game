"""地块树面板 PlotsPanel（P1 拆分）。"""
from typing import List, Optional

import tkinter as tk

from ui import theme as T
from ui.commands import fmt_time
from ui.panels.base import Panel, style_tree


class PlotsPanel(Panel):
    """左区主面板：地块列表 + 上下文按钮 + 两级建造流。"""
    key = "plots"
    title = "地块"
    SELECTED = "selected"

    def __init__(self) -> None:
        self.selected: Optional[str] = None

    def build(self, parent, engine, router) -> None:
        self.engine = engine
        self.router = router
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap

        # 过滤行：只看某类地块（找地皮/找矿快）
        self._kind_filter = "all"
        fbar = tk.Frame(wrap, bg=T.BG)
        fbar.pack(side="top", fill="x", padx=2, pady=(2, 0))
        tk.Label(fbar, text="筛选:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.BG).pack(side="left", padx=(2, 4))
        self._filter_btns = {}
        for key, label in (("all", "全部"), ("empty", "空地"),
                           ("ore", "矿脉"), ("wreck", "残骸"),
                           ("water", "水源")):
            b = tk.Label(fbar, text=label, font=T.FONT_SMALL, fg=T.TEXT,
                         bg=T.PANEL, padx=6, pady=1, cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, k=key: self.set_kind_filter(k))
            self._filter_btns[key] = b
        self._style_filter_btns()

        # 地块树：圈层 → 类型 → 地块（三级可折叠，ttk.Treeview）
        from tkinter import ttk
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.tree.pack(side="top", fill="both", expand=True)
        # 样式：黑白灰（含选中黑底白字）
        style_tree(self.tree, "Plots.Treeview")
        # 高亮 tag：新勘探地块（反白黑底白字）
        self.tree.tag_configure("new", background=T.ACCENT,
                                foreground=T.WARN_FG)
        self.tree.tag_configure("plot", foreground=T.TEXT)
        self.tree.tag_configure("lock", foreground=T.TEXT_DIM)
        # 滚动条
        tsb = tk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # 双击地块 → 若为矿脉/残骸，打开百科该物质词条
        self.tree.bind("<Double-Button-1>", self._on_double)
        # 跟踪用户折叠/展开，记录到 _open_by_key（供重建时恢复）
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_close)

        # 地块详情区（选中后显示完整信息，位于树下方）
        self.detail = tk.Frame(wrap, bg=T.PANEL)
        self.detail.pack(side="bottom", fill="x", pady=(4, 0))
        self.detail_label = tk.Label(self.detail, text="点击地块查看详情",
                                     justify="left", anchor="w",
                                     font=T.FONT_SMALL, fg=T.TEXT_SUB,
                                     bg=T.PANEL, wraplength=520)
        self.detail_label.pack(side="top", fill="x", padx=6, pady=4)

        # 操作行
        self.actions = tk.Frame(wrap, bg=T.PANEL)
        self.actions.pack(side="bottom", fill="x", pady=(2, 0))
        self._render_actions()

        # 新勘探标记记忆
        self._new_plots: set = set()
        self._tree_rows: dict = {}   # item_id -> (kind, plot_id)
        self._open_by_key = {}
        self.refresh(engine, router)

    # ---- 类型筛选 --------------------------------------------------
    def set_kind_filter(self, key: str) -> None:
        self._kind_filter = key
        self._style_filter_btns()
        self._refresh_sig_prev = None    # 强制重建
        self._rebuild_tree(preserve_open=False)

    def _style_filter_btns(self) -> None:
        for key, b in getattr(self, "_filter_btns", {}).items():
            if key == self._kind_filter:
                b.configure(bg=T.ACCENT, fg=T.WARN_FG)
            else:
                b.configure(bg=T.PANEL, fg=T.TEXT)

    def attach_prefs(self, prefs):
        """绑定 GUI 偏好：记忆树折叠状态（圈层/类型节点）。"""
        self._prefs = prefs
        saved = prefs.get("tree.plots.open")
        if isinstance(saved, dict):
            self._open_by_key.update(saved)
            # 折叠状态会影响下次重建；立即重建以应用
            self._rebuild_tree(preserve_open=True)
        prefs.add_hooks(write_fn=self._write_prefs)

    def _write_prefs(self, prefs):
        if getattr(self, "_open_by_key", None):
            prefs.set("tree.plots.open", dict(self._open_by_key))

    # ---- 树内容构建（圈层→类型→地块）---------------------------
    def _build_tree_lines(self):
        """把可见地块组织为 圈层→类型 树，返回 (ring, kind, plot) 分组。

        kind_filter（'all' 或某 kind）用于"只看空地/矿脉"等快速定位。
        """
        from collections import defaultdict
        engine = self.engine
        flt = getattr(self, "_kind_filter", "all")
        groups = defaultdict(lambda: defaultdict(list))
        for p in engine.world.visible_plots():
            if flt != "all" and p.kind != flt:
                continue
            groups[p.ring][p.kind].append(p)
        return groups

    def _row_text(self, p) -> str:
        """地块行内摘要：id + 状态 + 物质/估值（一眼可辨，减少点击）。"""
        state_names = {"known": "勘察", "claimed": "占领",
                       "developed": "开发", "depleted": "枯竭"}
        parts = [f"{p.id}",
                 f"[{state_names.get(p.state, p.state)}]"]
        if p.substance:
            name = self.router.rname(p.substance)
            if p.kind in ("ore", "wreck"):
                if p.surveyed and p.known_grade is not None:
                    parts.append(f"{name}~{p.known_grade:g}%")
                else:
                    parts.append(name)
            else:
                parts.append(name)
        return " ".join(parts)

    def _rebuild_tree(self, preserve_open: bool = True) -> None:
        # 记录展开状态：用稳定 key（ring-x / kind-x-y），重建后按 key 恢复
        open_map = {}
        if preserve_open and hasattr(self, "_open_by_key"):
            open_map = dict(self._open_by_key)

        self.tree.delete(*self.tree.get_children())
        self._tree_rows = {}
        self._open_by_key = {}
        groups = self._build_tree_lines()
        kind_names = {"empty": "空地", "water": "水源", "ore": "矿脉",
                      "wreck": "残骸"}
        new = self._new_plots

        for ring in sorted(groups.keys()):
            ring_key = f"ring:{ring}"
            ring_open = open_map.get(ring_key, True)
            ring_id = self.tree.insert(
                "", "end", iid=ring_key, text=f"圈{ring}",
                open=ring_open, tags=("plot",))
            self._open_by_key[ring_key] = ring_open
            for kind in sorted(groups[ring].keys()):
                kind_key = f"{ring_key}:{kind}"
                kind_open = open_map.get(kind_key, True)
                kind_id = self.tree.insert(
                    ring_id, "end", iid=kind_key,
                    text=f"{kind_names.get(kind, kind)} "
                         f"({len(groups[ring][kind])})",
                    open=kind_open, tags=("plot",))
                self._open_by_key[kind_key] = kind_open
                for p in sorted(groups[ring][kind], key=lambda x: x.id):
                    pid = p.id
                    tag = "new" if pid in new else "plot"
                    iid = self.tree.insert(
                        kind_id, "end", text=self._row_text(p),
                        tags=(tag,), values=(pid,))
                    self._tree_rows[iid] = (kind, pid)
        # 应用渐变闪烁
        if new:
            self._start_flash()

    def _on_tree_open(self, _evt):
        self._record_open_state()

    def _on_tree_close(self, _evt):
        self._record_open_state()

    def _record_open_state(self):
        """把当前树内所有节点的展开状态写回 _open_by_key。"""
        def walk(parent):
            for iid in self.tree.get_children(parent):
                open_ = self.tree.item(iid, "open")
                self._open_by_key[iid] = bool(open_)
                walk(iid)
        walk("")

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        meta = self._tree_rows.get(iid)
        if meta is None:
            return
        kind, pid = meta
        self.selected = pid
        self._render_detail()
        self._render_actions()

    def _on_double(self, _evt=None):
        """双击地块 → 打开百科该物质词条（矿脉/残骸/水源）。"""
        sel = self.tree.selection()
        if not sel:
            return
        meta = self._tree_rows.get(sel[0])
        cb = getattr(self, "open_wiki", None)
        if meta is None or cb is None:
            return
        _kind, pid = meta
        p = self.engine.world.get(pid)
        if p is None or not p.substance:
            return
        cb(f"sub:{p.substance}")

    def select_plot(self, plot_id: str) -> bool:
        """按地块 id 在树中定位并选中（供其他面板跳转）。"""
        for iid, (kind, pid) in self._tree_rows.items():
            if pid == plot_id:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.selected = plot_id
                self._render_detail()
                self._render_actions()
                return True
        return False

    # ---- 渐变闪烁（新勘探地块） --------------------------------
    def _start_flash(self):
        """为新地块 tag 启动渐变闪烁：黑底白字 ↔ 亮灰底黑字 交替。"""
        if getattr(self, "_flash_job", None) is not None:
            return
        self._flash_phase = 0
        palette = [(T.ACCENT, T.WARN_FG), ("#555555", "#FFFFFF")]
        self._flash_palette = palette

        def tick():
            if not self._new_plots:
                self._stop_flash()
                return
            self._flash_phase = 1 - self._flash_phase
            bg, fg = self._flash_palette[self._flash_phase]
            self.tree.tag_configure("new", background=bg, foreground=fg)
            self._flash_job = self.tree.after(300, tick)
        self._flash_job = self.tree.after(0, tick)

    def _stop_flash(self):
        if getattr(self, "_flash_job", None) is not None:
            try:
                self.tree.after_cancel(self._flash_job)
            except tk.TclError:
                pass
            self._flash_job = None
        # 清除所有 new tag，恢复普通样式
        self.tree.tag_configure("new", background=T.ACCENT,
                                foreground=T.WARN_FG)

    def _render_detail(self):
        engine = self.engine
        pid = self.selected
        if pid is None:
            self.detail_label.configure(text="点击地块查看详情",
                                        fg=T.TEXT_SUB)
            return
        p = engine.world.get(pid)
        if p is None:
            return
        state_names = {"known": "已勘察", "claimed": "已占领",
                       "developed": "已开发", "depleted": "已枯竭"}
        kind_names = {"empty": "空地", "water": "水源", "ore": "矿脉",
                      "wreck": "残骸"}
        # 多行信息（自动换行由 wraplength 处理）
        lines = [f"{p.id} · 圈{p.ring} · {kind_names.get(p.kind, p.kind)}",
                 f"状态: {state_names.get(p.state, p.state)}"]
        if p.substance:
            if p.kind == "ore" or p.kind == "wreck":
                # 显示勘探估值（带误差范围），开采会逐步逼近真实值
                g = p.known_grade if p.surveyed and p.known_grade is not None else None
                r = p.known_reserve if p.surveyed and p.known_reserve is not None else None
                text = ""
                if g is not None:
                    text = f"~{g:g}% (±{p.grade_err * 100:.0f}%) "
                if r is not None:
                    text += f"储量~{r:,.0f}{self.router.runit(p.substance)}"
                if not text:
                    text = "未知"
                lines.append(f"{self.router.rname(p.substance)}: {text}"
                             + (" (估值)" if p.surveyed else ""))
            else:
                lines.append(f"{self.router.rname(p.substance)}: "
                             f"品位 {p.grade:g}% | "
                             f"储量 {p.reserve:g}{self.router.runit(p.substance)}")
        lines.append(f"物流惩罚 ×{p.logistics_multiplier():g}")
        ind = engine.registry.get("industry")
        if ind:
            facs = [f for f in ind.facilities.values() if f.plot_id == p.id]
            if facs:
                lines.append("设施: " +
                             ", ".join(f"{f.name}({f.id})" for f in facs))
        self.detail_label.configure(text="\n".join(lines), fg=T.TEXT)
        # 自动换行：wraplength 随容器宽度
        self.detail_label.after_idle(self._fit_wrap)

    def _fit_wrap(self):
        try:
            w = self.detail_label.winfo_width()
            if w > 40:
                self.detail_label.configure(wraplength=w - 12)
        except tk.TclError:
            pass

    def _clear_actions(self):
        for w in self.actions.winfo_children():
            w.destroy()
        # 动作行重建后，旧的展开容器已销毁 → 重置引用
        for attr in ("_expand_scroll", "_expand", "_build_detail",
                     "_build_btn"):
            self.__dict__.pop(attr, None)

    def _make_btn(self, text, cmd, enabled=True, hint="", style="normal"):
        if not enabled:
            b = tk.Label(self.actions,
                         text=text + (f" {T.S_LOCK}" if hint == "lock" else ""),
                         font=T.FONT_UI, fg=T.TEXT_DIM, bg=T.PANEL_ALT,
                         padx=6, pady=2, cursor="X_cursor")
            return b
        b = tk.Label(self.actions, text=text, font=T.FONT_UI, fg=T.TEXT,
                     bg=T.PANEL, padx=6, pady=2, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def _render_actions(self) -> None:
        self._clear_actions()
        engine = self.engine
        pid = self.selected
        # 确保建造展开容器始终存在（懒初始化）
        if getattr(self, "_expand", None) is None:
            self._build_expand_row()
        if pid is None:
            tk.Label(self.actions, text="选中地块以操作",
                     font=T.FONT_SMALL, fg=T.TEXT_DIM,
                     bg=T.PANEL).pack(side="left", padx=4, pady=2)
            return
        plot = engine.world.get(pid)
        if plot is None:
            return
        ind = engine.registry.get("industry")
        # 依据地块状态生成上下文按钮
        if plot.state == "known":
            self._make_btn("占领", lambda: self.router.execute(f"claim {pid}")) \
                .pack(side="left", padx=2, pady=2)
        elif plot.state in ("claimed", "developed"):
            self._make_btn("建造 ▾", lambda: self._toggle_build_expand()) \
                .pack(side="left", padx=2, pady=2)
            # 分配单元
            owned = [f for f in ind.facilities.values()]
            has_fac = any(f.plot_id == pid for f in owned)
            facs_here = [f for f in owned if f.plot_id == pid]
            if has_fac:
                self._make_btn("分配", lambda: self._assign_pick(pid)) \
                    .pack(side="left", padx=2, pady=2)
                self._make_btn("调离", lambda: self._unassign(pid)) \
                    .pack(side="left", padx=2, pady=2)
        self._make_btn("详情", lambda: self.router.execute(f"plots")) \
            .pack(side="left", padx=2, pady=2)
        # 展开建造按钮排（作为 actions 下方一个子行）
        self._build_expand_row()

    def _build_expand_row(self) -> None:
        """建造展开区：可滚动列表 + 点击选中后在下方显示详情 + 建造按钮。"""
        # 可滚动建造列表
        scroll = tk.Frame(self.actions, bg=T.PANEL)
        scroll.pack(side="top", fill="x", padx=2, pady=2)
        self._expand_scroll = scroll
        self._expand = tk.Listbox(
            scroll, font=T.FONT_SMALL, bg=T.PANEL, fg=T.TEXT,
            selectbackground=T.ACCENT, selectforeground=T.WARN_FG,
            relief="flat", height=8, highlightthickness=1,
            highlightbackground=T.BORDER, activestyle="none",
            exportselection=False)
        self._expand.pack(side="left", fill="x", expand=True)
        escroll = tk.Scrollbar(scroll, orient="vertical",
                               command=self._expand.yview)
        escroll.pack(side="right", fill="y")
        self._expand.configure(yscrollcommand=escroll.set)
        self._expand.bind("<<ListboxSelect>>", self._on_build_select)
        from ui.panels.base import bind_mousewheel
        bind_mousewheel(self._expand)

        # 建造详情区（选中建筑后显示）
        self._build_detail = tk.Label(self.actions, text="",
                                      justify="left", anchor="w",
                                      font=T.FONT_SMALL, fg=T.TEXT_SUB,
                                      bg=T.PANEL, wraplength=560)
        self._build_detail.pack(side="top", fill="x", padx=2, pady=2)
        # 建造按钮（默认隐藏，选中可建项后出现）
        self._build_btn = tk.Label(self.actions, text="▶ 建造",
                                   font=T.FONT_UI_BOLD, fg=T.WARN_FG,
                                   bg=T.ACCENT, padx=10, pady=2,
                                   cursor="hand2")
        self._build_btn.bind("<Button-1>", lambda e: self._confirm_build())
        # 折叠（默认整体隐藏）
        for w in (self._expand_scroll, self._build_detail,
                  self._build_btn):
            w.pack_forget()
        self._expand._entries = []

    def _toggle_build_expand(self) -> None:
        if self._expand_scroll.winfo_ismapped():
            self._collapse_build()
            return
        self._populate_build_expand()
        self._expand_scroll.pack(side="top", fill="x", padx=2, pady=2)

    def _collapse_build(self):
        for w in (self._expand_scroll, self._build_detail,
                  self._build_btn):
            w.pack_forget()

    def _populate_build_expand(self) -> None:
        self._expand.delete(0, "end")
        self._expand._entries = []   # 存 (fac_id, enabled, def)
        pid = self.selected
        if not pid:
            return
        plot = self.engine.world.get(pid)
        ind = self.engine.registry.get("industry")
        rec = self.engine.registry.get("recovery")
        for d in ind.defs.values():
            if plot.kind not in d.get("allowed_plot_kinds", []):
                continue
            need = d.get("requires_recovery")
            locked = need and (rec is None or not rec.is_unlocked(need))
            label = f"{d['name']}" + (f" {T.S_LOCK}" if locked else "")
            self._expand.insert("end", label)
            self._expand._entries.append((d["id"], not locked, d))
        for i, (_fid, enabled, _d) in enumerate(self._expand._entries):
            self._expand.itemconfigure(
                i, fg=T.TEXT if enabled else T.TEXT_DIM,
                bg=T.PANEL if enabled else T.PANEL_ALT)
        # 高度随可选项数自适应（方便选择，最多 12 行后滚动）
        n = len(self._expand._entries)
        self._expand.configure(height=max(4, min(12, n)))

    def _on_build_select(self, _evt=None):
        sel = self._expand.curselection()
        if not sel:
            return
        i = sel[0]
        fid, enabled, d = self._expand._entries[i]
        ind = self.engine.registry.get("industry")
        need = d.get("requires_recovery")
        # 详情文本
        parts = []
        if need:
            rec = self.engine.registry.get("recovery")
            e = rec.entries.get(need) if rec else None
            parts.append(f"{T.S_LOCK} 需恢复: {e['name'] if e else need}")
        bt = d.get("build_time")
        if bt:
            parts.append(f"建造耗时 {bt:g}s")
        cost = d.get("build_cost", {})
        if cost:
            ct = ", ".join(f"{self.router.rname(k)} {v:g}{self.router.runit(k)}"
                           for k, v in cost.items())
            parts.append(f"材料: {ct}")
        else:
            parts.append("材料: 无")
        # 生产速率
        if d.get("extract_rate"):
            parts.append(f"产出 {d['extract_rate']:g}/s "
                         f"(耗电 {d.get('power_use', 0):g}/s)")
        elif d.get("recipe"):
            r = ind.recipes.get(d["recipe"], {})
            outs = ", ".join(f"{self.router.rname(k)} {v:g}/s"
                             for k, v in r.get("outputs", {}).items())
            ins = ", ".join(f"{self.router.rname(k)} {v:g}/s"
                            for k, v in r.get("inputs", {}).items())
            parts.append(f"消耗: {ins}")
            parts.append(f"产出: {outs}")
        self._build_detail.configure(text="\n".join(parts),
                                     fg=T.TEXT if enabled else T.TEXT_DIM)
        self._build_detail.pack(side="top", fill="x", padx=2, pady=2)
        # 建造按钮：仅可建项显示
        if enabled:
            self._build_btn.pack(side="top", anchor="w", padx=2, pady=2)
        else:
            self._build_btn.pack_forget()

    def _confirm_build(self):
        sel = self._expand.curselection()
        if not sel:
            return
        fid, enabled, _d = self._expand._entries[sel[0]]
        if not enabled:
            self.engine.log("[工业] 该设施尚未通过数据库恢复解锁。")
            return
        self.router.execute(f"build {self.selected} {fid}")
        self._collapse_build()

    def _assign_pick(self, pid):
        ind = self.engine.registry.get("industry")
        facs_here = [f for f in ind.facilities.values() if f.plot_id == pid]
        if not facs_here:
            self.engine.log("[工业] 该地块无设施可分配。")
            return
        self.router.execute(f"assign {facs_here[0].id}")

    def _unassign(self, pid):
        ind = self.engine.registry.get("industry")
        facs_here = [f for f in ind.facilities.values() if f.plot_id == pid]
        if facs_here:
            self.router.execute(f"unassign {facs_here[0].id}")

    def refresh(self, engine, router) -> None:
        # 检测新勘探地块：新出现在 visible_plots 里的标记为新，记录发现时间
        visible_ids = {p.id for p in engine.world.visible_plots()}
        # 地块集合/状态摘要：只有真正变化时才重建树（避免频繁重建导致折叠被重置）
        state_sig = tuple(sorted(
            (p.id, p.state, p.kind, p.ring) for p in engine.world.visible_plots()))
        old_sig = getattr(self, "_refresh_sig_prev", None)

        known_old = set(getattr(self, "_known_plot_ids", set()))
        if not hasattr(self, "_new_until"):
            self._new_until = {}
        newly = visible_ids - known_old
        now = engine.clock.time
        for pid in newly:
            self._new_until[pid] = now + 10.0   # 高亮持 10s
        self._known_plot_ids = visible_ids
        expired = [pid for pid, t in self._new_until.items() if now > t]
        for pid in expired:
            self._new_until.pop(pid, None)
        self._new_plots = set(self._new_until.keys())

        # 仅当地块集合/状态实体变化时才重建树
        if state_sig != old_sig:
            self._refresh_sig_prev = state_sig
            self._rebuild_tree()
        # 详情区与操作行随选中状态更新
        if self.selected is not None:
            self._render_detail()
        # 新地块全部过期后停止闪烁
        if not self._new_plots:
            self._stop_flash()
