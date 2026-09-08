"""图形化按钮栏（文件管理器式分层导航 + 多步选择流程）。

分层导航：
- 顶层=类别按钮；点类别进入其操作子层，收起类别层。
- 子层含"← 返回"，点击回上层；同层按钮从左往右流式排版、排满换行。

多步流程（用户定案）：
- 建造：扩展→建造→选地块→选建筑→【确定/取消】
  * 只列"已占领"的地块（可建厂）作为第一步；
  * 选中地块后列出该地块可建设施（未解锁/材料不足会标灰）；
  * 选建筑后进入确认步骤：控制台显示建筑信息，图形菜单给 确定/取消，
    材料不足则"确定"锁定（灰显）。
- 恢复：生存→恢复条目→选条目→【确定/取消】。
- 勘探：勘探层内嵌滑块（取代原"勘探"按钮），拖选圈层即执行。

所有执行仍经 router.execute，与终端指令一致；按钮点击短暂闪烁。
"""
from typing import Callable, Dict, List, Optional

import tkinter as tk

from ui import theme as T
from ui.commands import fmt_time


class ButtonBar:
    def __init__(self, master, engine, router,
                 selected_getter: Optional[Callable[[], Optional[str]]] = None,
                 on_open_build: Optional[Callable[[], None]] = None) -> None:
        self.master = master
        self.engine = engine
        self.router = router
        # 用顶层 widget 作为 after 宿主，避免子 widget 销毁后回调失效
        self._tk = master.winfo_toplevel()
        self.selected_getter = selected_getter or (lambda: None)
        self.on_open_build = on_open_build or (lambda: None)
        self._stack: List[str] = []
        self._frame = tk.Frame(master, bg=T.PANEL)
        self._survey_var = None
        self._flow: Optional[dict] = None   # 多步流程状态
        self._flow_selected_plot = None
        self._flow_selected_fac = None
        self._flow_selected_entry = None
        self._after_jobs = []   # 登记所有 after 句柄，便于取消
        self._destroyed = False
        self._render()

    # ================= 类别定义 =================
    def _levels(self) -> Dict[str, dict]:
        def btn(label, cmd, need_sel=False):
            return {"label": label, "cmd": cmd, "need_sel": need_sel}
        return {
            "时间": {"buttons": [
                btn("‖/▶ 暂停", "toggle"),
                btn("加速 ×2", "speed 2"), btn("加速 ×4", "speed 4"),
                btn("减速 ×0.5", "speed 0.5"), btn("常速 ×1", "speed 1"),
                btn("巡航 30s", "cruise 30"), btn("巡航 60s", "cruise 60"),
                btn("取消巡航", "cruise off"),
            ]},
            "勘探": {"buttons": [
                btn("勘察列表", "plots"),
            ], "has_survey_slider": True},
            "扩展": {"buttons": [
                btn("占领", "claim SELECTED", need_sel=True),
                btn("建造", "_FLOW:build", need_sel=True),
            ]},
            "工业": {"buttons": [
                btn("分配", "assign SELECTED", need_sel=True),
                btn("调离", "unassign SELECTED", need_sel=True),
                btn("进厂清单", "facilities"),
            ]},
            "生存": {"buttons": [
                btn("加固记忆", "maintain"),
                btn("恢复条目", "_FLOW:recover"),
                btn("固化条目", "fixate"), btn("条目清单", "entries"),
            ]},
            "终局": {"buttons": [
                btn("建造子系统", "construct"), btn("烧录迁移", "migrate"),
                btn("工程进度", "db"),
            ]},
            "系统": {"buttons": [
                btn("存档", "save"), btn("读档", "load"),
                btn("状态", "status"), btn("帮助", "help"),
            ]},
        }

    # ================= 渲染 =================
    def _render(self) -> None:
        for w in self._frame.winfo_children():
            w.destroy()
        self._survey_var = None

        buttons = []          # (label, cmd, need_sel, style)
        start_row = 0
        # 流程优先：若在多步流程中，渲染流程步骤
        if self._flow is not None:
            self._render_flow(buttons)
        elif self._stack:
            cat_name = self._stack[-1]
            cat = self._levels().get(cat_name) or {"buttons": []}
            buttons.append(("← 返回", "_BACK", False, "accent"))
            for b in cat["buttons"]:
                style = ("primary" if b["cmd"].startswith("_FLOW:")
                         else "normal")
                buttons.append((b["label"], b["cmd"], b["need_sel"], style))
            # 勘探层：滑块单开一行（行 0），按钮从行 1 开始
            if cat.get("has_survey_slider"):
                self._render_survey_slider(row=0)
                start_row = 1
        else:
            for name in self._levels().keys():
                buttons.append((name, "_ENTER:" + name, False, "category"))

        COLS = 4
        row = start_row
        col = 0
        for (label, cmd, need_sel, style) in buttons:
            self._grid_button(row, col, label, cmd, need_sel, style)
            col += 1
            if col >= COLS:
                col = 0
                row += 1
        self._frame.grid_columnconfigure(tuple(range(COLS)), weight=1,
                                         uniform="btn")

    def _render_survey_slider(self, row: int = 0):
        srow = tk.Frame(self._frame, bg=T.PANEL)
        srow.grid(row=row, column=0, columnspan=4, sticky="ew",
                  padx=4, pady=(4, 2))
        tk.Label(srow, text="勘探圈层:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.PANEL).pack(side="left", padx=4)
        var = tk.IntVar(value=1)
        self._survey_var = var
        scale = tk.Scale(srow, from_=1, to=5, orient="horizontal",
                         variable=var, showvalue=True, length=120,
                         bg=T.PANEL, fg=T.TEXT, troughcolor=T.PANEL_ALT,
                         highlightthickness=0)
        scale.pack(side="left", padx=4)
        # 滑块只选圈层，需点"勘探"确认才执行
        go = tk.Label(srow, text="勘探", font=T.FONT_UI, fg=T.WARN_FG,
                      bg=T.ACCENT, padx=12, pady=2, cursor="hand2")
        go.pack(side="left", padx=8)
        go.bind("<Button-1>", lambda e: self._survey_go_flash(go))

    def _survey_go_flash(self, go):
        # 确认按钮短暂闪烁后执行
        try:
            go.configure(bg=T.PANEL_ALT, fg=T.TEXT)
        except tk.TclError:
            return
        self._safe_after(140, self._survey_go)

    def _survey_go(self):
        if self._survey_var is None:
            return
        ring = int(self._survey_var.get())
        self.router.execute(f"survey {ring}")

    # ---- 多步流程渲染 -------------------------------------------
    def _render_flow(self, buttons):
        flow = self._flow
        kind = flow["kind"]
        # 统一加"取消流程"按钮
        buttons.append(("✕ 取消", "_FLOW_CANCEL", False, "accent"))
        if kind == "build":
            self._render_build_flow(buttons)
        elif kind == "recover":
            self._render_recover_flow(buttons)

    def _render_build_flow(self, buttons):
        # 步骤1: 选地块（已占领可建厂）
        if self._flow_selected_plot is None:
            buttons.insert(0, ("← 上一步", "_BACK", False, "accent"))
            for p in self._claimable_plots():
                buttons.append((p.id, f"_FLOW_PICK_PLOT:{p.id}", False,
                                "normal"))
            return
        # 步骤2: 选建筑
        if self._flow_selected_fac is None:
            buttons.insert(0, ("← 上一步", "_FLOW_BACK_PLOT", False,
                               "accent"))
            for d in self._buildable_facs(self._flow_selected_plot):
                label = d["name"] + (" *" if d["locked"] else "")
                buttons.append((label, f"_FLOW_PICK_FAC:{d['id']}",
                                d["locked"], "normal"))
            return
        # 步骤3: 确认（确定/取消），材料不足锁定确定
        fac = self._fac_def(self._flow_selected_fac)
        cost_ok = self._afford(fac.get("build_cost", {}))
        can = ("确定 √" if cost_ok else "确定 X(材料不足)")
        buttons.insert(0, ("← 上一步", "_FLOW_BACK_FAC", False, "accent"))
        buttons.append((can, "_FLOW_CONFIRM", not cost_ok, "primary"))

    def _render_recover_flow(self, buttons):
        if self._flow_selected_entry is None:
            buttons.insert(0, ("← 上一步", "_BACK", False, "accent"))
            for e in self._recovery_entries():
                locked = e.get("state") == "permanent"
                label = e["id"] + (" √" if e.get("state") == "permanent" else "")
                buttons.append((label, f"_FLOW_PICK_ENTRY:{e['id']}",
                                locked, "normal"))
            return
        # 确认步骤
        e = self._entry_def(self._flow_selected_entry)
        cost_ok = self._afford(e.get("cost", {}))
        can = ("确定 √" if cost_ok else "确定 X(材料不足)")
        buttons.insert(0, ("← 上一步", "_FLOW_BACK_ENTRY", False, "accent"))
        buttons.append((can, "_FLOW_CONFIRM", not cost_ok, "primary"))

    # ---- 数据查询 ------------------------------------------------
    def _claimable_plots(self):
        """可建造的地块 = 已占领（可建厂）。"""
        plots = []
        for p in self.engine.world.visible_plots():
            if p.state == "claimed":
                plots.append(p)
        return plots

    def _buildable_facs(self, plot_id):
        plot = self.engine.world.get(plot_id)
        if plot is None:
            return []
        ind = self.engine.registry.get("industry")
        rec = self.engine.registry.get("recovery")
        out = []
        for d in ind.defs.values():
            if plot.kind not in d.get("allowed_plot_kinds", []):
                continue
            need = d.get("requires_recovery")
            locked = bool(need) and (rec is None or not rec.is_unlocked(need))
            out.append({"id": d["id"], "name": d["name"], "locked": locked,
                        "def": d})
        return out

    def _fac_def(self, fac_id):
        ind = self.engine.registry.get("industry")
        return ind.defs.get(fac_id, {})

    def _recovery_entries(self):
        rec = self.engine.registry.get("recovery")
        if rec is None:
            return []
        return rec.entry_list()

    def _entry_def(self, entry_id):
        rec = self.engine.registry.get("recovery")
        return rec.entries.get(entry_id, {})

    def _afford(self, cost: dict) -> bool:
        """是否买得起（只读检查，不消耗）。"""
        for rid, amt in cost.items():
            if self.engine.economy.get(rid) < float(amt):
                return False
        return True

    # ---- 按钮创建 ------------------------------------------------
    def _grid_button(self, row, col, text, cmd, need_sel, style):
        return self._make_button(row, col, text, cmd, need_sel, style)

    def _make_button(self, row, col, text, cmd, need_sel, style,
                     on_click=None):
        sel = self.selected_getter()
        disabled = need_sel and sel is None
        if style == "accent":
            fg, bg = T.WARN_FG, T.ACCENT
        elif style == "category":
            fg, bg = T.TEXT, T.PANEL_ALT
        elif style == "primary":
            fg, bg = T.WARN_FG, T.ACCENT
        else:
            fg, bg = T.TEXT, T.PANEL
        if disabled:
            fg, bg = T.TEXT_DIM, T.PANEL_ALT
        b = tk.Label(self._frame, text=text, font=T.FONT_UI, fg=fg, bg=bg,
                     padx=6, pady=4, cursor="arrow" if disabled else "hand2",
                     borderwidth=0)
        b.grid(row=row, column=col, sticky="ew", padx=1, pady=1)
        if not disabled:
            cb = on_click or (lambda: self._flash_then(b, cmd))
            b.bind("<Button-1>", lambda e: cb())
        return b

    # ---- 动作 ------------------------------------------------
    def _safe_after(self, ms, fn):
        """安全调度：登记句柄、回调吞异常、销毁后可取消。"""
        if self._destroyed:
            return None
        try:
            job = self._tk.after(ms, lambda: self._run_safe(fn))
            self._after_jobs.append(job)
            return job
        except tk.TclError:
            return None

    def _run_safe(self, fn):
        try:
            fn()
        except Exception:
            # 吞掉异常，防止泄漏到 Tkinter 回调层
            pass

    def _flash_then(self, label, cmd):
        try:
            label.configure(bg=T.ACCENT, fg=T.WARN_FG)
        except tk.TclError:
            return
        self._safe_after(140, lambda: self._after_flash(label, cmd))

    def _after_flash(self, label, cmd):
        try:
            label.configure(bg=T.PANEL, fg=T.TEXT)
        except tk.TclError:
            return
        try:
            self._dispatch(cmd)
        except Exception as e:
            try:
                self.engine.log(f"[按钮] 执行失败: {e}")
            except Exception:
                pass

    def destroy(self):
        """取消所有待执行的 after 回调（窗口销毁时调用）。"""
        self._destroyed = True
        for job in self._after_jobs:
            try:
                self._tk.after_cancel(job)
            except (tk.TclError, Exception):
                pass
        self._after_jobs = []

    def _dispatch(self, cmd):
        if cmd == "_BACK":
            if self._stack:
                self._stack.pop()
            self._render()
        elif cmd.startswith("_ENTER:"):
            self._stack.append(cmd.split(":", 1)[1])
            self._render()
        elif cmd.startswith("_FLOW:"):
            self._start_flow(cmd.split(":", 1)[1])
        elif cmd.startswith("_FLOW_PICK_PLOT:"):
            self._flow_selected_plot = cmd.split(":", 1)[1]
            self._render()
        elif cmd.startswith("_FLOW_PICK_FAC:"):
            self._flow_selected_fac = cmd.split(":", 1)[1]
            self._confirm_info()
            self._render()
        elif cmd.startswith("_FLOW_PICK_ENTRY:"):
            self._flow_selected_entry = cmd.split(":", 1)[1]
            self._confirm_info()
            self._render()
        elif cmd == "_FLOW_BACK_PLOT":
            self._flow_selected_plot = None
            self._render()
        elif cmd == "_FLOW_BACK_FAC":
            self._flow_selected_fac = None
            self._render()
        elif cmd == "_FLOW_BACK_ENTRY":
            self._flow_selected_entry = None
            self._render()
        elif cmd == "_FLOW_CANCEL":
            self._flow = None
            self._flow_selected_plot = None
            self._flow_selected_fac = None
            self._flow_selected_entry = None
            self._render()
        elif cmd == "_FLOW_CONFIRM":
            self._confirm_execute()
        else:
            if "SELECTED" in cmd:
                sel = self.selected_getter()
                if sel is None:
                    self.engine.log("[按钮] 未选中对象，请先在地块/建筑栏选择。")
                    return
                cmd = cmd.replace("SELECTED", sel)
            self.router.execute(cmd)
            if self._flow is not None:
                self._flow = None
                self._render()

    def _start_flow(self, kind):
        self._flow = {"kind": kind}
        self._flow_selected_plot = None
        self._flow_selected_fac = None
        self._flow_selected_entry = None
        self._render()

    def _confirm_info(self):
        """选中建筑/条目后，控制台显示其信息。"""
        if self._flow["kind"] == "build" and self._flow_selected_fac:
            fac = self._fac_def(self._flow_selected_fac)
            cost = ", ".join(f"{k} {v:g}" for k, v in fac.get("build_cost", {}).items()) or "无"
            bt = fac.get("build_time", "")
            info = f"将建造: {fac['name']} | 成本 {cost}"
            tm = f" | 工时 {bt:g}s" if bt else ""
            self.engine.log(f"[流程] {info}{tm} — 请点『确定』或『取消』。")
        elif self._flow["kind"] == "recover" and self._flow_selected_entry:
            e = self._entry_def(self._flow_selected_entry)
            cost = ", ".join(f"{k} {v:g}" for k, v in e.get("cost", {}).items())
            self.engine.log(
                f"[流程] 将恢复: {e['name']} | 成本 {cost} | "
                f"{e.get('desc', '')} — 请点『确定』或『取消』。")

    def _confirm_execute(self):
        if self._flow["kind"] == "build":
            plot = self._flow_selected_plot
            fac = self._flow_selected_fac
            if plot and fac:
                self.router.execute(f"build {plot} {fac}")
        elif self._flow["kind"] == "recover":
            if self._flow_selected_entry:
                self.router.execute(f"recover {self._flow_selected_entry}")
        self._flow = None
        self._flow_selected_plot = None
        self._flow_selected_fac = None
        self._flow_selected_entry = None
        self._render()

    def pack(self, **kw):
        self._frame.pack(**kw)
        return self

    def grid(self, **kw):
        self._frame.grid(**kw)
        return self
