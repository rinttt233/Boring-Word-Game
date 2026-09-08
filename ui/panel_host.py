"""PanelHost：自研极简标签页容器（黑白灰）。

- 顶部标签条：扁平小按钮，选中=黑底白字，未选中=灰字；未解锁=更浅灰+🔒
- 内容区：各面板 Frame 显隐切换（grid_remove/grid）
- 每拍轮询各面板 unlock()，未解锁标签置灰并禁止切换

实现采用"Frame 容器 + 自定义 Label 标签"而非 ttk.Notebook，
以完全控制黑白灰样式（用户定案）。
"""
import tkinter as tk

from ui import theme as T
from ui.panels import PanelRegistry


class PanelHost:
    def __init__(self, master, engine, router, registry: PanelRegistry,
                 side: str = "left", key: str = "") -> None:
        self.engine = engine
        self.router = router
        self.registry = registry
        self.side = side
        self.key = key          # 用于偏好持久化
        self.panels = registry.ordered()
        self.current = None
        self._tab_widgets = {}
        self._prefs = None

        # 外框
        self.frame = tk.Frame(master, bg=T.BG)
        self.frame.pack(side="left", fill="both", expand=True)
        # 标签条（Canvas 包裹，支持水平滚动）
        self._build_tabbar()
        # 内容容器
        self.content = tk.Frame(self.frame, bg=T.BG)
        self.content.pack(side="top", fill="both", expand=True, padx=2)
        # 让选中的 page 撑满 content（修复面板高度不伸缩）
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        # 建每个面板的 Frame
        for p in self.panels:
            page = tk.Frame(self.content, bg=T.BG)
            page.grid(row=0, column=0, sticky="nsew")
            p.frame = page
            p.build(page, engine, router)
            page.grid_remove()   # 默认隐藏，等待选中

        self._rebuild_tabs()

    def attach_prefs(self, prefs):
        """绑定偏好：读取当前标签，写回时记录。"""
        self._prefs = prefs
        if self.key:
            saved = prefs.get(f"host.{self.key}.tab")
            if saved:
                self._pending_tab = saved
        prefs.add_hooks(write_fn=self._write_prefs)
        prefs.add_hooks(read_fn=self._read_prefs)

    def _read_prefs(self, prefs):
        if self.key:
            saved = prefs.get(f"host.{self.key}.tab")
            if saved and saved != getattr(self.current, "key", None):
                self.select(saved)

    def _write_prefs(self, prefs):
        if self.key and self.current is not None:
            prefs.set(f"host.{self.key}.tab", self.current.key)

    # ---- 标签条（空间不足自动换行堆叠）---------------------------
    def _build_tabbar(self) -> None:
        outer = tk.Frame(self.frame, bg=T.BG)
        outer.pack(side="top", fill="x", padx=2, pady=(4, 0))
        self._tabbar_outer = outer
        # Canvas 承载标签行（高度随行数变化）
        self._tabcanvas = tk.Canvas(outer, bg=T.BG, height=28,
                                    highlightthickness=0)
        self._tabcanvas.pack(side="top", fill="x")
        self._tabinner = tk.Frame(self._tabcanvas, bg=T.BG)
        self._tabwin = self._tabcanvas.create_window((0, 0),
                                                     window=self._tabinner,
                                                     anchor="nw")
        self._tabinner.bind("<Configure>", self._on_tabinner_configure)
        self._tabcanvas.bind("<Configure>", self._on_tabcanvas_configure)

    def _on_tabinner_configure(self, _e):
        self._tabcanvas.configure(scrollregion=self._tabcanvas.bbox("all"))

    def _on_tabcanvas_configure(self, e):
        # 宽度变化 → 重新排布（可能换行数变化）；宽度未变则忽略，防循环
        if getattr(self, "_last_tab_width", None) == e.width:
            return
        self._last_tab_width = e.width
        self._layout_tabs()

    def _layout_tabs(self) -> None:
        """标签堆叠排布：一行放不下就换到下一行，并调整标签条高度。

        采用 place 精确排布（每个标签宽度不同），换行后把 Canvas 与内嵌
        Frame 的高度同步增大，避免标签被裁掉。
        """
        try:
            avail = self._tabcanvas.winfo_width()
        except tk.TclError:
            return
        if avail <= 1:
            # 尚未完成布局，稍后再试（最多重试若干次，避免自旋）
            n = getattr(self, "_layout_retry", 0)
            if n < 20:
                self._layout_retry = n + 1
                self.root_after_idle()
            return
        self._layout_retry = 0
        x = 0
        y = 0
        row_h = 0
        gap = 2
        for p in self.panels:
            b = self._tab_widgets.get(p.key)
            if b is None:
                continue
            try:
                bw = b.winfo_reqwidth()
                bh = b.winfo_reqheight()
            except tk.TclError:
                continue
            if x > 0 and x + bw > avail:
                x = 0
                y += row_h
                row_h = 0
            b.place(x=x, y=y)
            x += bw + gap
            row_h = max(row_h, bh)
        total_h = max(26, y + row_h)
        if getattr(self, "_last_tab_height", None) == total_h and \
                getattr(self, "_last_tab_width", None) == avail:
            return          # 结果无变化：不再回写，避免 Configure 递归
        self._last_tab_height = total_h
        try:
            self._tabcanvas.configure(height=total_h)
            self._tabcanvas.itemconfigure(self._tabwin, width=avail,
                                          height=total_h)
        except tk.TclError:
            pass

    # ---- 标签条绘制 ---------------------------------------------
    def _rebuild_tabs(self) -> None:
        """重建标签条（按当前解锁状态渲染到可滚动内部 Frame）。"""
        for w in self._tabinner.winfo_children():
            w.destroy()
        self._tab_widgets = {}
        for p in self.panels:
            unlocked = p.unlock(self.engine)
            b = tk.Label(
                self._tabinner,
                text=(p.title if unlocked else f"{p.title} *"),
                font=T.FONT_UI,
                fg=T.TEXT if unlocked else T.TEXT_DIM,
                bg=T.PANEL if unlocked else T.PANEL_ALT,
                padx=8, pady=3, cursor="hand2" if unlocked else "arrow")
            # 位置由 _layout_tabs 计算（自动换行堆叠）
            b.place(x=0, y=0)
            if unlocked:
                b.bind("<Button-1>", lambda e, key=p.key: self.select(key))
            else:
                # 置灰面板点击 → 提示（仅日志）
                b.bind("<Button-1>",
                       lambda e, key=p.key: self._on_locked_tab(key))
            self._tab_widgets[p.key] = b
        # 排布（可能需换行）
        self.root_after_idle()

    def root_after_idle(self) -> None:
        try:
            self._tabcanvas.after_idle(self._layout_tabs)
        except tk.TclError:
            pass

    def _on_locked_tab(self, key: str) -> None:
        self.engine.log(f"[面板] 「{key}」尚未解锁。")

    # ---- 选中切换 -----------------------------------------------
    def select(self, key: str) -> None:
        target = None
        for p in self.panels:
            if p.key == key:
                target = p
                break
        if target is None:
            return
        if not target.unlock(self.engine):
            self._on_locked_tab(key)
            return
        # 隐藏当前
        if self.current is not None:
            self.current.frame.grid_remove()
        # 显示目标并更新标签样式
        target.frame.grid()
        self.current = target
        self._style_tab(key, selected=True)
        for other in self.panels:
            if other.key != key:
                self._style_tab(other.key, selected=False)
        target.refresh(self.engine, self.router)

    def _style_tab(self, key: str, selected: bool) -> None:
        b = self._tab_widgets.get(key)
        if b is None:
            return
        b.configure(relief="flat")
        if selected:
            b.configure(bg=T.ACCENT, fg=T.WARN_FG)
        else:
            # 重新按解锁态着色
            for p in self.panels:
                if p.key == key:
                    unlocked = p.unlock(self.engine)
                    b.configure(bg=T.PANEL if unlocked else T.PANEL_ALT,
                                fg=T.TEXT if unlocked else T.TEXT_DIM)
                    break

    # ---- 每拍刷新 -----------------------------------------------
    def pump(self) -> None:
        # 标签解锁状态可能变化 → 重建标签条（仅当解锁集合变了）
        need_rebuild = self._unlock_signature() != getattr(
            self, "_unlock_sig", None)
        if need_rebuild:
            self._unlock_sig = self._unlock_signature()
            self._rebuild_tabs()
        # 刷新当前面板
        if self.current is not None:
            self.current.refresh(self.engine, self.router)

    def _unlock_signature(self) -> tuple:
        return tuple(p.unlock(self.engine) for p in self.panels)

    def select_first_unlocked(self) -> None:
        for p in self.panels:
            if p.unlock(self.engine):
                self.select(p.key)
                return
