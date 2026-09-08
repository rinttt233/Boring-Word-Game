"""面板系统基类：Panel / PanelRegistry / style_tree / _RightPanel（P1 重构拆分）。"""
from typing import Dict, List

import tkinter as tk

from ui import theme as T


def style_tree(tree, style_name: str):
    """给 ttk.Treeview 应用黑白灰样式，含选中高亮（黑底白字）。

    仅用 style 会遗留系统默认蓝色选中高亮，需 style.map 强制。
    """
    from tkinter import ttk
    s = ttk.Style()
    s.configure(style_name, background=T.BG, foreground=T.TEXT,
                fieldbackground=T.BG, font=T.FONT_MONO, borderwidth=0)
    s.configure(style_name + ".Heading", background=T.PANEL)
    # 选中行（黑底白字）；map 才能覆盖系统主题
    s.map(style_name,
          background=[("selected", T.ACCENT)],
          foreground=[("selected", T.WARN_FG)])
    tree.configure(style=style_name)
    bind_mousewheel(tree)


def bind_mousewheel(widget):
    """树/列表统一滚轮：鼠标滚轮纵向滚动（跨平台兼容）。"""
    def _on_wheel(event):
        try:
            step = -1 if event.delta > 0 else 1
            if hasattr(widget, "yview_scroll"):
                widget.yview_scroll(step, "units")
            elif hasattr(widget, "yview"):
                widget.yview_scroll(step, "units")
        except tk.TclError:
            pass
        return "break"
    widget.bind("<MouseWheel>", _on_wheel)
    widget.bind("<Button-4>", lambda e: _wheel_linux(widget, -1))
    widget.bind("<Button-5>", lambda e: _wheel_linux(widget, 1))


def _wheel_linux(widget, direction):
    try:
        widget.yview_scroll(direction, "units")
    except tk.TclError:
        pass
    return "break"


class Panel:
    key = ""
    title = ""

    def unlock(self, engine) -> bool:
        return True

    def build(self, parent, engine, router) -> None:
        raise NotImplementedError

    def refresh(self, engine, router) -> None:
        pass


class PanelRegistry:
    def __init__(self) -> None:
        self._panels: Dict[str, Panel] = {}
        self._order: List[str] = []

    def register(self, panel: Panel) -> Panel:
        if panel.key in self._panels:
            raise ValueError(f"面板已存在: {panel.key}")
        self._panels[panel.key] = panel
        self._order.append(panel.key)
        return panel

    def ordered(self) -> List[Panel]:
        return [self._panels[k] for k in self._order]


class _RightPanel(Panel):
    """右区面板基类：浅灰面板 + 标题行 + 内容滚动区。"""
    def _build_shell(self, parent, engine, router):
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(side="top", fill="both", expand=True)
        self.wrap = wrap
        head = tk.Frame(wrap, bg=T.BG)
        head.pack(side="top", fill="x")
        tk.Label(head, text=self.title, font=T.FONT_TITLE, fg=T.TEXT,
                 bg=T.BG).pack(side="left", padx=4, pady=4)
        body = tk.Frame(wrap, bg=T.PANEL)
        body.pack(side="top", fill="both", expand=True, padx=4, pady=(0, 4))
        self.body = body


class Refreshable:
    """统一"状态签名 → 条件重建"刷新机制（P2 重构）。

    子类实现：
      _refresh_sig(engine)  返回可哈希状态签名（变化才重建）
      _do_rebuild()         重建（树/列表）
      _after_refresh(engine, router)  可选：重建后额外更新（详情等）

    refresh() 骨架：签名变 → 重建；恒调 _after_refresh。
    替代此前散落的 _sig_prev / _state_sig 两套命名。
    """

    def _refresh_sig(self, engine):
        raise NotImplementedError

    def _do_rebuild(self):
        raise NotImplementedError

    def _after_refresh(self, engine, router):
        pass

    def refresh(self, engine, router) -> None:
        sig = self._refresh_sig(engine)
        if getattr(self, "_refresh_sig_prev", None) != sig:
            self._refresh_sig_prev = sig
            self._do_rebuild()
        self._after_refresh(engine, router)
