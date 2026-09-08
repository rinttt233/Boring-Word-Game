"""文字控制台界面（终端皮）。

命令实现已抽到 ui/commands.py 的 CommandRouter，与 GUI 共用；本文件只负责：
- 播放速率换算（墙钟 × 倍率 → 游戏秒）喂 engine.tick
- 键盘/行输入
- 渲染（状态行 + 日志窗口）
"""
import os
import sys
import time
import unicodedata

try:
    import msvcrt  # Windows 非阻塞按键
except ImportError:  # pragma: no cover
    msvcrt = None

from ui.commands import CommandRouter, fmt_time

WIDTH = 78
LOG_WINDOW = 14


def disp_width(text: str) -> int:
    """终端显示宽度：全角/宽字符按 2 列计，其余 1 列。"""
    w = 0
    for ch in text:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def wrap_display(text: str, width: int = WIDTH) -> list:
    """按显示宽度折行（长信息不被截断），返回行列表。

    中文/全角字符占 2 列，保证在终端里真正不溢出。
    """
    if width <= 1:
        return [text]
    lines = []
    cur = ""
    cur_w = 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur_w + cw > width and cur:
            lines.append(cur)
            cur = ""
            cur_w = 0
        cur += ch
        cur_w += cw
    if cur or not lines:
        lines.append(cur)
    return lines


def _cls() -> None:
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")


class ConsoleUI:
    def __init__(self, engine, substance_map, speed: float = 1.0) -> None:
        self.engine = engine
        self.router = CommandRouter(engine, substance_map,
                                    on_quit=self._on_quit)
        self.router.speed = speed if speed > 0 else 1.0
        self.running = True
        self._rendered_logs = 0
        self._last_render = 0.0

    @property
    def speed(self):
        return self.router.speed

    def _on_quit(self) -> None:
        self.running = False

    def _execute(self, line: str) -> None:
        """转发到命令路由（保留给测试与外部调用）。"""
        self.router.execute(line)

    # ================= 主循环 =================
    def run(self) -> None:
        interactive = (msvcrt is not None and sys.stdin.isatty())
        if not interactive:
            self._run_blocking()
        else:
            self._run_rt()

    # ---- 实时模式（Windows）------------------------------------
    def _run_rt(self) -> None:
        _cls()
        self._render()
        acc = 0.0
        last = time.monotonic()
        buf = ""
        while self.running:
            now = time.monotonic()
            wall = now - last
            last = now
            # 世界推进：墙钟 × 倍率 → 游戏秒
            if not self.engine.clock.paused:
                acc += wall * self.speed
                step = 0.25
                while acc >= step:
                    self.engine.tick(step)
                    acc -= step
                    if self.engine.clock.paused:   # 巡航到点自动暂停
                        break
            # 按键处理（非阻塞）
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\r":                     # 回车执行
                    if buf.strip():
                        self.router.execute(buf.strip())
                    buf = ""
                elif ch in ("\x00", "\xe0"):       # 功能键前缀，吃掉第二字节
                    msvcrt.getwch()
                elif ch == "\x08" or ch == "\x7f":  # 退格
                    buf = buf[:-1]
                elif ch == " ":                     # 空格 = 暂停/继续
                    if buf == "":
                        self.router._toggle_pause()
                else:
                    buf += ch
                self._render()
            # 周期性刷新（事件变更由日志计数触发渲染）
            if time.monotonic() - self._last_render > 0.5:
                self._render()
            time.sleep(0.02)
        self._render(final=True)

    # ---- 阻塞行模式（非 Windows / 非 tty 降级）------------------
    def _run_blocking(self) -> None:
        self._print_logs()
        while self.running:
            # 世界推进：跑到没有作业/巡航结束自动暂停为止，等待下一条命令
            self._advance_until_idle()
            if not self.running:
                break
            try:
                line = input("\n[cmd] ").strip()
            except EOFError:
                self._advance_until_idle()   # EOF：把剩余世界跑完
                break
            if not line:
                continue
            self.router.execute(line)
            self._print_logs()
        print("[退出] 再见。")

    def _advance_until_idle(self) -> None:
        acc = 0.0
        last = time.monotonic()
        step = 0.25
        guard = 0
        while (self.engine.jobs.count() > 0 or self.engine.clock.cruising) \
                and not self.engine.clock.paused and guard < 100000:
            now = time.monotonic()
            acc += (now - last) * self.speed
            last = now
            while acc >= step:
                self.engine.tick(step)
                acc -= step
            guard += 1
            if not sys.stdout.isatty():
                self._print_logs()
            time.sleep(0.005)

    # ================= 渲染 =================
    def _render(self, final: bool = False) -> None:
        if not sys.stdout.isatty():
            self._print_logs()
            return
        self._last_render = time.monotonic()
        _cls()
        c, e, u = self.engine.clock, self.engine.economy, self.engine.units
        state = ("▶ 运行" if not c.paused else "‖ 暂停")
        if c.cruising:
            state += f" 巡航剩余{fmt_time(c.cruise_remaining())}"
        title = f" 坠毁ASI · 拓荒日志  |  {fmt_time(c.time)}  |  ×{self.speed:g}  |  {state} "
        print("─" * WIDTH)
        # 长信息一律折行显示（不再截断）
        for seg in wrap_display(title, WIDTH):
            print(seg)
        res = e.snapshot()
        if res:
            top = " | ".join(f"{self.router.rname(k)} {v:.1f}{self.router.runit(k)}"
                             for k, v in sorted(res.items(), key=lambda kv: -kv[1])[:4])
            for seg in wrap_display(" 库存 " + top, WIDTH):
                print(seg)
        for seg in wrap_display(
                f" 单元 {u.count_idle()}/{u.count()} 空闲 | 地块 "
                f"{len(self.engine.world.visible_plots())} | "
                f"日志 {len(self.engine.log_lines)}", WIDTH):
            print(seg)
        print("─" * WIDTH)
        # 日志窗口：从最新往回取，按显示行数（折行后）凑满 LOG_WINDOW 行
        budget = LOG_WINDOW
        blocks = []
        for ln in reversed(self.engine.log_lines):
            segs = wrap_display("  " + ln, WIDTH)
            if len(segs) > budget:
                segs = segs[:budget]
                blocks.append(segs)
                budget = 0
                break
            blocks.append(segs)
            budget -= len(segs)
            if budget <= 0:
                break
        for segs in reversed(blocks):
            for seg in segs:
                print(seg)
        print("─" * WIDTH)
        print("> ", end="", flush=True)

    def _print_logs(self) -> None:
        """headless：增量打印新日志。"""
        new = self.engine.log_lines[self._rendered_logs:]
        for ln in new:
            print(f"[{fmt_time(self.engine.clock.time)}] {ln}", flush=True)
        self._rendered_logs = len(self.engine.log_lines)
