#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent_play.py —— 让 AI agent（或任何脚本）逐回合试玩真实游戏（文件协议）。

为什么这么做：agent 不能点鼠标，但可以"读状态 → 下命令 → 再读状态"。
本进程长驻，把真实引擎（真实 content、真实规则）跑起来，用两个文本文件
与调用者对话：

    <in>.txt   累积命令（按行号消费：重复写同样的行不会重复执行）
    <out>.txt  追加回执：命令结果、日志尾部、状态摘要、统计

命令 = 任意游戏命令（与 GUI/控制台同一路由：build/assign/survey/recover/
fixate/maintain/mothball/claim/save/load/fuel…）
     + @ 前缀的 agent 指令：
       @view            状态摘要 + 日志尾部（"文本截图"）
       @stats           运行统计与关键指标
       @tick <秒> [步长] 推进游戏时间（默认只在被要求时推进，便于逐回合试玩）
       @run  <秒>        以真实速度运行（便于观察 GUI 动画），随后回到暂停
       @pause / @resume  暂停/恢复时间流动
       @quit            结束进程

时间模型：默认**暂停**，只有 @tick / @run 让它前进 —— 因此一局完整游戏
可以在几秒墙钟内跑完，且结果可复现（配合 --seed 固定环境随机）。

用法：
    python -X utf8 tools/agent_play.py                      # 无头
    python -X utf8 tools/agent_play.py --gui                # 开真窗口（可配合截图）
    python -X utf8 tools/agent_play.py --in saves/ai_in.txt --out saves/ai_out.txt \
        --samples saves/ai_samples.csv --sample-every 60
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main                                      # noqa: E402
from core.world import fmt_grade                 # noqa: E402
from ui.commands import CommandRouter, fmt_time  # noqa: E402


def now_stamp() -> str:
    return time.strftime("%H:%M:%S")


