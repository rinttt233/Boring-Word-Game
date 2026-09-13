"""存档槽窗口（黑白灰）：读档选择 / 存档命名 / 删除，关键动作均二次确认。

设计：GUI 按钮只发命令字符串（`save` / `load`），命令路由发现没有参数时
回调本窗口 —— 控制台语义不变，GUI 得到"选择界面"。
"""
import os
import time
import tkinter as tk

from ui import theme as T
from ui.commands import (SAVE_NAME_RE, list_slots, slot_line, slot_path)
from ui.panels.base import bind_mousewheel

MODE_TITLE = {"load": "读档", "save": "存档"}


def ask_confirm(root, title: str, text: str, on_yes, yes_text="确定",
                no_text="取消") -> None:
    """黑白灰二次确认弹窗；确认后先关闭弹窗再执行 on_yes。"""
    dlg = tk.Toplevel(root)
    dlg.title(title)
    dlg.configure(bg=T.BG)
    dlg.transient(root)
    dlg.geometry("+%d+%d" % (root.winfo_rootx() + 240,
                             root.winfo_rooty() + 200))
    tk.Label(dlg, text=title, font=T.FONT_TITLE, fg=T.TEXT,
             bg=T.BG).pack(padx=24, pady=(16, 6))
    tk.Label(dlg, text=text, font=T.FONT_UI, fg=T.TEXT_SUB, bg=T.BG,
             justify="center", wraplength=340).pack(padx=24, pady=4)
    row = tk.Frame(dlg, bg=T.BG)
    row.pack(pady=12)

    def _yes(_e=None):
        dlg.destroy()
        on_yes()

    for txt, act in ((yes_text, "yes"), (no_text, "no")):
        is_yes = act == "yes"
        b = tk.Label(row, text=txt, font=T.FONT_UI,
                     fg=T.WARN_FG if is_yes else T.TEXT,
                     bg=T.ACCENT if is_yes else T.PANEL_ALT,
                     padx=16, pady=4, cursor="hand2")
        b.pack(side="left", padx=8)
        b.bind("<Button-1>", _yes if is_yes else (lambda e: dlg.destroy()))
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    try:
        dlg.grab_set()
    except tk.TclError:
        pass


