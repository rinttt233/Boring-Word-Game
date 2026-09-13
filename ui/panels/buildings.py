"""建筑管理面板 BuildingsPanel（P1 拆分）。"""
from typing import Optional

import tkinter as tk

from ui import theme as T
from ui.panels.base import Panel, Refreshable, style_tree


class BuildingsPanel(Refreshable, Panel):
    """统一管理各类建筑：按类型分组展示所有设施，支持分配/调离。"""
    key = "buildings"
    title = "建筑"
    locate = None       # 由 GUI 注入：定位到所属地块的跳转回调

    def __init__(self) -> None:
        self.selected: Optional[str] = None

    def build(self, parent, engine, router) -> None:
        self.engine = engine
        self.router = router
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap

        # 工具栏：分组维度 / 只看停摆 / 一键补员 / 撤销建造
        self._only_stalled = False
        self._group_mode = "type"      # type | plot | ring | status
        tbar = tk.Frame(wrap, bg=T.BG)
        tbar.pack(side="top", fill="x", padx=2, pady=(2, 0))
        tk.Label(tbar, text="分组:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.BG).pack(side="left", padx=(2, 3))
        self._group_btns = {}
        for key, label in (("type", "类型"), ("plot", "地块"),
                           ("ring", "圈层"), ("status", "状态")):
            b = tk.Label(tbar, text=label, font=T.FONT_SMALL, fg=T.TEXT,
                         bg=T.PANEL, padx=5, pady=1, cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, k=key: self.set_group_mode(k))
            self._group_btns[key] = b
        self._style_group_btns()
        self._stall_btn = tk.Label(tbar, text="只看停摆", font=T.FONT_SMALL,
                                   fg=T.TEXT, bg=T.PANEL, padx=6, pady=1,
                                   cursor="hand2")
        self._stall_btn.pack(side="left", padx=(8, 1))
        self._stall_btn.bind("<Button-1>", lambda e: self.toggle_only_stalled())
        self._staff_btn = tk.Label(tbar, text="一键补员", font=T.FONT_SMALL,
                                   fg=T.TEXT, bg=T.PANEL, padx=6, pady=1,
                                   cursor="hand2")
        self._staff_btn.pack(side="left", padx=1)
        self._staff_btn.bind("<Button-1>", lambda e: self._auto_staff())
        self._undo_btn = tk.Label(tbar, text="撤销建造", font=T.FONT_SMALL,
                                  fg=T.TEXT, bg=T.PANEL, padx=6, pady=1,
                                  cursor="hand2")
        self._undo_btn.pack(side="left", padx=1)
        self._undo_btn.bind("<Button-1>", lambda e: self._undo_build())

        from tkinter import ttk
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        self.tree.pack(side="top", fill="both", expand=True)
        style_tree(self.tree, "Build.Treeview")
        self.tree.tag_configure("group", foreground=T.TEXT_SUB)
        self.tree.tag_configure("fac", foreground=T.TEXT)
        tsb = tk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)
        # 行内信息变长（在干什么/状态）→ 补横向滚动，避免内容被裁掉
        hsb = tk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        hsb.pack(side="bottom", fill="x")
        self.tree.configure(xscrollcommand=hsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # 双击设施 → 打开百科对应词条
        self.tree.bind("<Double-Button-1>", self._on_double)
        # 跟踪用户折叠/展开（与地块栏一致，重建时恢复）
        self.tree.bind("<<TreeviewOpen>>", self._record_open_state)
        self.tree.bind("<<TreeviewClose>>", self._record_open_state)

        # 详情 + 操作
        self.detail = tk.Frame(wrap, bg=T.PANEL)
        self.detail.pack(side="bottom", fill="x", pady=(4, 0))
        self.detail_label = tk.Label(self.detail, text="点击设施查看详情",
                                     justify="left", anchor="w",
                                     font=T.FONT_SMALL, fg=T.TEXT_SUB,
                                     bg=T.PANEL, wraplength=520)
        self.detail_label.pack(side="top", fill="x", padx=6, pady=4)
        self._fac_map = {}   # iid -> fac_id
        self._open_by_key = {}
        self.refresh(engine, router)

    # ---- 分组维度 / 停摆过滤 / 一键补员 -----------------------------
    def set_group_mode(self, mode):
        self._group_mode = mode
        self._style_group_btns()
        self._refresh_sig_prev = None
        self._rebuild()

    def _style_group_btns(self):
        for key, b in getattr(self, "_group_btns", {}).items():
            if key == self._group_mode:
                b.configure(bg=T.ACCENT, fg=T.WARN_FG)
            else:
                b.configure(bg=T.PANEL, fg=T.TEXT)

    # ---- 行内摘要："这台设施在干什么" ---------------------------------
    @staticmethod
    def _amt(v: float) -> str:
        """储量紧凑写法：12345 → 12k。"""
        if v >= 10000:
            return f"{v / 1000:.0f}k"
        if v >= 1000:
            return f"{v / 1000:.1f}k"
        return f"{v:g}"

    def _activity_text(self, f, d, ind) -> str:
        """采矿机 →煤 82k ｜ 焦炉 →焦炭 ｜ 发电机 →电 1.2/s 烧煤 ｜ 光伏 →电 2.0/s 光伏。"""
        if d.get("extract_rate"):
            plot = self.engine.world.get(f.plot_id)
            if plot is None:
                return ""
            if plot.kind == "water":
                return "→水 ∞"
            sub = plot.substance or "scrap_alloy"
            if plot.reserve <= 0:
                return f"→{self.router.rname(sub)} 枯竭"
            return f"→{self.router.rname(sub)} {self._amt(plot.reserve)}"
        if d.get("kind") == "burner":
            hv = float(ind.heat_values.get(f.fuel or "", 0.0) or 0.0)
            if hv <= 0 or not f.fuel:
                return "→电 未设燃料"
            out = (float(d.get("burn_rate", 0.5)) * hv
                   * float(d.get("burn_efficiency", 0.4)))
            return f"→电 {out:.1f}/s 烧{self.router.rname(f.fuel)}"
        if d.get("kind") == "renewable":
            pk = {"solar": "光热", "pv": "光伏",
                  "wind": "风电"}.get(d.get("power_kind"), "可再生")
            return f"→电 {float(d.get('capacity', 0.0)):.1f}/s {pk}"
        r = ind.recipes.get(d.get("recipe", ""))
        outs = (r or {}).get("outputs", {})
        if outs:
            sub, rate = max(outs.items(), key=lambda kv: float(kv[1]))
            if sub == "electricity":
                return f"→电 {float(rate):.1f}/s"
            return f"→{self.router.rname(sub)}"
        return ""

    def _group_of(self, ind, f):
        """返回 (分组键, 显示名)——由当前分组维度决定。"""
        mode = getattr(self, "_group_mode", "type")
        plot = self.engine.world.get(f.plot_id)
        if mode == "plot":
            ring = plot.ring if plot is not None else "?"
            return f.plot_id, f"{f.plot_id} 圈{ring}"
        if mode == "ring":
            ring = plot.ring if plot is not None else "?"
            return f"ring{ring}", f"圈{ring}"
        if mode == "status":
            if f.under_construction:
                return "building", f"{T.S_BUILD} 建造中"
            if f.stalled_reported:
                return "stalled", f"{T.S_WARN} 停摆"
            if not f.assigned:
                return "idle", "待分配单元"
            return "running", "运转中"
        did = ind.defs[f.def_id]["id"]
        name = self._group_name(did)
        return name, name

    def toggle_only_stalled(self):
        self._only_stalled = not self._only_stalled
        self._stall_btn.configure(
            bg=T.ACCENT if self._only_stalled else T.PANEL,
            fg=T.WARN_FG if self._only_stalled else T.TEXT)
        self._refresh_sig_prev = None
        self._rebuild()

    def _undo_build(self):
        """撤销最近一次建造（尚未产出的设施）。"""
        ind = self.engine.registry.get("industry")
        if ind is None:
            return
        err = ind.undo_build(self.engine)
        if err:
            self.engine.log(f"[工业] {err}")
        self._refresh_sig_prev = None
        self._rebuild()

    def _auto_staff(self):
        """把空闲执行单元优先分配给"已建但无单元"的设施（批量补员）。"""
        ind = self.engine.registry.get("industry")
        if ind is None:
            return
        idle = self.engine.units.count_idle()
        if idle <= 0:
            self.engine.log("[建筑] 没有空闲执行单元可补员。")
            return
        targets = [f for f in ind.facilities.values()
                   if not f.assigned and not f.under_construction]
        if not targets:
            self.engine.log("[建筑] 所有设施都已分配单元。")
            return
        done = 0
        for f in targets:
            if self.engine.units.count_idle() <= 0:
                break
            if ind.assign(self.engine, f.id) is None:
                done += 1
        self.engine.log(f"[建筑] 一键补员：{done} 座设施获得执行单元"
                        f"（剩余空闲 {self.engine.units.count_idle()}）。")
        self._refresh_sig_prev = None
        self._rebuild()

    def attach_prefs(self, prefs):
        """绑定 GUI 偏好：记忆建筑树折叠状态（分组节点）。"""
        self._prefs = prefs
        saved = prefs.get("tree.buildings.open")
        if isinstance(saved, dict):
            self._open_by_key.update(saved)
            self._rebuild()
        prefs.add_hooks(write_fn=self._write_prefs)

    def _write_prefs(self, prefs):
        if getattr(self, "_open_by_key", None):
            prefs.set("tree.buildings.open", dict(self._open_by_key))

    def _group_name(self, def_id):
        names = {
            "power_plant": "发电", "gas_generator": "发电",
            "coal_stove": "发电", "large_coal_plant": "发电",
            "fluid_power_plant": "发电", "csp_plant": "发电",
            "solar_farm": "发电", "wind_farm": "发电",
            "extractor": "采集", "salvager": "回收",
            "cokery": "化工", "sulfuric_plant": "化工",
            "ammonia_synth": "化工", "ammonia_v2_plant": "化工",
            "chloralkali_cell": "化工", "nitric_plant": "化工",
            "petrol_cracker": "化工", "distill_tower": "化工",
            "polymer_plant": "化工", "tar_tower": "化工",
            "blast_furnace": "冶金", "steel_mill": "冶金",
            "copper_refiner": "冶金", "aluminum_smelter": "冶金",
            "nickel_arc_furnace": "冶金", "chrome_arc_furnace": "冶金",
            "tungsten_arc_furnace": "冶金",
            "stainless_plant": "冶金", "superalloy_plant": "冶金",
            "nickel_concentrator": "选矿", "chrome_concentrator": "选矿",
            "tungsten_concentrator": "选矿",
            "rare_mill": "选矿", "solvent_extractor": "化工",
            "catalyst_v_line": "化工", "catalyst_cc_line": "化工",
            "sulfuric_v2_plant": "化工", "cracker_v2_plant": "化工",
            "silicon_furnace": "冶金", "hcl_tower": "化工",
            "tcs_reactor": "化工", "silicon_plant": "电子",
            "cable_line": "制造",
        }
        return names.get(def_id, def_id)

    def _record_open_state(self, _evt=None):
        def walk(parent):
            for iid in self.tree.get_children(parent):
                self._open_by_key[iid] = bool(self.tree.item(iid, "open"))
                walk(iid)
        if not hasattr(self, "_open_by_key"):
            self._open_by_key = {}
        walk("")

    def _rebuild(self):
        # 保留用户折叠状态
        if not hasattr(self, "_open_by_key"):
            self._open_by_key = {}
        open_map = dict(self._open_by_key)
        self.tree.delete(*self.tree.get_children())
        self._fac_map = {}
        self._open_by_key = {}
        ind = self.engine.registry.get("industry")
        if ind is None or not ind.facilities:
            return
        # 按当前维度分组（折叠 key 带模式前缀，切换维度不串味）
        from collections import defaultdict
        mode = getattr(self, "_group_mode", "type")
        groups = defaultdict(list)
        labels = {}
        for f in ind.facilities.values():
            if getattr(self, "_only_stalled", False) and not f.stalled_reported:
                continue                      # 只看停摆
            gkey, glabel = self._group_of(ind, f)
            groups[gkey].append(f)
            labels[gkey] = glabel
        if not groups:
            gkey = f"build:{mode}:__empty__"
            self.tree.insert("", "end", iid=gkey,
                             text="（没有停摆的设施）" if getattr(
                                 self, "_only_stalled", False)
                             else "（暂无设施）",
                             open=True, tags=("group",))
            self._open_by_key[gkey] = True
            return
        # 状态维度按"停摆→待分配→运转"排序，其余按名称
        order = {"stalled": 0, "idle": 1, "running": 2}
        if mode == "status":
            gkeys = sorted(groups.keys(), key=lambda k: order.get(k, 9))
        elif mode == "ring":
            gkeys = sorted(groups.keys(),
                           key=lambda k: int(k.replace("ring", "") or 0))
        else:
            gkeys = sorted(groups.keys(), key=lambda k: labels.get(k, k))
        for gk in gkeys:
            gkey = f"build:{mode}:{gk}"
            g_open = open_map.get(gkey, True)
            gid = self.tree.insert("", "end", iid=gkey,
                                   text=labels.get(gk, gk),
                                   open=g_open, tags=("group",))
            self._open_by_key[gkey] = g_open
            for f in sorted(groups[gk], key=lambda x: x.id):
                units = ",".join(f.assigned) or "-"
                act = self._activity_text(f, ind.defs[f.def_id], ind)
                stalled = ""
                if f.under_construction:
                    left = ind.build_progress(self.engine, f.id)
                    left_txt = f"{left:.0f}s" if left is not None else "…"
                    stalled = f" {T.S_BUILD}建造中 {left_txt}"
                elif f.mothballed:
                    stalled = " ⏸封存"
                elif f.halt_until > self.engine.clock.time:
                    stalled = f" {T.S_WARN}{f.halt_reason or '停机'}"
                elif f.stalled_reported:
                    stalled = f" {T.S_WARN}{f.stall_reason or '停摆'}"
                elif f.upkeep < 75.0 and (self.engine.registry.get(
                        "maintenance") is not None):
                    stalled = f" 设备{f.upkeep:.0f}"
                plot = self.engine.world.get(f.plot_id)
                ring = f"圈{plot.ring}" if plot is not None else "?"
                head = f"{f.id} {f.name} {ring}"
                if act:
                    head += f" {act}"
                iid = self.tree.insert(
                    gid, "end",
                    text=f"{head} 单元[{units}]{stalled}",
                    tags=("fac",), values=(f.id,))
                self._fac_map[iid] = f.id
                self._open_by_key[iid] = True

    def select_facility(self, fac_id: str) -> bool:
        """按设施 id 在树中定位并选中（供地块面板双击跳转）。

        会先按需重建树、展开其所属分组，选中后刷新详情与操作行。
        """
        ind = self.engine.registry.get("industry")
        if ind is None or fac_id not in ind.facilities:
            return False
        if self._fac_map.get(self.selected) != fac_id:
            self._rebuild()
        for iid, fid in self._fac_map.items():
            if fid != fac_id:
                continue
            parent = self.tree.parent(iid)
            if parent:
                self.tree.item(parent, open=True)
                self._open_by_key[parent] = True
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.tree.focus(iid)
            self.selected = fac_id
            self._render_detail()
            self._render_actions()
            return True
        return False

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        fac_id = self._fac_map.get(iid)
        if fac_id is None:
            return
        self.selected = fac_id
        self._render_detail()
        self._render_actions()

    def _on_double(self, _evt=None):
        """双击设施 → 打开百科该设施词条。"""
        sel = self.tree.selection()
        if not sel:
            return
        fac_id = self._fac_map.get(sel[0])
        if fac_id is None:
            return
        ind = self.engine.registry.get("industry")
        f = ind.facilities.get(fac_id) if ind else None
        cb = getattr(self, "open_wiki", None)
        if f is None or cb is None:
            return
        cb(f"fac:{f.def_id}")

    def _render_detail(self):
        ind = self.engine.registry.get("industry")
        f = ind.facilities.get(self.selected)
        if f is None:
            return
        d = ind.defs[f.def_id]
        parts = [f"{f.id} {f.name} @{f.plot_id}"]
        if f.under_construction:
            left = ind.build_progress(self.engine, f.id)
            parts.append(f"{T.S_BUILD} 建造中"
                         + (f"：剩余约 {left:.0f}s" if left is not None
                            else "（施工单元就位）"))
        if f.mothballed:
            parts.append("⏸ 已封存：不运转、零维护消耗"
                         "（解封需一个执行单元 + 重启时间）")
        elif f.halt_until > self.engine.clock.time:
            left = f.halt_until - self.engine.clock.time
            parts.append(f"{T.S_WARN} {f.halt_reason or '停机'}："
                         f"剩余约 {left:.0f}s")
        maint = self.engine.registry.get("maintenance")
        if maint is not None and maint.enabled(self.engine):
            parts.append(f"设备状态 {f.upkeep:.0f}/100"
                         f"（维护件 {maint.use_per_sec:g}/s"
                         + ("；已停用" if not f.assigned else "") + "）")
        units = ",".join(f.assigned) or "无"
        eff = float(getattr(self.engine.units, "efficiency", 1.0) or 1.0)
        slots = int(d.get("slots", 1))
        if eff > 1.0001:
            sf = self.engine.units.staff_factor(len(f.assigned), slots)
            parts.append(f"已分配单元: {units}  "
                         f"〔效能 ×{eff:.2f} → 产能/节拍 ×{sf:.2f}，"
                         f"满负荷需 {self.engine.units.effective_slots(slots)} 个〕")
        else:
            parts.append(f"已分配单元: {units}")
        if d.get("extract_rate"):
            plot = self.engine.world.get(f.plot_id)
            gf = 1.0
            if plot is not None and plot.kind != "water" \
                    and hasattr(ind, "grade_factor"):
                gf = ind.grade_factor(plot.substance, plot.grade)
            parts.append(f"产出 {d['extract_rate']:g}/s "
                         f"(耗电 {d.get('power_use', 0):g}/s)"
                         + (f"　实际 ≈{d['extract_rate'] * gf:g}/s"
                            f"（品位折算 ×{gf:.2f}）" if gf != 1.0 else ""))
        elif d.get("kind") == "unit_factory":
            rate = float(d.get("unit_rate", 0.0))
            r = ind.recipes.get(d.get("recipe", ""), {})
            ins = ", ".join(f"{self.router.rname(k)} {v:g}/s"
                            for k, v in r.get("inputs", {}).items())
            parts.append(f"消耗: {ins}")
            parts.append(f"产出: 执行单元 {rate:g}/s"
                         f"（约 {1 / rate:.0f}s 一个）"
                         f"　进度 {f.unit_progress:.0%}")
        elif d.get("recipe"):
            r = ind.recipes.get(d["recipe"], {})
            outs = ", ".join(f"{self.router.rname(k)} {v:g}/s"
                             for k, v in r.get("outputs", {}).items())
            ins = ", ".join(f"{self.router.rname(k)} {v:g}/s"
                            for k, v in r.get("inputs", {}).items())
            parts.append(f"消耗: {ins}")
            parts.append(f"产出: {outs}")
        if d.get("kind") == "burner":
            parts.append(f"燃料: {f.fuel or '未设(用 fuel 命令)'}")
        if d.get("kind") == "vent":
            parts.append("放空对象: "
                         + "、".join(self.router.rname(x) for x in
                                     d.get("vent_substances", []))
                         + f"　速率 {d.get('vent_rate', 0):g}/s（材料被烧掉）")
        if d.get("kind") == "sink":
            cap = float(d.get("capacity", 0.0))
            parts.append("回注对象: "
                         + "、".join(self.router.rname(x) for x in
                                     d.get("sink_substances", []))
                         + f"　堆存 {f.stored:g}/{cap:g}"
                         f"（{f.stored / cap:.0%}，装满需再建）" if cap else "")
        if getattr(f, "backlog_over", None):
            parts.append(f"{T.S_WARN} 因「{self.router.rname(f.backlog_over)}」"
                         "积压而限产")
        if f.stalled_reported:
            parts.append(f"{T.S_WARN} 停摆中：{f.stall_reason or '原因未知'}")
        self.detail_label.configure(text="\n".join(parts), fg=T.TEXT)
        self.detail_label.after_idle(self._fit_wrap)

    # ---- 上下文操作行（仿地块面板）-------------------------------
    def _act_btn(self, parent, text, cmd, enabled=True):
        b = tk.Label(parent, text=text, font=T.FONT_UI,
                     fg=T.TEXT if enabled else T.TEXT_DIM,
                     bg=T.PANEL if enabled else T.PANEL_ALT,
                     padx=6, pady=2, cursor="hand2" if enabled else "X_cursor")
        if enabled:
            b.bind("<Button-1>", lambda e: cmd())
        b.pack(side="left", padx=2, pady=2)
        return b

    def _render_actions(self):
        """选中设施后的上下文按钮：分配/调离 + burner 燃料选择。"""
        if not hasattr(self, "detail"):
            return
        # 清除旧操作行
        for w in getattr(self, "_action_row", None) or []:
            try:
                w.destroy()
            except tk.TclError:
                pass
        self._action_row = []
        ind = self.engine.registry.get("industry")
        if self.selected is None or ind is None:
            return
        f = ind.facilities.get(self.selected)
        if f is None:
            return
        row = tk.Frame(self.detail, bg=T.PANEL)
        row.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
        self._action_row = [row]
        d = ind.defs[f.def_id]
        self._act_btn(row, "分配", lambda: self.router.execute(
            f"assign {self.selected}"))
        if f.assigned:
            self._act_btn(row, "调离", lambda: self.router.execute(
                f"unassign {self.selected}"))
        # 封存/解封（批次2b：维护件供不上时的"先停一停"）
        if f.mothballed:
            self._act_btn(row, "解除封存", lambda: self.router.execute(
                f"mothball {self.selected} off"))
        else:
            self._act_btn(row, "封存", lambda: self.router.execute(
                f"mothball {self.selected}"))
        # 拆除（P1 ③：返还一半材料，地块可重新规划）
        self._act_btn(row, "拆除", lambda: self.router.execute(
            f"demolish {self.selected}"))
        if d.get("kind") == "burner":
            # 按设施燃料类别白名单过滤（无白名单设施=全部可燃）
            if hasattr(ind, "fuel_options_for"):
                fuels = ind.fuel_options_for(d.get("id", f.def_id))
            elif hasattr(ind, "fuel_options"):
                fuels = ind.fuel_options()
            else:
                fuels = []
            cur = f.fuel
            self._act_btn(row, "燃料 ▾",
                          lambda: self._toggle_fuel_list(fuels))
        # 定位到所属地块（左侧地块树选中）
        locate = getattr(self, "locate", None)
        if locate is not None and self.engine.world.get(f.plot_id) is not None:
            self._act_btn(row, "定位地块",
                          lambda p=f.plot_id: locate(p))
        self.detail_label.pack(side="top", fill="x", padx=6, pady=4)
        # 展开燃料列表（每拍重建后恢复其可见状态）
        if getattr(self, "_fuel_list_open", False):
            self._render_fuel_list(fuels)

    def _toggle_fuel_list(self, fuels):
        self._fuel_list_open = not getattr(self, "_fuel_list_open", False)
        self._render_actions()

    def _render_fuel_list(self, fuels):
        """burner 燃料展开列表：点击任一项即设燃料（仿地块建造展开）。"""
        ind = self.engine.registry.get("industry")
        f = ind.facilities.get(self.selected) if ind else None
        if f is None:
            return
        if not fuels:
            return
        wrap = tk.Frame(self.detail, bg=T.PANEL)
        wrap.pack(side="bottom", fill="x", padx=6, pady=(0, 3))
        self._action_row.append(wrap)
        from tkinter import ttk
        box = ttk.Treeview(wrap, show="tree", selectmode="browse",
                           height=min(6, len(fuels)))
        box.pack(side="left", fill="both", expand=True)
        style_tree(box, "Fuel.Treeview")
        for fu in fuels:
            mark = "●" if fu == f.fuel else " "
            box.insert("", "end", text=f"{mark} {self.router.rname(fu)}",
                       values=(fu,))
        box.bind("<<TreeviewSelect>>", self._on_fuel_pick)
        self._fuel_tree = box

    def _on_fuel_pick(self, _evt=None):
        box = getattr(self, "_fuel_tree", None)
        if box is None:
            return
        sel = box.selection()
        if not sel:
            return
        fu = box.item(sel[0], "values")[0]
        self.router.execute(f"fuel {self.selected} {fu}")
        self._fuel_list_open = False
        self._render_actions()

    def _fit_wrap(self):
        try:
            w = self.detail_label.winfo_width()
            if w > 40:
                self.detail_label.configure(wraplength=w - 12)
        except tk.TclError:
            pass

    def _refresh_sig(self, engine):
        ind = self.engine.registry.get("industry")
        if ind is None:
            return ()

        def reserve_bucket(f):
            """采掘设施：储量按 100 一档入签名，行内储量能缓慢跟着更新。"""
            if not ind.defs.get(f.def_id, {}).get("extract_rate"):
                return 0
            plot = self.engine.world.get(f.plot_id)
            return int((plot.reserve if plot is not None else 0.0) // 100)

        return tuple(sorted(
            (f.id, f.def_id, tuple(f.assigned), bool(f.stalled_reported),
             bool(f.under_construction), bool(f.mothballed),
             f.halt_reason or "", round(float(f.upkeep), 1),
             reserve_bucket(f), f.fuel or "")
            for f in ind.facilities.values()))

    def _do_rebuild(self):
        self._rebuild()

    def _after_refresh(self, engine, router):
        if self.selected is None:
            return
        ind = engine.registry.get("industry")
        if self.selected not in ind.facilities:
            self.selected = None
            return
        # 重建后恢复选中高亮（iid 会变，按值反查）
        if self.tree.selection():
            pass
        else:
            for iid, fid in self._fac_map.items():
                if fid == self.selected:
                    self.tree.selection_set(iid)
                    self.tree.see(iid)
                    break
        self._render_detail()
        self._render_actions()