class AgentPlay:
    def __init__(self, engine, subs, in_path, out_path, samples_path=None,
                 sample_every=60.0, gui=None, step=0.5, fresh=False,
                 replay=False) -> None:
        self.engine = engine
        self.subs = subs
        self.router = CommandRouter(engine, subs)
        self.in_path = in_path
        self.out_path = out_path
        self.samples_path = samples_path
        self.sample_every = float(sample_every)
        self.step = float(step)
        self.gui = gui
        self.replay = bool(replay)
        self.consumed = 0
        self._next_sample = 0.0
        self._log_cursor = 0
        for p in (in_path, out_path, samples_path):
            if p:
                os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        # 输出文件默认**追加**（试玩提案 §12：以前启动就清空，上一局日志全没了，
        # 只能自己 Copy-Item 备份）；想开新局用 --fresh。
        self.fresh = bool(fresh)
        mode = "w" if self.fresh else "a"
        with open(self.out_path, mode, encoding="utf-8") as f:
            if not self.fresh:
                f.write("\n")
            f.write(f"[{now_stamp()}] agent_play 启动（{'GUI' if gui else '无头'}"
                    f"{'，新日志' if self.fresh else '，追加'}）\n")
        if samples_path:
            with open(self.samples_path, mode, encoding="utf-8") as f:
                if self.fresh or f.tell() == 0 or mode == "w":
                    f.write("t,electricity,coal,steel,kit,units,idle,facs,"
                            "stalled,memory,efficiency,stall_seconds,"
                            "breakdowns\n")
        if not os.path.exists(in_path):
            with open(in_path, "w", encoding="utf-8") as f:
                f.write("@view\n")
        # 试玩提案 §12：**不从第 1 行消费** —— 文件里残留的旧命令（尤其 @quit）
        # 会让新进程一启动就退出。默认跳过启动前已有的行（用 --replay 可重放）。
        try:
            with open(in_path, "r", encoding="utf-8") as f:
                existing = len(f.read().splitlines())
        except OSError:
            existing = 0
        if not replay:
            self.consumed = existing
            if existing:
                self.say(f"（已跳过 in 文件里启动前的 {existing} 行历史命令；"
                         "只执行之后追加的命令。要重放请加 --replay）")
        self._lock_path = os.path.join(os.path.dirname(os.path.abspath(in_path)),
                                       "agent_play.lock")
        self._write_lock()
        self.say("用法：向 in 文件追加命令行。@view 看状态，@tick 600 推进 600 秒。")

    LOCK_STALE_SEC = 12 * 3600.0        # 锁文件超过 12h 视为陈旧

    def _write_lock(self) -> None:
        """写锁文件（试玩提案 §12：两个实例同时跑会互相覆盖同一组文件）。

        ⚠ **绝不探测 pid 存活**：Windows 上 `os.kill(pid, 0)` 不是"发 0 号信号探测"，
        而是 `TerminateProcess`，会把那个进程（甚至自己）真的杀掉 —— 那是比"两个实例
        互相覆盖"严重得多的后果。这里只按**锁文件新鲜度**做软提醒。
        """
        try:
            fresh = False
            if os.path.exists(self._lock_path):
                age = time.time() - os.path.getmtime(self._lock_path)
                fresh = age < self.LOCK_STALE_SEC
                if fresh:
                    with open(self._lock_path, encoding="utf-8") as f:
                        old = f.read().strip()
                    self.say(f"⚠ 存在较新的锁文件（{old or '未知'}，"
                             f"{age / 60:.0f} 分钟前）—— 可能有另一个 agent_play "
                             "仍在运行，两个实例会互相覆盖 in/out 文件；"
                             "确认它已退出可忽略本条（本工具**不会**替你杀进程）。")
            with open(self._lock_path, "w", encoding="utf-8") as f:
                f.write(f"pid={os.getpid()} started={now_stamp()}\n")
        except OSError:
            pass

    def _release_lock(self) -> None:
        """退出时删掉属于自己的锁（不是自己的就不动）。"""
        try:
            if not os.path.exists(self._lock_path):
                return
            with open(self._lock_path, encoding="utf-8") as f:
                txt = f.read()
            if f"pid={os.getpid()}" in txt:
                os.remove(self._lock_path)
        except OSError:
            pass

    # ---- 输出 ------------------------------------------------------
    def say(self, text: str) -> None:
        with open(self.out_path, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")

    def section(self, title: str) -> None:
        self.say("")
        self.say("=" * 62)
        self.say(f"[{now_stamp()}] {title}")
        self.say("=" * 62)

    def tail_logs(self, n: int = 14) -> None:
        """输出这一拍新产生的日志；`n <= 0` 表示**不截断**（信息类命令用）。

        试玩提案 §7：`facilities_help` 这类长输出曾被 tail(80) 截掉前半部分，
        而前半正是最常用的电站/炼焦炉/高炉 —— 现在信息类命令全量输出。
        """
        lines = self.engine.log_lines
        if len(lines) > self._log_cursor:
            new = lines[self._log_cursor:]
            self._log_cursor = len(lines)
            if n <= 0 or len(new) <= n:
                show = new
            else:
                show = new[-n:]
                self.say(f"  …（还有 {len(new) - n} 行日志未显示）")
            for ln in show:
                self.say("  │ " + ln)

    # ---- 状态摘要（"文本截图"）-------------------------------------
    def digest(self) -> None:
        eng = self.engine
        c = eng.clock
        reg = eng.registry
        self.say(f"时间 {fmt_time(c.time)} | {'暂停' if c.paused else '运行'}"
                 f" | 倍率 {self.router.speed:g}")
        mem = reg.get("memory")
        if mem is not None:
            rate = mem.degrade_rate(eng) if hasattr(mem, "degrade_rate") else 0
            self.say(f"记忆 {mem.integrity:.1f}% (劣化 {rate:.3f}/s"
                     f"{'，劣化已终止' if getattr(mem, '_degradation_disabled', False) else ''})")
        db = reg.get("database")
        if db is not None:
            self.say(f"数据库 {len(db.built)}/{len(db.projects)}"
                     f" {'✓可迁移' if db.is_complete() else ''}")
        units = eng.units
        self.say(f"执行单元 {units.count()} 个（空闲 {units.count_idle()}）"
                 f" 效能 ×{units.efficiency:.2f}")
        maint = reg.get("maintenance")
        if maint is not None and maint.enabled(eng):
            self.say("  " + maint.status_text(eng))
        ind = reg.get("industry")
        if ind is not None:
            pb = ind.power_balance(eng)
            self.say(f"电力 产 {pb['produce']:.1f}/s 耗 {pb['consume']:.1f}/s"
                     f" 净 {pb['produce'] - pb['consume']:+.1f}/s")
        # 资源（前 12 项）
        res = eng.economy.snapshot()
        top = sorted(res.items(), key=lambda kv: -kv[1])[:12]
        self.say("库存 " + "、".join(
            f"{self.router.rname(k)} {v:,.1f}{self.router.runit(k)}"
            for k, v in top if v > 0))
        # 设施
        if ind is not None:
            self.say(f"设施 {len(ind.facilities)} 座：")
            for f in sorted(ind.facilities.values(), key=lambda x: x.id):
                d = ind.defs[f.def_id]
                plot = eng.world.get(f.plot_id)
                ring = f"圈{plot.ring}" if plot else "?"
                if d.get("extract_rate"):
                    sub = "水" if plot and plot.kind == "water" else (
                        self.router.rname(plot.substance) if plot and
                        plot.substance else "?")
                    extra = f"→{sub} 剩{plot.reserve:,.0f}" if plot else ""
                elif d.get("kind") == "burner":
                    extra = f"→电 烧{f.fuel or '未设燃料'}"
                elif d.get("kind") == "renewable":
                    extra = "→电(可再生)"
                else:
                    r = ind.recipes.get(d.get("recipe", ""), {})
                    outs = r.get("outputs", {})
                    extra = ("→" + self.router.rname(
                        max(outs, key=lambda k: float(outs[k])))) if outs else ""
                state = []
                if f.under_construction:
                    state.append("建造中")
                if f.mothballed:
                    state.append("封存")
                if f.halt_until > c.time:
                    state.append(f"{f.halt_reason or '停机'}"
                                 f"({f.halt_until - c.time:.0f}s)")
                if f.stalled_reported and not state:
                    state.append(f"停摆:{f.stall_reason or '?'}")
                if not f.assigned and not state:
                    state.append("无单元")
                if f.upkeep < 99.0:
                    state.append(f"设备{f.upkeep:.0f}")
                self.say(f"   {f.id} {f.name} {ring} {extra} "
                         f"单元[{','.join(f.assigned) or '-'}] "
                         + (" ".join(state) if state else "运转"))
        # 地块
        rows = []
        for p in sorted(eng.world.visible_plots(), key=lambda x: (x.ring, x.id)):
            fac = ind.facilities.get(
                next((f.id for f in ind.facilities.values()
                      if f.plot_id == p.id), ""), None) if ind else None
            sub = self.router.rname(p.substance) if p.substance else p.kind
            res_txt = ""
            if p.kind in ("ore", "wreck") and p.reserve:
                g = (f"~{fmt_grade(p.known_grade)}%"
                     if getattr(p, "known_grade", None) else "")
                res_txt = f" {sub}{g} 剩{p.reserve:,.0f}"
            elif p.kind == "water":
                res_txt = " 水源"
            elif sub and sub != p.kind:
                res_txt = f" {sub}"
            rows.append(f"   {p.id} 圈{p.ring} [{p.state}]{res_txt}"
                        + (f" 『{fac.id} {fac.name}』" if fac else ""))
        self.say(f"地块 {len(rows)} 块：")
        for r in rows:
            self.say(r)
        # 恢复条目（只列已解锁/可研究的前 8 条）
        rec = reg.get("recovery")
        if rec is not None:
            st = {"permanent": "固化", "active": "临时", "locked": "未恢复"}
            done = [f"{(rec.entries[e].get('name') or e)}({st[s]})"
                    for e, s in rec.status.items() if s != "locked"]
            locked = [e for e, s in rec.status.items() if s == "locked"]
            self.say("已获得知识：" + ("、".join(done) if done else "（无）"))
            self.say(f"未恢复条目 {len(locked)} 条：" + "、".join(locked[:10])
                     + ("…" if len(locked) > 10 else ""))
        jobs = eng.jobs.list_jobs()
        if jobs:
            self.say("进行中作业：" + "、".join(
                f"{j.kind}({j.target_id},{j.remaining:.0f}s)" for j in jobs))
        self.tail_logs()

    def stats(self) -> None:
        eng = self.engine
        self.say("统计：" + eng.stats.summary_text())
        ind = eng.registry.get("industry")
        if ind is not None:
            stalled = [f for f in ind.facilities.values() if f.stalled_reported]
            idle = [f for f in ind.facilities.values() if not f.assigned]
            self.say(f"  设施 {len(ind.facilities)} 座：运转 "
                     f"{len(ind.facilities) - len(stalled) - len(idle)}、"
                     f"停摆 {len(stalled)}、无单元 {len(idle)}")
            if stalled:
                self.say("  停摆原因：" + "；".join(
                    f"{f.id} {f.stall_reason}" for f in stalled[:8]))

    # ---- 采样 ------------------------------------------------------
    def sample(self) -> None:
        if not self.samples_path:
            return
        eng = self.engine
        ind = eng.registry.get("industry")
        mem = eng.registry.get("memory")
        maint = eng.registry.get("maintenance")
        ep = eng.economy
        facs = list(ind.facilities.values()) if ind else []
        row = [
            f"{eng.clock.time:.1f}",
            f"{ep.get('electricity'):.1f}", f"{ep.get('coal'):.1f}",
            f"{ep.get('steel'):.1f}",
            f"{ep.get('maintenance_kit'):.1f}",
            f"{eng.units.count()}", f"{eng.units.count_idle()}",
            f"{len(facs)}",
            f"{sum(1 for f in facs if f.stalled_reported)}",
            f"{mem.integrity:.1f}" if mem else "",
            f"{eng.units.efficiency:.3f}",
            f"{eng.stats.get('stall_seconds'):.1f}",
            f"{eng.stats.get('breakdowns'):.0f}",
        ]
        # 维护件留空时统一写 0，便于报告解析
        if maint is None or not maint.enabled(eng):
            row[4] = "0.0"
        with open(self.samples_path, "a", encoding="utf-8") as f:
            f.write(",".join(row) + "\n")

    # ---- 时间推进 --------------------------------------------------
    def advance(self, seconds: float) -> None:
        eng = self.engine
        was_paused = eng.clock.paused
        eng.clock.resume()
        t = 0.0
        n = 0
        while t < seconds:
            eng.tick(min(self.step, seconds - t))
            t += self.step
            n += 1
            if self.gui is not None and n % 40 == 0:
                try:
                    self.gui.root.update()
                except Exception:
                    break
            if self.samples_path and eng.clock.time >= self._next_sample:
                self._next_sample = eng.clock.time + self.sample_every
                self.sample()
        if was_paused:
            eng.clock.pause()
        if self.gui is not None:
            try:
                self.gui.root.update()
            except Exception:
                pass
        self.say(f"推进 {seconds:.0f}s（模拟步长 {self.step:g}s，{n} 帧）→ "
                 f"现在 {fmt_time(eng.clock.time)}")

    # ---- 命令循环 --------------------------------------------------
    def handle(self, line: str) -> bool:
        line = line.strip()
        if not line or line.startswith("#"):
            return True
        self.section(f"> {line}")
        if line.startswith("@"):
            parts = line.split()
            cmd = parts[0].lower()
            if cmd == "@view":
                self.digest()
            elif cmd == "@stats":
                self.stats()
            elif cmd == "@tick":
                n = float(parts[1]) if len(parts) > 1 else 60.0
                if len(parts) > 2:
                    self.step = float(parts[2])
                self.advance(n)
                self.digest()
            elif cmd == "@run":
                n = float(parts[1]) if len(parts) > 1 else 10.0
                self.advance(n)
                self.digest()
            elif cmd == "@pause":
                self.engine.clock.pause()
                self.say("已暂停")
            elif cmd == "@resume":
                self.engine.clock.resume()
                self.say("已恢复运行")
            elif cmd == "@note":
                self.say("备注：" + line[len("@note"):].strip())
            elif cmd == "@quit":
                self.say("收到 @quit，进程退出。")
                return False
            else:
                self.say(f"未知 agent 指令: {cmd}")
            return True
        # 普通游戏命令：走与 GUI/控制台完全相同的路由
        before = len(self.engine.log_lines)
        self.router.execute(line)
        # 信息类命令（wiki/guide/entries/...）输出较长，别把内容截掉
        info_cmds = ("wiki", "guide", "entries", "report", "suggest", "cover",
                     "facilities", "facilities_help", "plots", "units",
                     "status", "resources", "db", "memory", "help")
        n = 0 if line.split()[0].lower() in info_cmds else 6
        self.tail_logs(n)
        if len(self.engine.log_lines) == before:
            self.say("（无日志输出）")
        return True

    def run(self) -> None:
        poll = 0.12
        while True:
            try:
                with open(self.in_path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except OSError:
                raw = ""
            if raw and not raw.endswith("\n"):
                # 丢弃可能"写了一半"的最后一行，避免执行被截断的命令
                raw = raw.rsplit("\n", 1)[0]
            lines = raw.splitlines()
            for ln in lines[self.consumed:]:
                self.consumed += 1
                if not self.handle(ln):
                    self._release_lock()
                    return
            time.sleep(poll)


def main_cli() -> int:
    ap = argparse.ArgumentParser(description="AI agent 试玩回路（文件协议）")
    ap.add_argument("--gui", action="store_true", help="开真实 GUI 窗口")
    ap.add_argument("--in", dest="inp", default=os.path.join(
        ROOT, "saves", "ai_in.txt"))
    ap.add_argument("--out", dest="outp", default=os.path.join(
        ROOT, "saves", "ai_out.txt"))
    ap.add_argument("--samples", default=os.path.join(
        ROOT, "saves", "ai_samples.csv"))
    ap.add_argument("--sample-every", type=float, default=60.0)
    ap.add_argument("--no-samples", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--fresh", action="store_true",
                    help="清空 out/samples 开新局（默认**追加**，保留上一局日志）")
    ap.add_argument("--replay", action="store_true",
                    help="连 in 文件启动前已有的命令一起执行（默认跳过，"
                         "避免残留的 @quit 让进程立刻退出）")
    args = ap.parse_args()

    engine = main.build_engine()
    subs = {s["id"]: s for s in json.load(
        open(os.path.join(ROOT, "content", "substances.json"),
             encoding="utf-8"))["substances"]}
    gui = None
    if args.gui:
        from ui.gui import GuiUI
        gui = GuiUI(engine, subs, speed=args.speed)
        gui.router.save_slot_hook = None
        gui.router.load_slot_hook = None
    engine.clock.pause()          # 只在被要求时推进
    shell = AgentPlay(engine, subs, args.inp, args.outp,
                      None if args.no_samples else args.samples,
                      args.sample_every, gui=gui, fresh=args.fresh,
                      replay=args.replay)
    shell.say(f"引擎就绪：content 已加载（物质 {len(subs)}），"
              f"时钟默认暂停，请用 @tick 推进。")
    shell.digest()
    # 启动自检：一开始就是"能源死锁"形态时明确告知（避免读档即绝望）
    ind0 = engine.registry.get("industry")
    if ind0 is not None and hasattr(ind0, "power_deadlock"):
        why = ind0.power_deadlock(engine)
        if why:
            shell.say(f"!! 启动自检：{why}")
            shell.say("!! 建议：先 `fuel <电站> <燃料>`，或给煤田派单元/加电站，"
                      "把采煤链接回电网。")
    if gui is not None:
        def _pump():
            try:
                gui.root.update()
                gui.root.after(80, _pump)
            except Exception:
                pass
        gui.root.after(80, _pump)
    shell.run()
    if gui is not None:
        try:
            gui._quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