class SaveLoadDialog:
    """存档槽管理窗口：mode='load' 载入；mode='save' 命名并写入。"""

    def __init__(self, root, engine, router, mode: str = "load",
                 on_done=None, on_close=None) -> None:
        self.root = root
        self.engine = engine
        self.router = router
        self.mode = "save" if mode == "save" else "load"
        self.on_done = on_done
        self.on_close = on_close
        self._closed = False
        # 打开期间暂停时间流动（避免边读边跑）
        try:
            self._was_paused = engine.clock.paused
            engine.clock.pause()
        except Exception:
            self._was_paused = True

        title = MODE_TITLE[self.mode]
        dlg = tk.Toplevel(root)
        self.dlg = dlg
        dlg.title(f"{title} · 存档槽")
        dlg.configure(bg=T.BG)
        dlg.transient(root)
        dlg.geometry("+%d+%d" % (root.winfo_rootx() + 140,
                                 root.winfo_rooty() + 120))
        dlg.minsize(560, 320)

        tk.Label(dlg, text=f"{title} · 存档槽", font=T.FONT_TITLE,
                 fg=T.TEXT, bg=T.BG).pack(anchor="w", padx=12, pady=(10, 2))

        # 存档模式：名字输入行
        self.name_var = tk.StringVar(
            value=time.strftime("slot-%Y%m%d-%H%M"))
        if self.mode == "save":
            nrow = tk.Frame(dlg, bg=T.BG)
            nrow.pack(fill="x", padx=12, pady=(2, 4))
            tk.Label(nrow, text="槽位名", font=T.FONT_UI, fg=T.TEXT_SUB,
                     bg=T.BG).pack(side="left")
            self.entry = tk.Entry(nrow, textvariable=self.name_var,
                                  font=T.FONT_MONO, bg=T.PANEL, fg=T.TEXT,
                                  insertbackground=T.TEXT, relief="flat",
                                  highlightthickness=1,
                                  highlightbackground=T.BORDER_DARK)
            self.entry.pack(side="left", fill="x", expand=True, padx=6)
            tk.Label(nrow, text="（字母/数字/_-）", font=T.FONT_SMALL,
                     fg=T.TEXT_DIM, bg=T.BG).pack(side="left")

        # 槽位列表
        wrap = tk.Frame(dlg, bg=T.BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=4)
        self.listbox = tk.Listbox(wrap, font=T.FONT_MONO, bg=T.BG,
                                  fg=T.TEXT, selectbackground=T.ACCENT,
                                  selectforeground=T.WARN_FG,
                                  highlightthickness=0, relief="flat",
                                  activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(wrap, orient="vertical",
                          command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=sb.set)
        bind_mousewheel(self.listbox)
        self.listbox.bind("<Double-Button-1>", lambda e: self._primary())
        self.listbox.bind("<<ListboxSelect>>", self._on_pick)

        # 按钮行
        brow = tk.Frame(dlg, bg=T.BG)
        brow.pack(fill="x", padx=12, pady=(2, 4))
        primary_txt = "载入" if self.mode == "load" else "保存"
        self._btn(brow, primary_txt, self._primary,
                  primary=True)
        self._btn(brow, "删除", self._delete)
        self._btn(brow, "刷新", self.refresh)
        self._btn(brow, "取消", self.close)
        self.status = tk.Label(dlg, text="", font=T.FONT_SMALL,
                               fg=T.TEXT_SUB, bg=T.BG, anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 10))

        dlg.bind("<Escape>", lambda e: self.close())
        dlg.bind("<Return>", lambda e: self._primary())
        dlg.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        try:
            dlg.grab_set()
            dlg.focus_force()
        except tk.TclError:
            pass

    # ---- 控件 ------------------------------------------------------
    def _btn(self, parent, text, cmd, primary: bool = False):
        b = tk.Label(parent, text=text, font=T.FONT_UI,
                     fg=T.WARN_FG if primary else T.TEXT,
                     bg=T.ACCENT if primary else T.PANEL_ALT,
                     padx=12, pady=3, cursor="hand2")
        b.pack(side="left", padx=(0, 6))
        b.bind("<Button-1>", lambda e: cmd())
        return b

    def _say(self, text: str, warn: bool = False) -> None:
        try:
            self.status.configure(text=text,
                                  fg=T.WARN_FG if warn else T.TEXT_SUB)
        except tk.TclError:
            pass

    # ---- 列表 ------------------------------------------------------
    def refresh(self) -> None:
        try:
            self.listbox.delete(0, "end")
        except tk.TclError:
            return
        self.slots = list_slots()
        for r in self.slots:
            self.listbox.insert("end", slot_line(r))
        if not self.slots:
            self._say("saves/ 下还没有存档。先存档，或让自动存档跑一轮。")
        else:
            self._say(f"共 {len(self.slots)} 个槽位"
                      + ("；双击槽位=载入。" if self.mode == "load"
                         else "；点槽位可覆盖它，或输入新名字。"))

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        try:
            return self.slots[sel[0]]["name"]
        except (IndexError, AttributeError):
            return None

    def _on_pick(self, _evt=None):
        """存档模式下点槽位 → 填进名字框（便于覆盖）。"""
        if self.mode == "save":
            name = self._selected_name()
            if name:
                self.name_var.set(name)

    # ---- 动作 ------------------------------------------------------
    def _primary(self) -> None:
        if self.mode == "load":
            name = self._selected_name()
            if not name:
                self._say("先选中一个槽位再载入。", warn=True)
                return
            ask_confirm(self.root, "载入存档",
                        f"载入「{name}」？\n当前未保存的进度会丢失。",
                        lambda: self._do_load(name))
            return
        name = (self.name_var.get() or "").strip()
        if not name or not SAVE_NAME_RE.fullmatch(name):
            self._say("槽位名只能包含字母/数字/_- 。", warn=True)
            return
        if os.path.exists(slot_path(name)):
            ask_confirm(self.root, "覆盖存档",
                        f"「{name}」已存在，确定覆盖？", 
                        lambda: self._do_save(name))
            return
        self._do_save(name)

    def _do_save(self, name: str) -> None:
        self.router.execute(f"save {name}")
        self.refresh()
        self._say(f"已保存到槽位「{name}」。")

    def _do_load(self, name: str) -> None:
        self.router.execute(f"load {name}")
        self.close()
        if self.on_done is not None:
            self.on_done()

    def _delete(self) -> None:
        name = self._selected_name()
        if not name:
            self._say("先选中一个槽位再删除。", warn=True)
            return

        def _really():
            path = slot_path(name)
            try:
                os.remove(path)
                self.refresh()
                self._say(f"已删除槽位「{name}」。")
            except OSError as e:
                self._say(f"删除失败: {e}", warn=True)

        ask_confirm(self.root, "删除存档",
                    f"删除槽位「{name}」？此操作不可撤销。", _really)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.dlg.grab_release()
            self.dlg.destroy()
        except tk.TclError:
            pass
        # 恢复打开前的时间流动状态
        try:
            if not self._was_paused:
                self.engine.clock.resume()
        except Exception:
            pass
        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                pass
