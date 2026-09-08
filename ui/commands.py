"""命令路由：终端与 GUI 共用的唯一命令入口。

设计原则（GUI 讨论定案）：GUI 按钮不直接调用系统方法，而是生成命令字符串
喂给 execute() —— 命令单一来源，终端与 GUI 行为永远一致；新增命令自动获得
两端支持。命令实现只依赖 engine + substance_map，不依赖任何界面。

exit 语义：execute() 处理 quit 时调用 self.on_quit（由界面层注入，
ConsoleUI 设 running=False，GuiUI 销毁窗口）；未注入时仅记日志。
"""
from typing import Callable, Optional

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


class CommandRouter:
    def __init__(self, engine, substance_map,
                 on_quit: Optional[Callable[[], None]] = None) -> None:
        self.engine = engine
        self.subs = substance_map
        self.speed = 1.0
        self.on_quit = on_quit

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
            "| undo (撤销上次建造) | status"
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
            "调试: dbg res <资源> <量> | dbg mem <0-100> | dbg add <资源> <量>"
            " | dbg unit <n> | dbg unlock <条目|all> | dbg time <秒> |"
            " dbg env <事件> | dbg degrade off"
        )
        self.engine.log(
            "快捷键: 空格=暂停/继续 | 任意键可随时打断时间流动输入指令"
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
        self.engine.log(
            f"记忆完整度 {mem.integrity:.1f}% / {mem.max_integrity:.0f}% | "
            f"劣化 {mem.degrade_per_sec:g}/s | 阈值告警 {mem.warn_threshold:.0f}%"
        )
        self.engine.log(
            f"加固: 消耗 {cost}，恢复 {mc.get('restore', 0):g}% | "
            f"{mc.get('desc', '')}"
        )

    def _cmd_entries(self, args):
        rec = self.engine.registry.get("recovery")
        if rec is None:
            self.engine.log("无恢复系统")
            return
        state_names = {"locked": "未恢复", "active": "临时(待固化)",
                       "permanent": "已固化"}
        for e in rec.entry_list():
            st = state_names.get(e.get("state", "locked"), e.get("state"))
            unlocks = e.get("unlocks_facility", [])
            u = ("解锁: " + ",".join(unlocks)) if unlocks else ""
            cost = " ".join(f"{k}:{v:g}" for k, v in e.get("cost", {}).items())
            self.engine.log(
                f"  {e['id']} {e['name']} [{st}] | 恢复需 {cost} | {u}")
            self.engine.log(f"      {e.get('desc', '')}")

    def _cmd_recover(self, args):
        if not args:
            self.engine.log("[用法] recover <条目id>，如 recover db_coking")
            return
        rec = self.engine.registry.get("recovery")
        err = rec.recover(self.engine, args[0]) if rec else "无恢复系统"
        if err:
            self.engine.log(f"[数据库] {err}")

    def _cmd_fixate(self, args):
        if not args:
            self.engine.log("[用法] fixate <条目id>，固化已恢复条目")
            return
        rec = self.engine.registry.get("recovery")
        err = rec.fixate(self.engine, args[0]) if rec else "无恢复系统"
        if err:
            self.engine.log(f"[数据库] {err}")

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
        for p in db.projects:
            st = "√竣工" if p["id"] in db.built else "·未建"
            if p["id"] == db.next_project()["id"] and p["id"] not in db.built:
                st = "▶当前"
            cost = " ".join(f"{k}:{v:g}" for k, v in p.get("cost", {}).items())
            preq = f" | 电网≥{p.get('power_req', 0):g}kWh" \
                if p.get("power_req") else ""
            self.engine.log(f"  {p['id']} {p['name']} [{st}] | "
                            f"需 {cost}{preq} | {p.get('desc', '')}")
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
                extra = f" | {self.rname(p.substance)} 品位{p.grade:g}% " \
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

    def _cmd_save(self, args):
        import json
        import os
        import re
        name = args[0] if args else "auto"
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            raise ValueError("存档名仅允许字母/数字/_-")
        os.makedirs("saves", exist_ok=True)
        path = os.path.join("saves", name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.engine.to_dict(), f, ensure_ascii=False, indent=1)
        self.engine.log(f"[存档] 已写入 {path}")

    def _cmd_load(self, args):
        import json
        import os
        if not args:
            raise ValueError("用法: load <存档名>")
        path = os.path.join("saves", args[0] + ".json")
        if not os.path.exists(path):
            raise ValueError(f"存档不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            self.engine.from_dict(json.load(f))
        self.engine.log(f"[读档] {args[0]} 已载入，时间 {fmt_time(self.engine.clock.time)}")

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
            e = wiki.get(key[1:])
            if e is None:
                self.engine.log(f"[百科] 未找到词条 {key[1:]}")
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
            else:
                self.engine.log("[调试] 未知子命令或参数不足，见 dbg 帮助。")
        except (ValueError, IndexError) as e:
            self.engine.log(f"[调试] 参数错误: {e}")
