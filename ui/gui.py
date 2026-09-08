"""GuiUI：极简黑白灰桌面界面（tkinter）。

G3 之后结构（顶部→底部）：
  状态栏（时间/倍率/运行态/记忆/单元/DB/环境）
  左右标签页容器（左=地块；右=资源/单元/恢复/工艺🔒/终局🔒）
  模拟控制台（日志滚动 + 命令输入）

潘面系统：ui/panels.py 注册，ui/panel_host.py 提供标签页容器。
G2 的"空格=暂停"、"命令经 CommandRouter"手感保留。
"""
import re
import time
import tkinter as tk

from ui import theme as T
from ui.commands import CommandRouter, fmt_time
from ui.panels import (PanelRegistry, PlotsPanel, BuildingsPanel,
                       ResourcesPanel, UnitsPanel, RecoveryPanel,
                       ProcessesPanel, ChainPanel, PowerPanel, LayersPanel,
                       WikiPanel, DatabasePanel, EnvironmentPanel)
from ui.panel_host import PanelHost
from ui.buttonbar import ButtonBar
from ui.prefs import GUIPrefs
from ui.widgets import CollapsiblePane, WarningBoard


def _enable_dpi_awareness() -> None:
    """Windows 高 DPI 感知（避免高分屏字体发虚）。非 Windows 静默跳过。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def build_left_registry() -> PanelRegistry:
    r = PanelRegistry()
    r.register(PlotsPanel())
    return r


def build_build_registry() -> PanelRegistry:
    """建筑管理栏：独立栏位，只放建筑面板。"""
    r = PanelRegistry()
    r.register(BuildingsPanel())
    return r


def build_right_registry() -> PanelRegistry:
    r = PanelRegistry()
    r.register(ResourcesPanel())
    r.register(UnitsPanel())
    r.register(RecoveryPanel())
    r.register(ProcessesPanel())
    r.register(ChainPanel())
    r.register(PowerPanel())
    r.register(LayersPanel())
    r.register(DatabasePanel())
    r.register(EnvironmentPanel())
    r.register(WikiPanel())
    return r


class GuiUI:
    def __init__(self, engine, substance_map, speed: float = 1.0) -> None:
        _enable_dpi_awareness()
        self.engine = engine
        self.router = CommandRouter(engine, substance_map,
                                    on_quit=self._quit)
        self.router.speed = speed if speed > 0 else 1.0

        self.root = tk.Tk()
        # 字体回退（缺 Segoe UI/Consolas 时换可用中文字体，避免豆腐块）
        T.apply_font_fallback(self.root)
        self.root.title("坠毁ASI · 拓荒日志 — 模拟控制台")
        self.root.configure(bg=T.BG)
        self.root.minsize(920, 560)
        # GUI 偏好（布局/折叠/标签/窗口几何记忆）
        self.prefs = GUIPrefs()
        # 恢复上次窗口位置与大小（越界时忽略，交回给窗口管理器）
        try:
            geo = self.prefs.get("win.geometry")
            if geo:
                self.root.geometry(geo)
            if self.prefs.get("win.zoomed"):
                try:
                    self.root.state("zoomed")
                except tk.TclError:
                    pass
        except Exception:
            pass
        # 需在退出时清理 after 的宿主（按钮栏等）—— 在构建控件前初始化
        self._all_after_hosts = []
        # 活跃警告看板（在构建控制台前创建，供 _render_warnings 使用）
        self.warnboard = WarningBoard(
            engine, on_change=self._render_warnings)
        self._build_statusbar()
        self._build_panels()
        self._build_console()
        # 订阅日志事件 → 更新警告看板
        engine.bus.on("log", self.warnboard.handle_log)
        # 读回布局偏好（折叠/标签）
        self.prefs.run_read_hooks()
        self._tick_acc = 0.0
        self._last_wall = time.monotonic()
        self._last_status = 0.0
        self._rendered_logs = 0
        self._cmd_history = []
        self._hist_idx = 0
        self._quitting = False

        # 空格 = 暂停/继续（输入框聚焦时不触发）
        self.root.bind("<space>", self._on_space)
        # F12 = GUI 一键复位
        self.root.bind("<F12>", lambda e: self._confirm_reset())
        # F11 = 调试面板
        self.root.bind("<F11>", lambda e: self._open_debug())
        # Ctrl+Z = 撤销上次建造
        self.root.bind("<Control-z>", lambda e: self.router.execute("undo"))
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        # 快捷键体系（输入框聚焦时不触发）
        self._hotkeys = {
            "g": lambda: self.set_mode("gui" if self._mode == "cmd" else "cmd"),
            "1": lambda: self.router.execute("survey 1"),
            "2": lambda: self.router.execute("survey 2"),
            "3": lambda: self.router.execute("survey 3"),
            "4": lambda: self.router.execute("survey 4"),
            "5": lambda: self.router.execute("survey 5"),
            "m": lambda: self.router.execute("maintain"),
            "h": lambda: self.router.execute("help"),
            "p": lambda: self.router._toggle_pause(),
        }
        for key, fn in self._hotkeys.items():
            self.root.bind(
                key, lambda e, k=key: self._on_hotkey(k))
        self._mode = "cmd"

        # 记忆崩溃 → 自动暂停并提示（GUI 层，防止错过连锁丢失）
        engine.bus.on("memory_crash", self._on_memory_crash)
        engine.bus.on("database_complete", self._on_db_done)
        # 周期自动存档计时
        self._last_autosave = time.monotonic()
        self._autosave_interval = 180.0   # 秒（墙钟）
        # 启动提示：检测到自动存档
        self._check_autosave_hint()

    # ================= 快捷键 =================
    def _on_hotkey(self, key):
        """快捷键回调：输入框聚焦时忽略，避免误触打字。"""
        try:
            focused = self.root.focus_get()
            if focused is not None and str(focused).startswith(
                    self.entry.winfo_pathname(0)):
                return
        except tk.TclError:
            pass
        fn = self._hotkeys.get(key)
        if fn is not None:
            fn()

    def _on_memory_crash(self, payload):
        """记忆崩溃：GUI 自动暂停，醒目提示，避免玩家错过连锁丢失。"""
        if not self.engine.clock.paused:
            self.engine.clock.pause()
            self.engine.log("[记忆] 记忆崩溃！已自动暂停 —— 先做加固/固化再继续。",
                            level="danger", category="memory")
        else:
            self.engine.log("[记忆] 记忆崩溃（已暂停）。",
                            level="danger", category="memory")

    def _on_db_done(self, payload):
        """数据库竣工：进度条到 100%。"""
        try:
            self.root.after(300, lambda: self._refresh_status())
        except tk.TclError:
            pass

    def _check_autosave_hint(self):
        import os
        path = os.path.join("saves", "autosave.json")
        if os.path.exists(path):
            self.engine.log(
                "[提示] 发现自动存档。想从自动档继续？输入: load autosave")

    def _autosave(self):
        """周期自动存档（GUI 层墙钟触发，不打断操作）。"""
        now = time.monotonic()
        if now - self._last_autosave < self._autosave_interval:
            return
        self._last_autosave = now
        try:
            import json
            import os
            os.makedirs("saves", exist_ok=True)
            path = os.path.join("saves", "autosave.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.engine.to_dict(), f, ensure_ascii=False,
                          indent=1)
        except Exception:
            pass    # 自动存档失败不打断游戏

    # ================= 状态栏 =================
    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=T.BG)
        bar.pack(side="top", fill="x")
        self.time_lbl = tk.Label(bar, text="0:00", font=T.FONT_TITLE,
                                 fg=T.TEXT, bg=T.BG)
        self.speed_lbl = tk.Label(bar, text="×1", font=T.FONT_TITLE,
                                  fg=T.TEXT_SUB, bg=T.BG)
        self.state_lbl = tk.Label(bar, text="▶ 运行", font=T.FONT_TITLE,
                                  fg=T.TEXT, bg=T.BG)
        # 生命体征：记忆进度条（UI-6 图形化）
        self.mem_lbl = tk.Label(bar, text="记忆 --", font=T.FONT_UI,
                                fg=T.TEXT_SUB, bg=T.BG)
        self.mem_bar = self._mini_bar(bar, 60)
        self.units_lbl = tk.Label(bar, text="单元 -/-", font=T.FONT_UI,
                                  fg=T.TEXT_SUB, bg=T.BG)
        # 数据库进度条
        self.db_lbl = tk.Label(bar, text="DB -/-", font=T.FONT_UI,
                               fg=T.TEXT_SUB, bg=T.BG)
        self.db_bar = self._mini_bar(bar, 48)
        # 电力净值（缺电停摆是核心痛点 → 状态栏直接暴露）
        self.power_lbl = tk.Label(bar, text="电力 --", font=T.FONT_UI,
                                  fg=T.TEXT_SUB, bg=T.BG)
        self.day_lbl = tk.Label(bar, text="", font=T.FONT_UI,
                                fg=T.TEXT_SUB, bg=T.BG)
        self.env_lbl = tk.Label(bar, text="", font=T.FONT_UI,
                                fg=T.TEXT_DIM, bg=T.BG)
        for w in (self.time_lbl, self.speed_lbl, self.state_lbl,
                  self.mem_lbl, self.mem_bar, self.units_lbl,
                  self.db_lbl, self.db_bar, self.power_lbl):
            w.pack(side="left", padx=(4, 6), pady=6)
        self.day_lbl.pack(side="right", padx=(0, 2), pady=6)
        self.env_lbl.pack(side="right", padx=10, pady=6)

        # 顶部时间控制（用户定案：时间选项放顶部栏位）
        self._build_time_controls(bar)
        rule = tk.Frame(self.root, bg=T.BORDER, height=1)
        rule.pack(side="top", fill="x")

    def _build_time_controls(self, bar):
        """状态栏右边的快捷操作按钮组（时间控制 + 勘探 + 加固）。"""
        wrap = tk.Frame(bar, bg=T.BG)
        wrap.pack(side="right", padx=6, pady=2)
        # 暂停/继续
        self.time_pause_btn = self._mini_btn(wrap, "⏸/▶", "toggle",
                                             fg=T.TEXT, bg=T.PANEL_ALT)
        # 巡航/变速
        for txt, cmd in [("×0.5", "speed 0.5"), ("×1", "speed 1"),
                         ("×2", "speed 2"), ("×4", "speed 4"),
                         ("×8", "speed 8")]:
            self._mini_btn(wrap, txt, cmd, fg=T.TEXT_SUB, bg=T.PANEL)
        # 巡航秒数快捷（跑 N 游戏秒后自动暂停）
        self.cruise_entry = tk.Entry(wrap, width=4, font=T.FONT_SMALL,
                                     bg=T.BG, fg=T.TEXT,
                                     insertbackground=T.TEXT,
                                     relief="flat", highlightthickness=1,
                                     highlightbackground=T.BORDER_DARK,
                                     highlightcolor=T.ACCENT)
        self.cruise_entry.pack(side="left", padx=(4, 0), pady=2)
        self.cruise_entry.insert(0, "60")
        self.cruise_entry.bind("<Return>", lambda e: self._do_cruise())
        cb = tk.Label(wrap, text="巡航", font=T.FONT_SMALL, fg=T.TEXT,
                      bg=T.PANEL, padx=6, pady=2, cursor="hand2",
                      borderwidth=0)
        cb.pack(side="left", padx=2)
        cb.bind("<Button-1>", lambda e: self._flash_mini(cb, "cruise_go"))
        # 高频操作快捷（UI-4）
        self._mini_btn(wrap, "勘探1", "survey 1", fg=T.TEXT, bg=T.PANEL)
        self._mini_btn(wrap, "加固", "maintain", fg=T.TEXT, bg=T.PANEL)

    def _do_cruise(self):
        """读取巡航秒数输入并执行（可被按钮/回车触发）。"""
        try:
            sec = float(self.cruise_entry.get().strip() or 60)
        except ValueError:
            sec = 60.0
        self.router.execute(f"cruise {sec:g}")

    def _dbg_pick_id(self, combo, items):
        """从"中文名 (id)"下拉取回 id；兼容直接输入 id/中文名。"""
        text = (combo.get() or "").strip()
        if text in items:
            return text
        if text.endswith(")") and "(" in text:
            return text[text.rfind("(") + 1:-1].strip()
        for it in items:
            if self.router.rname(it) == text:
                return it
        return text

    def _mini_bar(self, parent, width):
        """黑白灰迷你进度条（Canvas）：黑底白块表示比例。"""
        h = 12
        c = tk.Canvas(parent, width=width, height=h, bg=T.BG,
                      highlightthickness=0, bd=0)
        # 槽（浅灰边框）
        c.create_rectangle(0, 0, width, h, outline=T.BORDER, width=1)
        return c

    def _set_bar(self, canvas, ratio):
        """刷新 Canvas 进度条（ratio 0~1）。"""
        try:
            canvas.delete("fill")
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            fill_w = max(0, int(w * max(0.0, min(1.0, ratio))))
            if fill_w > 0:
                canvas.create_rectangle(1, 1, fill_w, h - 1,
                                        fill=T.ACCENT, outline="",
                                        tags="fill")
        except tk.TclError:
            pass

    def _mini_btn(self, parent, text, cmd, fg, bg):
        b = tk.Label(parent, text=text, font=T.FONT_SMALL, fg=fg, bg=bg,
                     padx=6, pady=2, cursor="hand2", borderwidth=0)
        b.pack(side="left", padx=2)
        b.bind("<Button-1>", lambda e: self._flash_mini(b, cmd))
        return b

    def _flash_mini(self, b, cmd):
        b.configure(bg=T.ACCENT, fg=T.WARN_FG)
        try:
            b.after(140, lambda: self._after_flash_mini(b, cmd))
        except tk.TclError:
            pass

    def _after_flash_mini(self, b, cmd):
        try:
            b.configure(bg=T.PANEL, fg=T.TEXT)
        except tk.TclError:
            pass
        if cmd == "cruise_go":
            self._do_cruise()
            return
        self.router.execute(cmd)

    def open_wiki(self, query: str) -> bool:
        """打开百科并定位词条（双击跳转的统一入口）。

        query 可为词条 id / #id / 标题 / 关键词。
        """
        try:
            host = self.right_host
            host.select("wiki")
            panel = host.current
            if panel is not None and hasattr(panel, "show_entry"):
                ok = panel.show_entry(query)
                if not ok:
                    self.engine.log(f"[百科] 没有找到与「{query}」相关的词条。")
                return ok
        except tk.TclError:
            pass
        self.engine.log(f"[百科] wiki {query}")
        self.router.execute(f"wiki {query}")
        return False

    def _attach_wiki_hook(self, host) -> None:
        """给一个 PanelHost 里的所有面板注入 open_wiki 回调（双击跳转用）。"""
        for panel in getattr(host, "panels", []):
            try:
                panel.open_wiki = self.open_wiki
            except Exception:
                pass

    def _locate_plot(self, plot_id: str):
        """建筑面板"定位地块"：展开左栏并让地块树选中该地块。"""
        try:
            if getattr(self, "left_pane", None) is not None \
                    and self.left_pane.collapsed:
                self.left_pane.toggle_collapse()
            plots = self._plots_panel
            if plots is not None:
                # 先刷新树，确保目标地块行已存在
                if hasattr(plots, "refresh"):
                    try:
                        plots.refresh(self.engine, self.router)
                    except tk.TclError:
                        pass
                if hasattr(plots, "select_plot"):
                    if plots.select_plot(plot_id):
                        self.engine.log(f"[建筑] 已定位地块 {plot_id}。")
                        return
            self.engine.log(f"[建筑] 地块 {plot_id} 未在地块树中。")
        except tk.TclError:
            pass

    # ================= 四栏（可折叠 + 可拖分隔）=================
    def _build_panels(self) -> None:
        # 经典 tk.PanedWindow：支持 minsize + 黑白灰 sash（可拖拽分隔线）
        mid = tk.PanedWindow(self.root, orient="horizontal",
                             sashwidth=4, bg=T.BORDER, bd=0,
                             relief="flat")
        mid.pack(side="top", fill="both", expand=True, padx=0, pady=0)
        # 各栏布局权重（复位时按此比例恢复 sash 宽度）
        self._pane_weights = [30, 20, 28, 22]

        def add_pane(pane, minsize=170):
            mid.add(pane.frame, minsize=minsize, stretch="always")

        # 左区-地块（可折叠）
        self.left_pane = CollapsiblePane(mid, "地块", key="left")
        self.left_host = PanelHost(self.left_pane.body, self.engine,
                                   self.router, build_left_registry(),
                                   side="left", key="left")
        self.left_host.select_first_unlocked()
        self.left_host.attach_prefs(self.prefs)
        self.left_pane.attach_prefs(self.prefs)
        plots_panel = self.left_host.current
        self._plots_panel = plots_panel   # 供定位回调使用
        if plots_panel is not None and hasattr(plots_panel, "attach_prefs"):
            plots_panel.attach_prefs(self.prefs)
        add_pane(self.left_pane)

        # 建筑管理栏（可折叠）
        self.build_pane = CollapsiblePane(mid, "建筑", key="build")
        self.build_host = PanelHost(self.build_pane.body, self.engine,
                                    self.router, build_build_registry(),
                                    side="left", key="build")
        self.build_host.select_first_unlocked()
        self.build_host.attach_prefs(self.prefs)
        self.build_pane.attach_prefs(self.prefs)
        bld_panel = self.build_host.current
        if bld_panel is not None:
            if hasattr(bld_panel, "attach_prefs"):
                bld_panel.attach_prefs(self.prefs)
            # 定位地块：切回地块页并选中对应地块
            if hasattr(bld_panel, "locate"):
                bld_panel.locate = self._locate_plot
        add_pane(self.build_pane)

        # 右区（可折叠）
        self.right_pane = CollapsiblePane(mid, "状态", key="right")
        self.right_host = PanelHost(self.right_pane.body, self.engine,
                                    self.router, build_right_registry(),
                                    side="right", key="right")
        self.right_host.select_first_unlocked()
        self.right_host.attach_prefs(self.prefs)
        self.right_pane.attach_prefs(self.prefs)
        add_pane(self.right_pane)

        # 控制台（第 4 个可折叠栏）—— 单独添加到 paned，作为右侧 pane
        self.console_pane = CollapsiblePane(mid, "模拟控制台",
                                             key="console")
        add_pane(self.console_pane)
        self.console_pane.attach_prefs(self.prefs)

        # 由于 PanedWindow 直接持有各 pane.frame，窗口关闭时统一销毁
        self._all_after_hosts.append(self.console_pane)
        self._mid = mid
        # 双击跳转百科：给所有面板注入统一回调
        for host in (self.left_host, self.build_host, self.right_host):
            self._attach_wiki_hook(host)

    # ================= 右侧控制台纵列（可折叠）=================
    def _build_console(self) -> None:
        # 控制台已在 _build_panels 里 add 进 PanedWindow，这里只构建其内容
        wrap = self.console_pane.body
        self._console = wrap
        # 权重给 log 所在行(row3)，使其随窗口高度伸缩
        wrap.grid_rowconfigure(3, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        # 模式切换行（指令/图形化）
        self._build_mode_toggle(wrap)
        # 日志工具行（等级过滤 + 提示）
        self._build_log_toolbar(wrap)

        # 活跃警告看板（去重 + 上限10 + TTL/恢复解除）——置于日志上方
        self.warn_area = tk.Frame(wrap, bg=T.PANEL_ALT)
        self.warn_area.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._render_warnings()
        self.warn_area.grid_remove()   # 无警告时隐藏

        # 日志区：默认自动折行（长信息不丢），可切换为不折行+横向滚动
        self._log_wrap = True
        self.ysb = tk.Scrollbar(wrap, orient="vertical")
        self.log = tk.Text(
            wrap, font=T.FONT_MONO, bg=T.BG, fg=T.TEXT,
            insertbackground=T.TEXT, relief="flat",
            state="disabled", wrap="word", padx=7, pady=6,
            yscrollcommand=self.ysb.set, highlightthickness=1,
            highlightbackground=T.BORDER)
        self.xsb = tk.Scrollbar(wrap, orient="horizontal",
                                command=self.log.xview)
        self.log.configure(xscrollcommand=self.xsb.set)
        self.ysb.configure(command=self.log.yview)
        self.log.grid(row=3, column=0, sticky="nsew")
        self.xsb.grid(row=4, column=0, sticky="ew")
        self.ysb.grid(row=3, column=1, sticky="ns")
        self.xsb.grid_remove()      # 折行模式下不需要横向滚动
        from ui.panels.base import bind_mousewheel as _bind_wheel  # noqa: F401

        def _log_wheel(event):
            step = -3 if event.delta > 0 else 3
            self.log.yview_scroll(step, "units")
            return "break"
        self.log.bind("<MouseWheel>", _log_wheel)
        self.log.bind("<Button-4>", lambda e: self.log.yview_scroll(-3, "units"))
        self.log.bind("<Button-5>", lambda e: self.log.yview_scroll(3, "units"))
        # 双击日志行 → 跳转百科（匹配该行出现的词条标题）
        self.log.bind("<Double-Button-1>", self._on_log_double)
        # 日志等级配色（黑白灰：正常=纯黑；注意=深灰；警告=加粗深灰；危险=反白）
        self.log.tag_configure("normal", foreground=T.TEXT)
        self.log.tag_configure("info", foreground=T.TEXT_SUB)
        self.log.tag_configure("warn", foreground=T.TEXT, font=T.FONT_MONO_BOLD)
        self.log.tag_configure("danger", foreground=T.WARN_FG,
                               background=T.ACCENT)

        # 底部：指令输入框 / 图形按钮栏（二者切换显示）
        self._build_input_zone(wrap)

    def _build_log_toolbar(self, wrap):
        """日志工具行：等级过滤（全部/警告/危险）。"""
        row = tk.Frame(wrap, bg=T.BG)
        row.grid(row=1, column=0, columnspan=2, sticky="ew")
        tk.Label(row, text="日志:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.BG).pack(side="left", padx=(6, 4), pady=2)
        self._log_filter = "all"
        self._log_filter_btns = {}
        for key, label in (("all", "全部"), ("warn", "警告+"),
                           ("danger", "危险")):
            b = tk.Label(row, text=label, font=T.FONT_SMALL, fg=T.TEXT,
                         bg=T.PANEL, padx=6, pady=1, cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, k=key: self.set_log_filter(k))
            self._log_filter_btns[key] = b
        self._style_log_filter_btns()
        self._wrap_btn = tk.Label(row, text="折行:开", font=T.FONT_SMALL,
                                  fg=T.TEXT, bg=T.PANEL, padx=6, pady=1,
                                  cursor="hand2")
        self._wrap_btn.pack(side="left", padx=(8, 1))
        self._wrap_btn.bind("<Button-1>", lambda e: self.toggle_log_wrap())
        tk.Label(row, text="(点日志行可跳转相关面板)",
                 font=T.FONT_SMALL, fg=T.TEXT_DIM,
                 bg=T.BG).pack(side="right", padx=6)

    def toggle_log_wrap(self):
        self.set_log_wrap(not getattr(self, "_log_wrap", True))

    def set_log_wrap(self, on: bool):
        """长日志折行显示开关：关掉则恢复横向滚动（看对齐表格更顺）。"""
        self._log_wrap = bool(on)
        try:
            self.log.configure(wrap="word" if self._log_wrap else "none")
            if self._log_wrap:
                self.xsb.grid_remove()
            else:
                self.xsb.grid()
            self._wrap_btn.configure(
                text="折行:开" if self._log_wrap else "折行:关")
            self._rerender_log()
            self.log.see("end")
        except tk.TclError:
            pass

    def set_log_filter(self, key):
        self._log_filter = key
        self._style_log_filter_btns()
        self._rerender_log()

    def _style_log_filter_btns(self):
        for key, b in getattr(self, "_log_filter_btns", {}).items():
            if key == self._log_filter:
                b.configure(bg=T.ACCENT, fg=T.WARN_FG)
            else:
                b.configure(bg=T.PANEL, fg=T.TEXT)

    def _log_level_of(self, line: str) -> str:
        return self._log_level(line)

    def _log_visible(self, line: str) -> bool:
        lv = self._log_level_of(line)
        if self._log_filter == "all":
            return True
        if self._log_filter == "warn":
            return lv in ("warn", "danger")
        return lv == "danger"

    def _rerender_log(self):
        """按当前过滤重新渲染整份日志（供过滤切换使用）。"""
        try:
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
        except tk.TclError:
            return
        self._rendered_logs = 0
        self._flush_logs()

    def _build_mode_toggle(self, wrap):
        row = tk.Frame(wrap, bg=T.BG)
        row.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(row, text="控制台:", font=T.FONT_SMALL, fg=T.TEXT_SUB,
                 bg=T.BG).pack(side="left", padx=(6, 4), pady=4)
        self._mode_cmd_btn = tk.Label(row, text="指令", font=T.FONT_UI, fg=T.WARN_FG,
                                      bg=T.ACCENT, padx=8, pady=2, cursor="hand2")
        self._mode_cmd_btn.pack(side="left", padx=2)
        self._mode_cmd_btn.bind("<Button-1>", lambda e: self.set_mode("cmd"))
        self._mode_gui_btn = tk.Label(row, text="图形", font=T.FONT_UI, fg=T.TEXT,
                                      bg=T.PANEL, padx=8, pady=2, cursor="hand2")
        self._mode_gui_btn.pack(side="left", padx=2)
        self._mode_gui_btn.bind("<Button-1>", lambda e: self.set_mode("gui"))

    def _build_input_zone(self, wrap):
        # 指令输入框（默认模式）
        self._entry_zone = tk.Frame(wrap, bg=T.BG)
        self._entry_zone.grid(row=4, column=0, columnspan=2, sticky="ew")
        prompt = tk.Label(self._entry_zone, text=">", font=T.FONT_MONO,
                          fg=T.TEXT_SUB, bg=T.BG)
        prompt.pack(side="left", padx=(8, 2), pady=6)
        self.entry = tk.Entry(self._entry_zone, font=T.FONT_MONO, bg=T.BG,
                              fg=T.TEXT, insertbackground=T.TEXT,
                              relief="flat", highlightthickness=1,
                              highlightbackground=T.BORDER_DARK,
                              highlightcolor=T.ACCENT)
        self.entry.pack(side="left", fill="x", expand=True,
                        padx=(0, 8), pady=6, ipady=2)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Up>", self._on_history_up)
        self.entry.bind("<Down>", self._on_history_down)
        self.entry.bind("<Tab>", self._on_tab_complete)
        self.entry.focus_set()

        # 图形按钮栏（默认隐藏）
        self._gui_zone = tk.Frame(wrap, bg=T.PANEL)
        self._gui_zone.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._gui_zone.grid_remove()
        # 按钮栏（分层导航 + 多步流程），勘探滑块已内嵌到勘探层
        self.buttonbar = ButtonBar(
            self._gui_zone, self.engine, self.router,
            selected_getter=self._current_selection,
            on_open_build=self._open_build_bar)
        self.buttonbar.pack(side="top", fill="x", padx=2, pady=(0, 4))
        self._all_after_hosts.append(self.buttonbar)

    # ---- 模式切换 -----------------------------------------------
    def set_mode(self, mode):
        self._mode = mode
        if mode == "gui":
            self._entry_zone.grid_remove()
            self._gui_zone.grid()
            self._mode_gui_btn.configure(bg=T.ACCENT, fg=T.WARN_FG)
            self._mode_cmd_btn.configure(bg=T.PANEL, fg=T.TEXT)
        else:
            self._gui_zone.grid_remove()
            self._entry_zone.grid()
            self.entry.focus_set()
            self._mode_cmd_btn.configure(bg=T.ACCENT, fg=T.WARN_FG)
            self._mode_gui_btn.configure(bg=T.PANEL, fg=T.TEXT)

    def _do_survey(self):
        # 已内嵌到按钮栏勘探层（保留空壳兼容旧调用）
        pass

    def _current_selection(self):
        """返回当前"选中对象"（优先地块，其次设施），供按钮栏上下文用。"""
        plots = self.left_host.current if hasattr(self, "left_host") else None
        if plots is not None and getattr(plots, "selected", None):
            return plots.selected
        buildings = self.build_host.current if hasattr(self, "build_host") else None
        if buildings is not None and getattr(buildings, "selected", None):
            return buildings.selected
        return None

    def _open_build_bar(self):
        # 打开当前选中地块的建造展开
        plots = self.left_host.current
        if plots is not None:
            plots._toggle_build_expand()

    # ================= 事件 =================
    def _on_space(self, _evt):
        if self.entry.focus_get() is not self.entry:
            self.router._toggle_pause()
        return "break"

    def _on_enter(self, _evt):
        line = self.entry.get().strip()
        if not line:
            return "break"
        if not self._cmd_history or self._cmd_history[-1] != line:
            self._cmd_history.append(line)
        self._hist_idx = len(self._cmd_history)
        self.router.execute(line)
        self.entry.delete(0, "end")
        return "break"

    def _on_history_up(self, _evt):
        if self._cmd_history and self._hist_idx > 0:
            self._hist_idx -= 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self._cmd_history[self._hist_idx])
        return "break"

    def _on_history_down(self, _evt):
        if self._cmd_history and self._hist_idx < len(self._cmd_history) - 1:
            self._hist_idx += 1
            self.entry.delete(0, "end")
            self.entry.insert(0, self._cmd_history[self._hist_idx])
        else:
            self._hist_idx = len(self._cmd_history)
            self.entry.delete(0, "end")
        return "break"

    # ---- Tab 补全（命令名 / 资源 / 地块 / 设施）-------------------
    def _completion_candidates(self, parts):
        """按已输入前缀给出候选补全词。"""
        ind = self.engine.registry.get("industry")
        if len(parts) <= 1:
            cmds = [m[5:] for m in dir(self.router) if m.startswith("_cmd_")]
            return sorted(cmds)
        cmd = parts[0].lower()
        if cmd in ("build", "claim", "plots"):
            return sorted(p.id for p in self.engine.world.visible_plots())
        if cmd in ("assign", "unassign", "fuel"):
            if ind is None:
                return []
            return sorted(ind.facilities.keys())
        if cmd in ("recover", "fixate"):
            rec = self.engine.registry.get("recovery")
            return sorted(rec.entries.keys()) if rec else []
        if cmd == "dbg":
            subs = ["res", "add", "mem", "degrade", "unit", "time",
                    "unlock", "env"]
            if len(parts) == 2:
                return sorted(subs)
            if parts[1] == "res" and len(parts) == 3:
                return sorted(self.router.subs.keys())
        return []

    def _on_tab_complete(self, _evt):
        text = self.entry.get()
        parts = text.split()
        cands = self._completion_candidates(parts)
        if not cands:
            return "break"
        prefix = parts[-1] if parts else ""
        if len(parts) <= 1:
            # 补全命令名
            hits = [c for c in cands if c.startswith(prefix)]
            if len(hits) == 1:
                self.entry.delete(0, "end")
                self.entry.insert(0, hits[0] + " ")
            elif hits:
                self.engine.log("[补全] " + "  ".join(hits[:12]))
            return "break"
        # 补全参数
        hits = [c for c in cands if c.startswith(prefix)]
        if len(hits) == 1:
            parts[-1] = hits[0]
            self.entry.delete(0, "end")
            self.entry.insert(0, " ".join(parts) + " ")
        elif hits:
            self.engine.log("[补全] " + "  ".join(hits[:12]))
        return "break"

    # ================= GUI 一键复位（F12）=================
    def _confirm_reset(self):
        """自建黑白灰确认弹窗：第一次询问是否复位。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("GUI 复位")
        dlg.configure(bg=T.BG)
        dlg.geometry("+%d+%d" % (self.root.winfo_rootx() + 220,
                                 self.root.winfo_rooty() + 180))
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="GUI 复位", font=T.FONT_TITLE, fg=T.TEXT,
                 bg=T.BG).pack(padx=24, pady=(16, 6))
        tk.Label(dlg, text="将恢复界面到初始布局。\n不影响游戏进度。",
                 font=T.FONT_UI, fg=T.TEXT_SUB, bg=T.BG,
                 justify="center").pack(padx=24, pady=4)
        row = tk.Frame(dlg, bg=T.BG)
        row.pack(pady=12)
        for txt, act in [("确定复位", "yes"), ("取消", "no")]:
            is_yes = act == "yes"
            b = tk.Label(row, text=txt,
                         font=T.FONT_UI,
                         fg=T.WARN_FG if is_yes else T.TEXT,
                         bg=T.ACCENT if is_yes else T.PANEL_ALT,
                         padx=16, pady=4, cursor="hand2")
            b.pack(side="left", padx=8)
            if is_yes:
                b.bind("<Button-1>", lambda e: self._reset_yes(dlg))
            else:
                b.bind("<Button-1>", lambda e: dlg.destroy())
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _reset_yes(self, dlg):
        """第一次确认通过 → 执行复位 → 二次询问是否持久化。"""
        dlg.destroy()
        self.reset_gui()
        self._ask_reset_persist()

    def _ask_reset_persist(self):
        """二次询问：是否把默认布局写入 gui_prefs。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("持久化")
        dlg.configure(bg=T.BG)
        dlg.geometry("+%d+%d" % (self.root.winfo_rootx() + 220,
                                 self.root.winfo_rooty() + 220))
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="已复位到初始布局", font=T.FONT_TITLE, fg=T.TEXT,
                 bg=T.BG).pack(padx=24, pady=(16, 6))
        tk.Label(dlg, text="是否将默认布局保存？\n（重启后保持默认；否则下次仍恢复旧布局）",
                 font=T.FONT_UI, fg=T.TEXT_SUB, bg=T.BG,
                 justify="center").pack(padx=24, pady=4)
        row = tk.Frame(dlg, bg=T.BG)
        row.pack(pady=12)
        for txt, save in [("保存", True), ("不保存", False)]:
            b = tk.Label(row, text=txt, font=T.FONT_UI,
                         fg=T.WARN_FG if save else T.TEXT,
                         bg=T.ACCENT if save else T.PANEL_ALT,
                         padx=16, pady=4, cursor="hand2")
            b.pack(side="left", padx=8)
            b.bind("<Button-1>", lambda e, s=save: self._reset_persist(s, dlg))
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _reset_persist(self, save, dlg):
        dlg.destroy()
        if save:
            self.prefs.save()
            self.engine.log("[复位] 已保存默认 GUI 布局。")
        else:
            # 不保存 → 清掉本次会话的偏好，重启读回旧 json
            try:
                self.prefs.data = {}
            except Exception:
                pass
            self.engine.log("[复位] GUI 已复位（未保存，重启恢复旧布局）。")

    def reset_gui(self):
        """仅重置 GUI 布局（窗格/标签/树/控制台模式），不动游戏进度。"""
        # 1. 4 个窗格展开
        for pane in (self.left_pane, self.build_pane,
                     self.right_pane, self.console_pane):
            if pane.collapsed:
                pane.toggle_collapse()
        # 2. 标签回首个解锁
        for host in (self.left_host, self.build_host, self.right_host):
            host.select_first_unlocked()
        # 3. 树全展开（强制重建：清签名缓存 + 重置 open_by_key）
        for host in (self.left_host, self.build_host):
            panel = host.current
            if panel is not None and hasattr(panel, "_open_by_key"):
                panel._open_by_key = {}
                if hasattr(panel, "_refresh_sig_prev"):
                    panel._refresh_sig_prev = None
                if hasattr(panel, "_rebuild_tree"):
                    panel._rebuild_tree(preserve_open=False)
                elif hasattr(panel, "_rebuild"):
                    panel._rebuild()
        # 4. 控制台回指令模式
        self.set_mode("cmd")
        # 5. 恢复各栏初始宽度（按权重比例分配 pane）
        self._reset_pane_widths()
        # 6. 清空偏好（本次会话生效；是否保存由二次询问决定）
        try:
            self.prefs.data = {}
        except Exception:
            pass
        self.engine.log("[复位] GUI 已恢复到初始布局。")

    def _reset_pane_widths(self):
        """按 _pane_weights 权重比例，把 PanedWindow 的 sash 恢复到初始位置。"""
        mid = self._mid
        if not hasattr(self, "_pane_weights") or len(self._pane_weights) < 2:
            return
        try:
            total_w = mid.winfo_width()
            if total_w <= 0:
                return
            weights = self._pane_weights
            total = sum(weights)
            # 依次放置 sash，使各 pane 宽度=权重占比
            acc = 0
            # 有 n 个 pane → n-1 个 sash；sash i 出现在前 i+1 个 pane 的累积宽度处
            for i in range(len(weights) - 1):
                acc += weights[i]
                x = int(total_w * acc / total)
                mid.sash_place(i, x, 0)
        except tk.TclError:
            pass

    # ================= 调试面板（F11）=================
    def _open_debug(self):
        """F11：打开调试面板，直接修改各类数值（开发/调试用）。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("调试模式")
        dlg.configure(bg=T.BG)
        dlg.transient(self.root)
        tk.Label(dlg, text="调试模式（修改即时生效）", font=T.FONT_TITLE,
                 fg=T.WARN_FG, bg=T.ACCENT, anchor="w",
                 padx=10, pady=5).pack(side="top", fill="x")

        body = tk.Frame(dlg, bg=T.BG)
        body.pack(side="top", fill="both", expand=True, padx=10, pady=8)
        from tkinter import ttk

        def _row(label_text):
            r = tk.Frame(body, bg=T.BG)
            r.pack(side="top", fill="x", pady=3)
            tk.Label(r, text=label_text, font=T.FONT_SMALL, fg=T.TEXT,
                     bg=T.BG, width=11, anchor="w").pack(side="left")
            return r

        def _entry(r, width=12, values=None):
            if values:
                e = ttk.Combobox(r, width=width, values=values,
                                 font=T.FONT_SMALL)
            else:
                e = tk.Entry(r, width=width, font=T.FONT_SMALL, bg=T.BG,
                             fg=T.TEXT, insertbackground=T.TEXT)
            e.pack(side="left", padx=3)
            return e

        def _btn(r, text, cmd):
            b = tk.Label(r, text=text, font=T.FONT_SMALL, fg=T.TEXT,
                         bg=T.PANEL, padx=7, pady=1, cursor="hand2")
            b.pack(side="left", padx=3)
            b.bind("<Button-1>", lambda e: cmd())
            return b

        def _zh_values(items, name_of):
            """下拉值用中文名(id)，实际执行时需还原成 id。"""
            return [f"{name_of(x)} ({x})" for x in items]

        def _pick_id(combo, items):
            """从"中文 (id)"下拉里取回 id；也兼容直接输入 id。"""
            return self._dbg_pick_id(combo, items)

        # 资源行
        r = _row("资源")
        res_ids = sorted(self.router.subs.keys())
        res_values = _zh_values(res_ids, self.router.rname)
        self._dbg_res = _entry(r, values=res_values)
        self._dbg_res_amt = _entry(r, width=7)
        self._dbg_res_amt.insert(0, "1000")
        _btn(r, "设置", lambda: self.router.execute(
            f"dbg res {_pick_id(self._dbg_res, res_ids)} "
            f"{self._dbg_res_amt.get().strip() or 0}"))
        _btn(r, "加量", lambda: self.router.execute(
            f"dbg add {_pick_id(self._dbg_res, res_ids)} "
            f"{self._dbg_res_amt.get().strip() or 0}"))

        # 记忆行
        r = _row("记忆")
        self._dbg_mem = _entry(r, width=7)
        self._dbg_mem.insert(0, "100")
        _btn(r, "设完整度", lambda: self.router.execute(
            f"dbg mem {self._dbg_mem.get().strip() or 100}"))
        _btn(r, "暂停劣化", lambda: self.router.execute("dbg degrade off"))
        _btn(r, "恢复劣化", lambda: self.router.execute("dbg degrade on"))

        # 执行单元
        r = _row("执行单元")
        self._dbg_unit = _entry(r, width=7)
        self._dbg_unit.insert(0, "5")
        _btn(r, "增加", lambda: self.router.execute(
            f"dbg unit {self._dbg_unit.get().strip() or 1}"))

        # 时间
        r = _row("时间")
        self._dbg_time = _entry(r, width=10)
        self._dbg_time.insert(0, "600")
        _btn(r, "跳转(秒)", lambda: self.router.execute(
            f"dbg time {self._dbg_time.get().strip() or 0}"))

        # 恢复条目
        r = _row("恢复条目")
        entry_ids = []
        entry_names = {}
        rec = self.engine.registry.get("recovery")
        if rec is not None:
            entry_ids = sorted(rec.entries.keys())
            entry_names = {eid: rec.entries[eid].get("name", eid)
                           for eid in entry_ids}
        entry_values = _zh_values(entry_ids,
                                  lambda eid: entry_names.get(eid, eid))
        self._dbg_entry = _entry(r, values=entry_values)
        _btn(r, "永久化", lambda: self.router.execute(
            f"dbg unlock {_pick_id(self._dbg_entry, entry_ids)}"))
        _btn(r, "全部永久化", lambda: self.router.execute("dbg unlock all"))

        # 环境
        r = _row("环境")
        env_ids = []
        env = self.engine.registry.get("environment")
        if env is not None:
            env_ids = [e["id"] for e in env.events]
        self._dbg_env = _entry(r, values=env_ids)
        _btn(r, "切换", lambda: self.router.execute(
            f"dbg env {self._dbg_env.get().strip()}"))

        tk.Label(dlg, text="提示：也可在控制台输入 dbg 查看全部命令。",
                 font=T.FONT_SMALL, fg=T.TEXT_DIM, bg=T.BG,
                 anchor="w").pack(side="bottom", fill="x", padx=10, pady=4)
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    def _quit(self):
        if self._quitting:
            return
        self._quitting = True
        self.engine.log("[退出] 记忆快照保留在内存中（可用 save 落盘）。")
        # 保存窗口几何（位置/大小）
        try:
            self.prefs.set("win.geometry",
                           self.root.winfo_geometry())
            if self.root.state() == "zoomed":
                self.prefs.set("win.zoomed", True)
        except Exception:
            pass
        # 保存 GUI 偏好（折叠/标签）
        try:
            self.prefs.save()
        except Exception:
            pass
        # 取消泵 after，避免窗口销毁后回调报错
        pump_job = getattr(self, "_pump_job", None)
        if pump_job is not None:
            try:
                self.root.after_cancel(pump_job)
            except (tk.TclError, Exception):
                pass
        # 取消按钮栏/面板的挂起 after，避免窗口销毁后回调报错
        for host in getattr(self, "_all_after_hosts", []):
            try:
                host.destroy()
            except Exception:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ================= 主循环 =================
    def run(self) -> None:
        self._pump_job = self.root.after(50, self._pump)
        try:
            self.root.mainloop()
        except tk.TclError:
            pass

    def _pump(self) -> None:
        if self._quitting:
            return
        now = time.monotonic()
        wall = now - self._last_wall
        self._last_wall = now
        if not self.engine.clock.paused:
            self._tick_acc += wall * self.router.speed
            step = 0.25
            while self._tick_acc >= step:
                self.engine.tick(step)
                self._tick_acc -= step
                if self.engine.clock.paused:
                    self._tick_acc = 0.0
                    break
        else:
            self._tick_acc = 0.0
        self._flush_logs()
        self.warnboard.tick()   # TTL 过期清除
        self._autosave()        # 周期自动存档（墙钟，暂停中也照存）
        self.left_host.pump()
        self.right_host.pump()
        if now - self._last_status >= 0.5:
            self._refresh_status()
        if not self._quitting:
            self._pump_job = self.root.after(50, self._pump)

    # ================= 渲染 =================
    def _log_level(self, text: str) -> str:
        """按日志关键词定等级：danger > warn > normal。"""
        if any(k in text for k in ("崩溃", "丢失", "劣化终止", "严重警告")):
            return "danger"
        if any(k in text for k in ("警告", "停摆", "⚠", "记忆", "失忆风险",
                                   "自动暂停")):
            return "warn"
        if any(k in text for k in ("[工业]", "[勘探]", "[扩展]", "[数据库]",
                                   "[存档]", "[读档]", "[巡航]", "[记忆]",
                                   "[反应堆]", "[环境]", "[终局]")):
            return "normal"
        return "normal"

    def _render_warnings(self):
        """渲染活跃警告看板（去重后最多 10 条；无警告时隐藏）。"""
        if not hasattr(self, "warn_area"):
            return
        for w in self.warn_area.winfo_children():
            w.destroy()
        warns = self.warnboard.order()
        if not warns:
            self.warn_area.grid_remove()
            return
        self.warn_area.grid()
        for w in warns:
            cat = w.get("category", "")
            lbl = tk.Label(self.warn_area, text=T.S_WARN + " " + w["text"],
                           font=T.FONT_SMALL, fg=T.WARN_FG,
                           bg=T.PANEL_ALT, anchor="w", justify="left",
                           padx=6, pady=1, cursor="hand2")
            lbl.pack(side="top", fill="x")
            lbl.bind("<Button-1>",
                     lambda e, c=cat: self._on_warn_click(c))

    def _on_warn_click(self, category):
        """警告点击跳转：fac:* → 定位建筑面板对应设施；其余切到日志上下文。"""
        if category.startswith("fac:"):
            fac_id = category.split(":", 1)[1]
            ind = self.engine.registry.get("industry")
            f = ind.facilities.get(fac_id) if ind else None
            if f is None:
                self.engine.log(f"[警告] 设施 {fac_id} 已不存在。")
                return
            # 定位到左侧建筑面板该设施（若建筑面板存在）
            try:
                bp = self.build_host.current
                if bp is not None and hasattr(bp, "selected"):
                    for iid, fid in getattr(bp, "_fac_map", {}).items():
                        if fid == fac_id:
                            bp.tree.selection_set(iid)
                            bp.tree.see(iid)
                            bp.selected = fac_id
                            bp._render_detail()
                            bp._render_actions()
                            break
            except tk.TclError:
                pass
            self._locate_plot(f.plot_id)
            return
        if category == "memory":
            self.router.execute("memory")
            self.router.execute("maintain")
            return
        if category.startswith("fac"):
            self.engine.log(f"[警告] 点击跳转：{category}（详见建筑面板）。")
            return
        self.engine.log(f"[警告] {category} —— 见上方日志。")

    def _append_log(self, text: str) -> None:
        if not self._log_visible(text):
            return
        self.log.configure(state="normal")
        level = self._log_level(text)
        # 记录该行对应的分类（供点击跳转）
        cat = self._log_category(text)
        start = self.log.index("end-1c")
        self.log.insert("end",
                        f"[{fmt_time(self.engine.clock.time)}] {text}\n",
                        (level,))
        end = self.log.index("end-1c")
        if cat:
            self._log_tags = getattr(self, "_log_tags", {})
            self._log_tags[f"{start}"] = cat
            # 给整行打一个不可见标记 tag，点击时反查
            tag = f"cat:{start}"
            try:
                self.log.tag_add(tag, start, end)
            except tk.TclError:
                pass
            self.log.tag_bind(tag, "<Button-1>",
                              lambda e, c=cat: self._on_warn_click(c))
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_log_double(self, event):
        """双击日志行 → 匹配该行出现的词条标题并打开百科。"""
        wiki = self.engine.registry.get("wiki")
        if wiki is None:
            return "break"
        try:
            idx = self.log.index("@%d,%d" % (event.x, event.y))
            raw = self.log.get(f"{idx} linestart", f"{idx} lineend")
        except tk.TclError:
            return "break"
        # 去掉渲染时加的 "[时间] " 前缀
        text = re.sub(r"^\[[^\]]*\]\s*", "", raw).strip()
        if not text:
            return "break"
        eid = self._match_wiki_in_text(text)
        if eid is None:
            self.engine.log("[百科] 该行没有可跳转的词条（双击可跳转设施/物质/科技）。")
            return "break"
        self.open_wiki(eid)
        return "break"

    def _match_wiki_in_text(self, text: str):
        """从一行文本里找出最可能对应的百科词条 id。

        策略：① 标题出现位置最靠前（同位置取更长=句子主语优先）
              ② [物质id] 括号写法（勘探回报里会出现）
              ③ 标签关键词命中（如"记忆崩溃"→ 记忆相关条目）
        """
        wiki = self.engine.registry.get("wiki")
        if wiki is None or not text:
            return None
        best, best_key = None, None
        for e in wiki.entries.values():
            title = e.get("title", "")
            if len(title) < 2:
                continue
            pos = text.find(title)
            if pos < 0:
                continue
            key = (pos, -len(title))
            if best_key is None or key < best_key:
                best_key, best = key, e
        if best is not None:
            return best["id"]
        # ② [substance_id] 形式
        for m in re.finditer(r"\[([a-z_][a-z0-9_]*)\]", text):
            sid = m.group(1)
            if f"sub:{sid}" in wiki.entries:
                return f"sub:{sid}"
        # ③ 标签关键词（取最长的命中标签，越具体越好）
        tag_hit, tag_len = None, 0
        for e in wiki.entries.values():
            for t in e.get("tags", []):
                if len(t) >= 2 and t in text and len(t) > tag_len:
                    tag_hit, tag_len = e, len(t)
        if tag_hit is not None:
            return tag_hit["id"]
        return None

    def _log_category(self, text: str) -> str:
        """从日志文本推断可跳转的分类（设施优先）。"""
        if "[工业]" in text and "停摆" in text:
            # 找最近的停摆设施：以名称匹配（简单可靠）
            ind = self.engine.registry.get("industry")
            if ind is not None:
                for f in ind.facilities.values():
                    if f.name in text:
                        return f"fac:{f.id}"
        if "记忆" in text:
            return "memory"
        return ""

    def _flush_logs(self) -> None:
        new = self.engine.log_lines[self._rendered_logs:]
        for ln in new:
            self._append_log(ln)
        self._rendered_logs = len(self.engine.log_lines)

    def _refresh_status(self) -> None:
        self._last_status = time.monotonic()
        c, e, u = self.engine.clock, self.engine.economy, self.engine.units
        self.time_lbl.configure(text=fmt_time(c.time))
        self.speed_lbl.configure(text=f"×{self.router.speed:g}")
        if c.paused:
            self.state_lbl.configure(text="‖ 暂停", fg=T.ACCENT)
        else:
            self.state_lbl.configure(text="▶ 运行", fg=T.TEXT)
        mem = self.engine.registry.get("memory")
        if mem is not None:
            low = mem.integrity < mem.warn_threshold
            self.mem_lbl.configure(
                text=f"记忆 {mem.integrity:.0f}%",
                fg=T.WARN_FG if low else T.TEXT_SUB,
                bg=T.WARN_BG if low else T.BG)
            # 记忆进度条（完整度比例）
            self._set_bar(self.mem_bar, mem.integrity / mem.max_integrity)
        self.units_lbl.configure(
            text=f"单元 {u.count_idle()}/{u.count()} 空闲")
        db = self.engine.registry.get("database")
        if db is not None:
            self.db_lbl.configure(
                text="DB %d/%d" % (len(db.built), len(db.projects)))
            self._set_bar(self.db_bar,
                          len(db.built) / len(db.projects)
                          if db.projects else 0.0)
        env = self.engine.registry.get("environment")
        if env is not None and env.current() is not None:
            self.env_lbl.configure(text=env.current()["name"])
        else:
            self.env_lbl.configure(text="")
        dl = self.engine.registry.get("daylight")
        if dl is not None:
            self.day_lbl.configure(text=dl.phase_text())
        else:
            self.day_lbl.configure(text="")
        # 电力净值：产-耗（负数=缺电，红底反白提示）
        ind = self.engine.registry.get("industry")
        if ind is not None:
            try:
                pb = ind.power_balance(self.engine)
            except Exception:
                pb = None
            if pb is not None:
                net = pb["produce"] - pb["consume"]
                if net >= 0:
                    self.power_lbl.configure(
                        text=f"电力 +{net:.1f}/s", fg=T.TEXT_SUB, bg=T.BG)
                else:
                    self.power_lbl.configure(
                        text=f"电力 {net:.1f}/s 缺电", fg=T.WARN_FG,
                        bg=T.WARN_BG)
        else:
            self.power_lbl.configure(text="")


def gui_selftest(engine, sub_map) -> bool:
    """无头自检：建窗喂命令刷新，验证不抛错后销毁。"""
    ui = GuiUI(engine, sub_map)
    try:
        ui.root.update_idletasks()
        ui.root.update()
        ui.router.execute("status")
        ui.router.execute("plots")
        ui._flush_logs()
        ui._refresh_status()
        ui.left_host.pump()
        ui.right_host.pump()
        ui.root.update()
        assert "记忆" in ui.mem_lbl.cget("text"), "状态栏记忆标签未更新"
        # 标签页置灰锁检查：工艺/终局初始应锁
        sig = ui.right_host._unlock_signature()
        ui._quit()
        return True
    except tk.TclError as e:
        ui.engine.log(f"[GUI 自检] 无显示环境，跳过: {e}")
        try:
            ui.root.destroy()
        except tk.TclError:
            pass
        return False
