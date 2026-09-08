"""通用 GUI 小部件（P3a 重构：自 gui.py 拆出）。

- CollapsiblePane：四栏可折叠面板（标题条 + 内容体）。
- WarningBoard：活跃警告看板（去重/上限10/恢复事件/TTL）。
"""
import tkinter as tk

from ui import theme as T


class CollapsiblePane:
    """可折叠面板：标题条(▸/▾ 切换) + 内容体（可隐藏收起）。

    key 用于 GUI 偏好持久化（记录折叠态）。
    """

    def __init__(self, master, title: str, key: str = "") -> None:
        self.master = master
        self.collapsed = False
        self.key = key
        self.frame = tk.Frame(master, bg=T.BG)
        # 标题条（点击切换折叠）
        self.header = tk.Frame(self.frame, bg=T.PANEL)
        self.header.pack(side="top", fill="x")
        self.toggle = tk.Label(self.header, text="▾ " + title,
                               font=T.FONT_TITLE, fg=T.TEXT, bg=T.PANEL,
                               anchor="w", padx=6, pady=4, cursor="hand2")
        self.toggle.pack(side="left", fill="x", expand=True)
        self.toggle.bind("<Button-1>", lambda e: self.toggle_collapse())
        # 内容体
        self.body = tk.Frame(self.frame, bg=T.BG)
        self.body.pack(side="top", fill="both", expand=True)

    def toggle_collapse(self):
        if self.collapsed:
            self.body.pack(side="top", fill="both", expand=True)
            self.toggle.configure(text="▾ " + self.toggle.cget("text")[2:])
            self.collapsed = False
        else:
            self.body.pack_forget()
            self.toggle.configure(text="▸ " + self.toggle.cget("text")[2:])
            self.collapsed = True

    # ---- 持久化钩子 ----------------------------------------------
    def attach_prefs(self, prefs):
        """绑定 prefs：读取折叠态，写回时记录。"""
        self._prefs = prefs
        if self.key:
            collapsed = prefs.get(f"pane.{self.key}.collapsed")
            if collapsed:
                self.toggle_collapse()
        prefs.add_hooks(write_fn=self._write_prefs)

    def _write_prefs(self, prefs):
        if self.key:
            prefs.set(f"pane.{self.key}.collapsed", self.collapsed)

    def grid(self, **kw):
        self.frame.grid(**kw)
        return self


class WarningBoard:
    """活跃警告看板：去重 + 上限10 + 恢复事件/TTL 解除。

    - 每条警告按 category 去重；同 category 再次出现 → 覆盖更新。
    - 最多 10 个不同 category；满了淘汰最旧的。
    - recover 事件（recover=category）→ 移除。
    - TTL：超过 warning_ttl 秒且未恢复 → 自动移除（兜底）。
    """

    def __init__(self, engine, on_change=None, max_warns=10,
                 ttl_seconds=120.0) -> None:
        self.engine = engine
        self.on_change = on_change
        self.max = max_warns
        self.ttl = ttl_seconds
        self.warns: dict = {}   # category -> {text, since, seq}
        self._seq = 0

    def handle_log(self, payload: dict) -> None:
        level = payload.get("level", "normal")
        recov = payload.get("recover")
        if recov:
            if recov in self.warns:
                del self.warns[recov]
                self._notify()
            return
        if level not in ("warn", "danger"):
            return
        cat = payload.get("category")
        if not cat:
            return
        self._seq += 1
        self.warns[cat] = {
            "text": payload.get("text", ""),
            "since": self.engine.clock.time,
            "seq": self._seq,
            "category": cat,
        }
        # 超过上限淘汰最旧（最小 seq）
        while len(self.warns) > self.max:
            oldest = min(self.warns, key=lambda c: self.warns[c]["seq"])
            del self.warns[oldest]
        self._notify()

    def tick(self) -> None:
        """每拍：TTL 过期清除。"""
        now = self.engine.clock.time
        expired = [c for c, w in self.warns.items()
                   if now - w["since"] > self.ttl]
        if expired:
            for c in expired:
                del self.warns[c]
            self._notify()

    def order(self) -> list:
        return sorted(self.warns.values(), key=lambda w: w["seq"])

    def _notify(self):
        if self.on_change:
            self.on_change()
