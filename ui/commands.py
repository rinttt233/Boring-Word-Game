"""命令路由：终端与 GUI 共用的唯一命令入口。

设计原则（GUI 讨论定案）：GUI 按钮不直接调用系统方法，而是生成命令字符串
喂给 execute() —— 命令单一来源，终端与 GUI 行为永远一致；新增命令自动获得
两端支持。命令实现只依赖 engine + substance_map，不依赖任何界面。

exit 语义：execute() 处理 quit 时调用 self.on_quit（由界面层注入，
ConsoleUI 设 running=False，GuiUI 销毁窗口）；未注入时仅记日志。
"""
from typing import Callable, Optional

import json
import os
import re
import time

from core.world import fmt_grade

VALID_SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def fmt_amt(v: float) -> str:
    """去尾浮点：≈整 → 整数千分位；否则保留 1-2 位小数（去多余 0）。"""
    if v is None:
        return "-"
    if abs(v - round(v)) < 1e-9:
        return f"{round(v):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


# ================= 存档槽（控制台 / GUI 共用）=================
SAVE_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")


def project_root() -> str:
    """项目根目录（存档一律放这里，避免从别处启动时读写到别处的 saves/）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def saves_dir() -> str:
    return os.path.join(project_root(), "saves")


def slot_path(name: str) -> str:
    return os.path.join(saves_dir(), name + ".json")


def slot_summary(path: str) -> dict:
    """读取存档头部信息，供存档槽列表展示（失败时只给文件信息）。"""
    info = {"ok": False, "time": None, "facilities": None, "db": None,
            "fixated": None, "units": None}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return info
    try:
        clock = data.get("clock", {}) or {}
        info["time"] = float(clock.get("time", 0.0))
        systems = data.get("systems", {}) or {}
        facs = (systems.get("industry", {}) or {}).get("facilities", [])
        info["facilities"] = len(facs)
        info["units"] = len((data.get("units", {}) or {}).get("units", []))
        status = (systems.get("recovery", {}) or {}).get("status", {}) or {}
        info["fixated"] = sum(1 for v in status.values() if v == "permanent")
        built = (systems.get("database", {}) or {}).get("built", [])
        projects = (systems.get("database", {}) or {}).get("projects", [])
        info["db"] = (len(built), len(projects) if projects else 5)
        info["ok"] = True
    except Exception:
        pass
    return info


def list_slots() -> list:
    """列出 saves/*.json 槽位（按修改时间倒序）。"""
    d = saves_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if not fn.endswith(".json") or fn == "gui_prefs.json":
            continue
        p = os.path.join(d, fn)
        if not os.path.isfile(p):
            continue
        try:
            mt = os.path.getmtime(p)
            size = os.path.getsize(p)
        except OSError:
            continue
        out.append({"name": fn[:-5], "path": p, "mtime": mt, "size": size,
                    **slot_summary(p)})
    out.sort(key=lambda r: -r["mtime"])
    return out


def slot_line(r: dict, rname=None) -> str:
    """槽位单行文本（等宽字体下列对齐）。"""
    tag = "自动" if r["name"] in ("autosave", "auto") else "    "
    if r.get("ok") and r.get("time") is not None:
        t = fmt_time(r["time"])
        db = r.get("db") or (0, 5)
        body = (f"t={t:<8} 设施{r['facilities']:>3} 单元{r['units']:>3} "
                f"DB {db[0]}/{db[1]} 固化{r['fixated']:>2}")
    else:
        body = "（无法解析的存档文件）"
    mt = time.strftime("%m-%d %H:%M", time.localtime(r["mtime"]))
    return f"{tag} {r['name']:<16} {body}  {mt}"


class CommandRouter:
    def __init__(self, engine, substance_map,
                 on_quit: Optional[Callable[[], None]] = None) -> None:
        self.engine = engine
        self.subs = substance_map
        self.speed = 1.0
        self.on_quit = on_quit
        self.exec_count = 0          # 已执行的命令数（report 用，便于复盘）
        self._emergency_used = False

    # ================= 名称工具（供命令与界面复用）=================
    def rname(self, rid: str) -> str:
        s = self.subs.get(rid)
        return s["name"] if s else rid

    def runit(self, rid: str) -> str:
        s = self.subs.get(rid)
        return s.get("unit", "") if s else ""

    # ================= 命令分发 =================
    def execute(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return
        cmd = parts[0].lower()
        args = parts[1:]
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            self.engine.log(f"[未知指令] '{cmd}' — 输入 help 查看命令。")
            return
        self.exec_count += 1
        try:
            handler(args)
        except (ValueError, IndexError) as e:
            self.engine.log(f"[参数错误] {e}")

    def _request_quit(self) -> None:
        if self.on_quit is not None:
            self.on_quit()
        self.engine.log("[退出] 记忆快照保留在内存中（可用 save 落盘）。")

    # ---- 命令实现 -------------------------------------------------
    def _cmd_help(self, args):
        self.engine.log(
            "勘探: survey <圈层1-5> | plots | claim <地块> "
            "| build <地块> <设施> | facilities"
        )
        self.engine.log(
            "工业: assign <设施> | unassign <设施> | units | resources "
            "| undo (撤销上次建造) | demolish <设施> (拆除返一半) "
            "| mothball <设施> [on|off] | status"
        )
        self.engine.log(
            "生存: maintain (记忆加固，防失忆) | memory | entries | "
            "recover <条目> | fixate <条目>"
        )
        self.engine.log(
            "终局: db (工程进度) | construct (建造当前子系统) | migrate"
        )
        self.engine.log(
            "时间: pause | resume | toggle | speed <倍率> | cruise <秒> "
            "| cruise off"
        )
        self.engine.log(
            "系统: logs | save <名> | load <名> | quit | help | facilities_help"
        )
        self.engine.log(
            "百科: wiki (列分类) | wiki <关键词> 搜索 | wiki #<id> 查看词条"
        )
        self.engine.log(
            "AI/盲测: guide (试玩向导) | report (JSON 状态) | suggest (下一步建议) "
            "| cover (覆盖清单)"
        )
        self.engine.log(
            "副产物: policy backlog throttle|ignore (超限限产开关) | "
            "convert(水煤气变换)/vent(放空塔)/sink(回注井) 三选一给副产物找出路"
        )
        self.engine.log(
            "调试: dbg res <资源> <量> | dbg mem <0-100> | dbg add <资源> <量>"
            " | dbg unit <n> | dbg unlock <条目|all> | dbg time <秒> |"
            " dbg env <事件> | dbg degrade off | dbg instant [on|off]"
        )
        self.engine.log(
            "快捷键: 空格=暂停/继续 | F9=存档 / F10=读档 | 任意键可随时打断时间流动输入指令"
        )

    def _cmd_facilities_help(self, args):
        ind = self.engine.registry.get("industry")
        if ind is None:
            return
        lines = []
        for d in ind.defs.values():
            allowed = ",".join(d.get("allowed_plot_kinds", []))
            cost = ", ".join(f"{k}{v:g}" for k, v in d.get("build_cost", {}).items()) or "无"
            req = ""
            if d.get("requires_recovery"):
                req = f" | 需恢复: {d['requires_recovery']}"
            lines.append(
                f"  {d['id']}: {d['name']} (建在{allowed}地, 成本{cost}, "
                f"单元位{d.get('slots', 1)}){req}")
            if d.get("extract_rate"):
                lines.append(f"     提取速率 {d['extract_rate']:g}/s "
                             f"耗电 {d.get('power_use', 0):g}/s")
            elif d.get("recipe"):
                r = ind.recipes.get(d["recipe"], {})
                ins = " ".join(f"{k}:{v:g}/s" for k, v in r.get("inputs", {}).items())
                outs = " ".join(f"{k}:{v:g}/s" for k, v in r.get("outputs", {}).items())
                lines.append(f"     {ins} → {outs}")
        self.engine.log("[设施图鉴] " + " | ".join(
            [d['id'] for d in ind.defs.values()]))
        for ln in lines:
            self.engine.log(ln)

    def _cmd_memory(self, args):
        mem = self.engine.registry.get("memory")
        if mem is None:
            self.engine.log("无记忆系统")
            return
        mc = mem.maintain_cfg
        cost = " ".join(f"{k}:{v:g}" for k, v in mc.get("cost", {}).items())
        rate = mem.degrade_rate(self.engine) if hasattr(mem, "degrade_rate") \
            else mem.degrade_per_sec
        self.engine.log(
            f"记忆完整度 {mem.integrity:.1f}% / {mem.max_integrity:.0f}% | "
            f"劣化 {rate:.3f}/s | 阈值告警 {mem.warn_threshold:.0f}%"
        )
        self.engine.log(
            f"加固: 消耗 {cost}，恢复 {mem.restore_amount():.0f}%"
            f"（按完整度分档）| {mc.get('desc', '')}"
        )
        maint = self.engine.registry.get("maintenance")
        if maint is not None:
            self.engine.log("[维护] " + maint.status_text(self.engine))

    def _cmd_policy(self, args):
        """policy [backlog throttle|ignore] —— 副产物积压政策（批次3）。

        throttle（默认）：超限副产物会让产出它的设施限产；
        ignore：完全关闭限产（纯沙盒/调试）。
        """
        ind = self.engine.registry.get("industry")
        if ind is None:
            self.engine.log("无工业模块")
            return
        if not args:
            self.engine.log(f"[政策] 副产物积压 = {ind.backlog_policy}"
                            "（可选 throttle / ignore）")
            st = ind.backlog_status(self.engine)
            over = [f"{k} {v['stock']:g}/{v['limit']:g}"
                    for k, v in st.items() if v["over"]]
            self.engine.log("[政策] 当前限产：" + ("；".join(over) if over
                                                   else "无超限副产物"))
            return
        if args[0].lower() != "backlog" or len(args) < 2:
            self.engine.log("用法: policy backlog throttle|ignore")
            return
        val = args[1].lower()
        if val not in ("throttle", "ignore"):
            self.engine.log("可选值: throttle（超限限产）/ ignore（关闭）")
            return
        ind.backlog_policy = val
        self.engine.log(f"[政策] 副产物积压政策已设为 {val}。")

    def _cmd_demolish(self, args):
        """demolish <设施id> —— 拆除设施，返还 50% 材料，地块可重新规划。"""
        ind = self.engine.registry.get("industry")
        if ind is None or not args:
            self.engine.log("用法: demolish <设施id>（返还包括建造成本的一半）")
            return
        err = ind.demolish(self.engine, args[0], refund=0.5)
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_mothball(self, args):
        """mothball <设施ID> [on|off] —— 封存/解除封存（零维护消耗）。"""
        ind = self.engine.registry.get("industry")
        if ind is None or not args:
            self.engine.log("用法: mothball <设施ID> [on|off]")
            return
        fid = args[0]
        on = True
        if len(args) >= 2:
            on = args[1].lower() not in ("off", "0", "false", "解封")
        err = ind.mothball(self.engine, fid, on=on)
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_entries(self, args):
        rec = self.engine.registry.get("recovery")
        if rec is None:
            self.engine.log("无恢复系统")
            return
        state_names = {"locked": "未恢复", "active": "临时(待固化)",
                       "permanent": "已固化"}
        for e in rec.entry_list():
            st = state_names.get(e.get("state", "locked"), e.get("state"))
            # BUG-5：临时条目给出**剩余游戏秒**倒计时（<30s 标 ! 提醒）
            extra = ""
            if e.get("state") == "active":
                left = rec.expires_in(self.engine, e["id"])
                if left is not None:
                    mark = "!" if left <= 30 else ""
                    extra = f" | 剩余 {left:.0f}s{mark}"
                    if rec.is_queued(e["id"]):
                        extra += "（固化已排队）"
            unlocks = e.get("unlocks_facility", [])
            u = ("解锁: " + ",".join(unlocks)) if unlocks else ""
            cost = " ".join(f"{k}:{v:g}" for k, v in e.get("cost", {}).items())
            self.engine.log(
                f"  {e['id']} {e['name']} [{st}{extra}] | 恢复需 {cost} | {u}")
            self.engine.log(f"      {e.get('desc', '')}")

    def _cmd_recover(self, args):
        if not args:
            self.engine.log("[用法] recover <条目id>，如 recover db_coking")
            return
        rec = self.engine.registry.get("recovery")
        err = rec.recover(self.engine, args[0]) if rec else "无恢复系统"
        if err:
            self.engine.log(f"[数据库] {err}")
            return
        # 试玩提案 §9：recover 只花电，fixate 还要合金 —— 材料没备齐就白烧窗口
        if rec is not None:
            e = rec.entries.get(args[0], {})
            miss = [f"{k} 缺 {float(v) - self.engine.economy.get(k):g}"
                    for k, v in (e.get("fixate_cost") or {}).items()
                    if self.engine.economy.get(k) < float(v)]
            if miss:
                ttl = rec.ttl_of(args[0]) if hasattr(rec, "ttl_of") else 180.0
                self.engine.log(
                    f"⚠ 固化材料不足（{'、'.join(miss)}）：临时窗口只有 "
                    f"{ttl:.0f}s，窗口一过条目照样丢 —— 赶紧补齐材料后 "
                    f"`fixate {args[0]}`，或先把电/单元让给别的活。",
                    level="warn", category="memory")

    def _cmd_fixate(self, args):
        if not args:
            self.engine.log("[用法] fixate <条目id>，固化已恢复条目")
            return
        rec = self.engine.registry.get("recovery")
        err = rec.fixate(self.engine, args[0]) if rec else "无恢复系统"
        if err:
            self.engine.log(f"[数据库] {err}")
        elif rec is not None and rec.is_queued(args[0]):
            # fixate 已排队（没有空闲单元），上面的排队日志已给出细节
            self.engine.log(
                f"[数据库] {args[0]} 的固化在队列里：单元一空出就自动开工，"
                "期间临时窗口照常流逝（`units` 看谁占着单元）。")

    # ---- 勘探/扩展命令 ---------------------------------------------
    def _cmd_survey(self, args):
        if not args:
            self.engine.log("[用法] survey <圈层1-5>，如 survey 1")
            return
        sys_s = self.engine.registry.get("survey")
        err = sys_s.survey(self.engine, int(args[0])) if sys_s else "无勘探模块"
        if err:
            self.engine.log(f"[勘探] {err}")

    def _cmd_claim(self, args):
        if not args:
            self.engine.log("[用法] claim <地块id>，如 claim IRON1")
            return
        sys_c = self.engine.registry.get("claim")
        err = sys_c.claim(self.engine, args[0]) if sys_c else "无扩展模块"
        if err:
            self.engine.log(f"[扩展] {err}")

    def _cmd_build(self, args):
        if len(args) < 2:
            self.engine.log("[用法] build <地块id> <设施id>，如 build HOME power_plant")
            self._cmd_facilities_help([])
            return
        ind = self.engine.registry.get("industry")
        if ind is None:
            self.engine.log("无工业模块")
            return
        err = ind.build(self.engine, args[0], args[1])
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_assign(self, args):
        if not args:
            self.engine.log("[用法] assign <设施id>")
            return
        ind = self.engine.registry.get("industry")
        err = ind.assign(self.engine, args[0]) if ind else "无工业模块"
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_unassign(self, args):
        if not args:
            self.engine.log("[用法] unassign <设施id>")
            return
        ind = self.engine.registry.get("industry")
        err = ind.unassign(self.engine, args[0]) if ind else "无工业模块"
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_undo(self, args):
        """撤销最近一次建造（仅限尚未产出过的设施，防套利）。"""
        ind = self.engine.registry.get("industry")
        if ind is None:
            self.engine.log("无工业模块")
            return
        err = ind.undo_build(self.engine)
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_facilities(self, args):
        ind = self.engine.registry.get("industry")
        if ind is None:
            self.engine.log("无工业模块")
            return
        if not ind.facilities:
            self.engine.log("[工业] 暂无设施。用 build 建造。")
            return
        for f in ind.facilities.values():
            d = ind.defs[f.def_id]
            units = ",".join(f.assigned) or "无"
            fuel_txt = ""
            if d.get("kind") == "burner":
                fuel_txt = f" | 燃料[{f.fuel or '未设'}]"
            self.engine.log(
                f"  {f.id} {f.name} @{f.plot_id} | 单元[{units}] | "
                f"类型 {d['id']}{fuel_txt}"
            )

    def _cmd_fuel(self, args):
        """fuel <设施id> <燃料id> —— 设置燃烧发电设施燃料。"""
        ind = self.engine.registry.get("industry")
        if ind is None:
            self.engine.log("无工业模块")
            return
        if len(args) < 2:
            fuels = ind.fuel_options() if hasattr(ind, "fuel_options") else []
            self.engine.log("[用法] fuel <设施id> <燃料id>")
            if fuels:
                self.engine.log("可燃燃料: " + ", ".join(fuels))
            return
        err = ind.set_fuel(self.engine, args[0], args[1])
        if err:
            self.engine.log(f"[工业] {err}")

    def _cmd_maintain(self, args):
        mem = self.engine.registry.get("memory")
        if mem is None:
            self.engine.log("无记忆系统")
            return
        err = mem.maintain(self.engine)
        if err:
            self.engine.log(f"[记忆] {err}")

    # ---- 时间控制 -------------------------------------------------
    def _cmd_pause(self, args):
        self.engine.clock.pause()
        self.engine.clock.cancel_cruise()
        self.engine.log("[暂停] 世界冻结。输入 resume 或空格继续。")

    def _cmd_resume(self, args):
        self.engine.clock.resume()
        self.engine.log("[继续] 时间恢复流动。")

    def _cmd_toggle(self, args):
        self._toggle_pause()

    def _toggle_pause(self):
        c = self.engine.clock
        if c.paused:
            c.resume()
            self.engine.log("[继续]")
        else:
            c.pause()
            c.cancel_cruise()
            self.engine.log("[暂停]")

    def _cmd_speed(self, args):
        v = float(args[0])
        if v <= 0:
            raise ValueError("倍率须 > 0")
        self.speed = v
        self.engine.log(f"[速度] 播放倍率 ×{v:g}")

    def _cmd_cruise(self, args):
        c = self.engine.clock
        if args and args[0] == "off":
            c.cancel_cruise()
            self.engine.log("[巡航] 已取消。")
            return
        sec = float(args[0])
        if sec <= 0:
            raise ValueError("巡航秒数须 > 0")
        self.engine.cruise(sec)
        self.engine.log(f"[巡航] 目标 {fmt_time(sec)} 后自动暂停。")

    def _cmd_status(self, args):
        c, e, u = self.engine.clock, self.engine.economy, self.engine.units
        mem = self.engine.registry.get("memory")
        mem_txt = ""
        if mem is not None:
            mem_txt = f" | {mem.status_text()}"
        self.engine.log(f"时间 {fmt_time(c.time)} | 倍率 ×{self.speed:g} | "
                        f"{'运行中' if not c.paused else '已暂停'}"
                        + (" | 巡航中" if c.cruising else "")
                        + mem_txt)
        self.engine.log(f"执行单元 {u.count_idle()}/{u.count()} 空闲 | "
                        f"利用率 {u.utilization() * 100:.0f}%")
        plots = self.engine.world.visible_plots()
        known = sum(1 for p in plots if p.substance)
        ind = self.engine.registry.get("industry")
        nfac = len(ind.facilities) if ind else 0
        jobs = self.engine.jobs.count()
        self.engine.log(f"已勘察地块 {len(plots)} 处，其中矿藏 {known} 处 | "
                        f"设施 {nfac} | 进行中作业 {jobs}")
        # 试玩提案 §10/§13：空地数（工厂只能建空地，是最稀缺的规划资源）
        free_empty = [p.id for p in plots
                      if p.kind == "empty"
                      and p.state in ("claimed", "developed", "depleted")
                      and not any(f.plot_id == p.id
                                  for f in (ind.facilities.values()
                                            if ind else []))]
        if free_empty:
            self.engine.log(f"可用空地 {len(free_empty)} 块（{'、'.join(free_empty[:6])}"
                            + ("…" if len(free_empty) > 6 else "") + "）")
        else:
            self.engine.log("可用空地 **0 块** —— 工厂类设施只能建空地："
                            "先 survey/claim 新地块，或 demolish 掉不用的设施")
        # 试玩提案 §3/§13：被副产物积压限产的设施（别误判成"输入不足"）
        if ind is not None:
            thr = [f for f in ind.facilities.values() if f.backlog_over]
            if thr:
                self.engine.log(
                    "[积压] 被限产：" + "、".join(
                        f"{f.name}({f.backlog_over})" for f in thr[:5])
                    + " —— 出路：水煤气变换炉／放空塔／回注井／"
                      "`policy backlog ignore`", level="warn",
                    category="industry")
        # 电力预算（M3：诊断"为何停摆"）
        if ind is not None:
            pb = ind.power_balance(self.engine)
            net = pb["produce"] - pb["consume"]
            flag = "~盈余" if net >= 0 else "!缺电"
            self.engine.log(
                f"[电网] 产 {pb['produce']:.2f}/s | 耗 {pb['consume']:.2f}/s "
                f"| 净 {net:+.2f}/s {flag}")
            if net < 0 and pb["consumers"]:
                top = sorted(pb["consumers"], key=lambda x: -x[1])[:3]
                self.engine.log("  最大耗电: " +
                                " | ".join(f"{n} {v:.2f}/s" for n, v in top))
        # 终局工程进度
        db = self.engine.registry.get("database")
        if db is not None and not db.is_complete():
            nxt = db.next_project()
            self.engine.log(f"[终局] {db.progress_text()}"
                            + (f" | 当前可建: {nxt['name']}"
                               if nxt else " | 等待烧录迁移"))
        # 环境状态（第5模块）
        env = self.engine.registry.get("environment")
        if env is not None and env.current() is not None:
            ev = env.current()
            prod = ev.get("effects", {}).get("production", 1.0)
            self.engine.log(f"[环境] {ev['name']}"
                            + (f" (生产×{prod:g})" if prod != 1.0 else ""))
        res = e.snapshot()
        if res:
            items = " | ".join(f"{self.rname(k)} {fmt_amt(v)}{self.runit(k)}"
                               for k, v in sorted(res.items()))
            self.engine.log("库存: " + items)
        else:
            self.engine.log("库存: 空")

    # ---- 终局工程命令 ----------------------------------------------
    def _cmd_env(self, args):
        env = self.engine.registry.get("environment")
        if env is None:
            self.engine.log("[环境] 无环境系统。")
            return
        e = env.current()
        if e is None:
            self.engine.log("[环境] 无数据。")
            return
        prod = e.get("effects", {}).get("production", 1.0)
        mem = e.get("effects", {}).get("memory", 1.0)
        eff = f" | 生产×{prod:g}" if prod != 1.0 else ""
        eff += f" | 记忆劣化×{mem:g}" if mem != 1.0 else ""
        self.engine.log(f"[环境] {e['name']}{eff} | {e.get('desc', '')}")

    def _cmd_db(self, args):
        db = self.engine.registry.get("database")
        if db is None:
            self.engine.log("无数据库工程")
            return
        if db.is_complete():
            self.engine.log("[终局] 可靠数据库已建成，劣化终止。")
            return
        # CRASH-1 修复：全部竣工但尚未迁移时 next_project() 为 None
        nxt = db.next_project()
        nxt_id = nxt["id"] if nxt else None
        for p in db.projects:
            st = "√竣工" if p["id"] in db.built else "·未建"
            if nxt_id is not None and p["id"] == nxt_id \
                    and p["id"] not in db.built:
                st = "▶当前"
            cost = " ".join(f"{k}:{v:g}" for k, v in p.get("cost", {}).items())
            preq = f" | 电网≥{p.get('power_req', 0):g}kWh" \
                if p.get("power_req") else ""
            self.engine.log(f"  {p['id']} {p['name']} [{st}] | "
                            f"需 {cost}{preq} | {p.get('desc', '')}")
        if nxt_id is None:
            self.engine.log(f"[终局] {db.progress_text()} —— 全部子系统已竣工，"
                            "可执行 migrate 烧录迁移（需主线知识全部固化）。")
        else:
            self.engine.log(f"[终局] {db.progress_text()}"
                            " | 指令: construct 建造当前项 | migrate 烧录迁移")

    def _cmd_construct(self, args):
        db = self.engine.registry.get("database")
        err = db.construct(self.engine) if db else "无数据库工程"
        if err:
            self.engine.log(f"[终局] {err}")

    def _cmd_migrate(self, args):
        db = self.engine.registry.get("database")
        err = db.migrate(self.engine) if db else "无数据库工程"
        if err:
            self.engine.log(f"[终局] {err}")

    def _cmd_plots(self, args):
        w = self.engine.world
        ind = self.engine.registry.get("industry")
        rows = w.visible_plots()
        if not rows:
            self.engine.log("[勘探] 暂无已勘察地块。用 survey <圈层> 勘察。")
            return
        state_names = {"known": "已勘察", "claimed": "已占领",
                       "developed": "已开发", "depleted": "已枯竭"}
        kind_names = {"empty": "空地", "water": "水源", "ore": "矿脉",
                      "wreck": "残骸"}
        for p in rows:
            extra = ""
            if p.substance:
                extra = f" | {self.rname(p.substance)} 品位{fmt_grade(p.grade)}% " \
                        f"储量{p.reserve:g}{self.runit(p.substance)}"
            fac = ""
            if ind:
                for f in ind.facilities.values():
                    if f.plot_id == p.id:
                        fac = f" | {f.name}({f.id})"
                        break
            self.engine.log(
                f"  {p.id} 圈{p.ring} [{kind_names.get(p.kind, p.kind)}]"
                f"{extra}{fac} | "
                f"物流×{p.logistics_multiplier():g} | "
                f"{state_names.get(p.state, p.state)}"
            )

    def _cmd_units(self, args):
        for u in self.engine.units.units:
            self.engine.log(f"  {u.id} {u.name} | {u.status}"
                            + (f" → {u.task}" if u.task else ""))

    def _cmd_resources(self, args):
        self._cmd_status(args)

    def _cmd_logs(self, args):
        n = int(args[0]) if args else 12
        for line in self.engine.log_lines[-n:]:
            self.engine.log("  " + line)

    # ---- agent / 盲测接口（只读，见 ui/agent_api.py 与 AGENTS.md）----
    def _cmd_report(self, args):
        """report [to <文件>] —— 机器可读状态（JSON）。"""
        from ui import agent_api
        rep = agent_api.build_report(self.engine, self)
        if args and args[0].lower() == "to" and len(args) >= 2:
            path = args[1]
            if not os.path.isabs(path):
                path = os.path.join(project_root(), path)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
            self.engine.log(f"[报告] 已写入 {os.path.relpath(path, project_root())}"
                            f"（report_version={rep['report_version']}）")
            return
        # 控制台直接输出紧凑 JSON（GUI 下也可复制）
        self.engine.log(json.dumps(rep, ensure_ascii=False))

    def _cmd_suggest(self, args):
        """suggest —— 现在最该做的几件事（建议+理由+阻塞）。"""
        from ui import agent_api
        items = agent_api.suggest_actions(self.engine, self)
        if not items:
            self.engine.log("[建议] 暂时没有可执行的建议 —— 试试 @tick 推进时间。")
            return
        self.engine.log("[建议] 按优先级：")
        for i, s in enumerate(items, 1):
            cmd = s["cmd"] or "（无法执行）"
            line = f"  {i}) {cmd}　← {s['why']}"
            if s.get("blocked"):
                line += f"　[阻塞：{s['blocked']}]"
            self.engine.log(line)

    def _cmd_cover(self, args):
        """cover —— 内容覆盖清单进度（盲测目标函数）。"""
        from ui import agent_api
        tr = getattr(self, "_coverage", None)
        if tr is None:
            tr = agent_api.CoverageTracker()
            self._coverage = tr
        tr.update(agent_api.build_report(self.engine, self))
        p = tr.progress()
        self.engine.log(f"[覆盖] {p['done']}/{p['total']} 项已体验")
        for m in p["missing"]:
            self.engine.log(f"  · 未覆盖：{m['desc']}（{m['id']}）"
                            + (f"　提示：{m['hint']}" if m["hint"] else ""))

    def _cmd_guide(self, args):
        """guide [章节id] —— 打印试玩向导（与 AGENTS.md 同源）。"""
        from ui import agent_api
        text = agent_api.guide_text(args[0] if args else "")
        for line in text.splitlines():
            self.engine.log(line)

    def _cmd_save(self, args):
        name = args[0] if args else None
        if name is None:
            # GUI：打开存档槽选择窗口（控制台下退回默认档名）
            hook = getattr(self, "save_slot_hook", None)
            if hook is not None:
                hook()
                return
            name = "auto"
        if not SAVE_NAME_RE.fullmatch(name):
            raise ValueError("存档名仅允许字母/数字/_-")
        os.makedirs(saves_dir(), exist_ok=True)
        path = slot_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.engine.to_dict(), f, ensure_ascii=False, indent=1)
        self.engine.log(f"[存档] 已写入 {os.path.relpath(path, project_root())}")

    def _cmd_load(self, args):
        if not args:
            hook = getattr(self, "load_slot_hook", None)
            if hook is not None:
                hook()
                return
            raise ValueError("用法: load <存档名>")
        path = slot_path(args[0])
        if not os.path.exists(path):
            raise ValueError(f"存档不存在: {os.path.relpath(path, project_root())}")
        with open(path, "r", encoding="utf-8") as f:
            self.engine.from_dict(json.load(f))
        self.engine.log(f"[读档] {args[0]} 已载入，时间 {fmt_time(self.engine.clock.time)}")
        self._post_load_check()

    def _post_load_check(self):
        """读档后自检（READLOCK-1）：落到"无燃料+无电+采矿需电"死锁时，
        给明确诊断并提供**一次性应急启动**，避免"读档即绝望"。"""
        ind = self.engine.registry.get("industry")
        if ind is None or not hasattr(ind, "power_deadlock"):
            return
        why = ind.power_deadlock(self.engine)
        if why is None:
            return
        self.engine.log(f"[读档自检] 检测到能源死锁：{why}。", level="danger",
                        category="power")
        if self._emergency_used:
            self.engine.log("[读档自检] 应急启动本局已用过一次，不再重复发放。"
                            "建议：load 一个更早的存档，或手动 dbg 处理。",
                            level="warn")
            return
        self._emergency_used = True
        self.engine.economy.add("coal", 30.0)
        from core.stats import bump as _bump
        _bump(self.engine, "emergency_starts")
        self.engine.log("[读档自检] 已发放应急启动煤 30t（一次性，已记入统计）。"
                        "请立刻用它把采煤链接回电网：给电站 fuel 设煤 → "
                        "给煤矿派单元 → 让电力储备回升。", level="warn")

    def _cmd_quit(self, args):
        self._request_quit()

    # ---- 百科 ------------------------------------------------------
    def _cmd_wiki(self, args):
        """wiki            → 列出分类
           wiki <关键词>   → 搜索词条
           wiki #<id>      → 按 id 直接查看
        """
        wiki = self.engine.registry.get("wiki")
        if wiki is None:
            self.engine.log("无百科模块")
            return
        if not args:
            total = wiki.count()
            self.engine.log(f"[百科] 共 {total} 条词条。分类：")
            for cat in wiki.categories():
                items = wiki.list_by_category(cat)
                self.engine.log(f"  {cat}（{len(items)}）："
                                + "、".join(e["title"] for e in items[:8])
                                + ("…" if len(items) > 8 else ""))
            self.engine.log("[百科] 用法: wiki <关键词> 搜索 | "
                            "wiki #<id> 查看 | GUI 右区「百科」页可翻查")
            return
        key = args[0]
        if key.startswith("#"):
            eid = key[1:]
            e = wiki.get(eid)
            if e is None:
                # 容错：允许直接写游戏内条目 id（db_coking / coal / cokery …）
                for prefix in ("db:", "sub:", "fac:", "rec:"):
                    e = wiki.get(prefix + eid)
                    if e is not None:
                        break
            if e is None:
                self.engine.log(f"[百科] 未找到词条 {eid}"
                                "（可先 wiki <关键词> 搜索，或写全 id 如 #db:db_coking）")
                return
            self.engine.log(f"【{e['title']}】({e['category']})")
            for line in e["body"].splitlines():
                self.engine.log("  " + line)
            return
        hits = wiki.search(key, limit=8)
        if not hits:
            self.engine.log(f"[百科] 没有匹配「{key}」的词条。")
            return
        if len(hits) == 1:
            e = hits[0]
            self.engine.log(f"【{e['title']}】({e['category']})")
            for line in e["body"].splitlines():
                self.engine.log("  " + line)
            return
        self.engine.log(f"[百科] 「{key}」匹配 {len(hits)} 条：")
        for e in hits:
            self.engine.log(f"  {e['title']}  [{e['category']}]  #{e['id']}")

    # ---- 调试命令（F11 调试面板/控制台通用，仅供开发调试）---------
    def _cmd_dbg(self, args):
        """调试统一入口：dbg <sub> [...]。子命令：
          res <rid> <amt>         设置资源库存(0=清零)
          add <rid> <amt>         追加资源
          mem <pct>               设置记忆完整度(0~100)
          degrade <on|off>        暂停/恢复记忆劣化
          unit <n>                增加 n 个执行单元
          time <sec>              跳转游戏时间(有副作用,慎用)
          unlock <entry|all>      把恢复条目永久化(或全部)
          env <id>                强制切到指定环境事件
          instant [on|off]        切换即时建造(不耗时不占执行单元)
        """
        if not args:
            self.engine.log("[调试] 用法见 dbg 帮助。")
            return
        sub = args[0].lower()
        engine = self.engine
        try:
            if sub == "res" and len(args) >= 3:
                amt = float(args[2])
                engine.economy.set(args[1], amt)
                engine.log(f"[调试] 资源 {args[1]} 设为 {amt:g}。")
            elif sub == "add" and len(args) >= 3:
                amt = float(args[2])
                engine.economy.add(args[1], amt)
                engine.log(f"[调试] 资源 {args[1]} +{amt:g}。")
            elif sub == "mem" and len(args) >= 2:
                mem = engine.registry.get("memory")
                if mem is None:
                    engine.log("[调试] 无记忆模块。")
                    return
                v = max(0.0, min(mem.max_integrity, float(args[1])))
                mem.integrity = v
                engine.log(f"[调试] 记忆完整度设 {v:.0f}%。")
            elif sub == "degrade" and len(args) >= 2:
                mem = engine.registry.get("memory")
                if mem is None:
                    engine.log("[调试] 无记忆模块。")
                    return
                mem._degradation_disabled = args[1].lower() == "off"
                engine.log("[调试] 记忆劣化"
                           + ("暂停" if mem._degradation_disabled else "恢复") + "。")
            elif sub == "unit" and len(args) >= 2:
                n = max(1, int(float(args[1])))
                for _ in range(n):
                    engine.units.add_unit("调试执行器")
                engine.log(f"[调试] 增加 {n} 个执行单元"
                           f"（现 {engine.units.count()}）。")
            elif sub == "time" and len(args) >= 2:
                engine.clock.time = max(0.0, float(args[1]))
                engine.log(f"[调试] 时间跳转至 {fmt_time(engine.clock.time)}"
                           "（注意：进行中作业不受影响）。")
            elif sub == "unlock":
                rec = engine.registry.get("recovery")
                if rec is None:
                    engine.log("[调试] 无恢复模块。")
                    return
                if len(args) >= 2 and args[1] == "all":
                    for eid in rec.entries:
                        rec.status[eid] = "permanent"
                    engine.log("[调试] 全部恢复条目已永久化。")
                elif len(args) >= 2:
                    eid = args[1]
                    if eid in rec.entries:
                        rec.status[eid] = "permanent"
                        engine.log(f"[调试] 条目 {eid} 已永久化。")
                    else:
                        engine.log(f"[调试] 未知条目: {eid}")
                else:
                    engine.log("[调试] unlock <entry|all>")
            elif sub == "env" and len(args) >= 2:
                env = engine.registry.get("environment")
                if env is None:
                    engine.log("[调试] 无环境模块。")
                    return
                env.current_id = args[1]
                env._until = engine.clock.time + 86400.0
                cur = env.current()
                engine.log("[调试] 环境切至: "
                           + (cur.get("name", args[1]) if cur else args[1]))
            elif sub == "instant":
                ind = engine.registry.get("industry")
                if ind is None:
                    engine.log("[调试] 无工业模块。")
                    return
                if len(args) >= 2:
                    ind.instant_build = args[1].lower() in ("on", "1", "true")
                else:
                    ind.instant_build = not ind.instant_build
                engine.log("[调试] 即时建造 "
                           + ("开启（不耗时不占单元）"
                              if ind.instant_build else "关闭（正常施工耗时）")
                           + "。")
            else:
                self.engine.log("[调试] 未知子命令或参数不足，见 dbg 帮助。")
        except (ValueError, IndexError) as e:
            self.engine.log(f"[调试] 参数错误: {e}")
