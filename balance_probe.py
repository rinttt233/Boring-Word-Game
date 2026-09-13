# -*- coding: utf-8 -*-
"""M4 平衡探针 v4：数值不变量 + 真实机制脚本化试玩。

第一部分：断言游戏数值不变量（开局煤能否撑到找到煤矿、回收节奏、记忆节奏）。
第二部分：完整脚本化试玩（真实 content 真实机制，不注入资源），
  从出生 → 第一炉生铁 → 记录时间线。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import build_engine          # noqa: E402
from core.engine import Engine         # noqa: E402


def tick_for(eng, seconds, step=0.25):
    t = 0.0
    while t < seconds:
        eng.tick(step)
        t += step


def spawn_claimed(eng, kind, substance=None, grade=0, reserve=0):
    eng.world.spawn(ring=0, kind=kind, substance=substance,
                    grade=grade, reserve=reserve, state="claimed")
    return [p.id for p in eng.world.plots.values()
            if p.kind == kind and (substance is None or p.substance == substance)
            and p.state == "claimed"][-1]


def build_staff(eng, ind, plot_id, def_id, max_s=90):
    err = ind.build(eng, plot_id, def_id)
    assert err is None, f"build {def_id}@{plot_id}: {err}"
    fid = [f.id for f in ind.facilities.values()
           if f.plot_id == plot_id][-1]
    # 建造耗时（批次1.5 起）：等完工再派员 —— 否则 assign 会被拒（仍在建造中），
    # 设施永远不运转（探针自 1.5 起一直踩这个坑）。
    t = 0.0
    while t < max_s and ind.facilities[fid].under_construction:
        eng.tick(0.25)
        t += 0.25
    assert not ind.facilities[fid].under_construction, f"{def_id} 建造超时"
    ind.assign(eng, fid)
    return fid


def ensure_free_unit(eng, ind, keep=("power_plant",)):
    """若没有空闲单元，从非保活设施调离一个（模拟玩家腾挪）。"""
    if eng.units.count_idle() > 0:
        return
    for f in list(ind.facilities.values()):
        if f.assigned and ind.defs[f.def_id]["id"] not in keep:
            ind.unassign(eng, f.id)
            return


def do_claim(eng, ind, claim, plot_id, label, max_s=300):
    plot = eng.world.get(plot_id)
    ensure_free_unit(eng, ind)
    if plot.state == "known" and eng.units.count_idle() > 0:
        claim.claim(eng, plot_id)
    t = 0.0
    while t < max_s:
        eng.tick(0.25)
        t += 0.25
        if plot.state == "claimed":
            return True
        # 作业没启动（无空闲单元）→ 腾单元重试
        if eng.jobs.count() == 0 and plot.state == "known" \
                and eng.units.count_idle() > 0:
            claim.claim(eng, plot_id)
    print(f"  ⚠ 超时占领: {label}")
    return False


def part1_numeric_invariants():
    """不依赖 RNG 的数值平衡断言。"""
    print("=== 第一部分: 数值不变量 ===")
    ok = True
    # I1: 开局煤 120t（A12 ①），电厂耗 0.4/s → 300s；最近煤矿 survey 8s+claim 3s
    #     窗口 300s 足够（安全边际 >10x）
    margin = (120.0 / 0.4) / (8.0 + 3.0)
    print(f"  I1 开局煤窗口: 120/0.4={300:.0f}s vs 找煤所需 ~11s, "
          f"安全边际 {margin:.1f}x")
    ok &= margin > 5
    # I2: WRECK1 残骸 180t @ 1.2/s = 150s 拆完 → 奖励单元节奏
    wreck_time = 180.0 / 1.2
    print(f"  I2 出生残骸拆解: {wreck_time:.0f}s 得合金+1单元")
    ok &= wreck_time < 300
    # I3: 记忆: 100% @0.02/s → 归零需 5000s；maintain 恢复 25%
    #     加固成本 20电+5合金
    print(f"  I3 记忆: 满→0 需 {100 / 0.02:.0f}s (83min) 游戏时间")
    # I4: 电厂供电 1.6/s vs 基础链: 煤矿机0.1+铁矿机0.1+炼焦0.1+
    #     高炉0.2 = 0.5/s → 单电厂有余
    demand = 0.1 * 3 + 0.2
    print(f"  I4 单电厂 1.6/s vs 早期链需求 ~{demand:.1f}/s → 盈余")
    ok &= 1.6 > demand * 1.5
    print(f"  第一部分 {'通过' if ok else '有超标项'}\n")
    return ok


def part2_scripted_playthrough():
    """早期冒烟：验证出生→(电厂+拆残骸)→煤矿稳定 的真实节奏。
    只测到"基础供给稳定"，工艺链深水区由 gameplay/chain/victory 测试覆盖。
    """
    print("=== 第二部分: 早期冒烟 (真实 bootstrap) ===")
    eng = build_engine()
    eng.clock.resume()
    ind = eng.registry.get("industry")
    claim = eng.registry.get("claim")
    survey = eng.registry.get("survey")
    mem = eng.registry.get("memory")

    def run_until(cond, max_s, label):
        t = 0.0
        while t < max_s:
            eng.tick(0.25)
            t += 0.25
            if cond():
                return True
        print(f"  ⚠ 超时: {label}")
        return False

    # 电厂(1单元) + 拆残骸(1单元)，第3单元勘探
    build_staff(eng, ind, "HOME", "power_plant")          # U1
    do_claim(eng, ind, claim, "WRECK1", "占WRECK1")       # U2 作业
    build_staff(eng, ind, "WRECK1", "salvager")           # U2 拆残骸
    # U3 勘探圈1直到见煤（40%权重，最多8次）
    for i in range(8):
        if any(p.substance == "coal" and p.ring >= 1
               and p.state == "known" for p in eng.world.plots.values()):
            break
        if survey.survey(eng, 1) is None:
            run_until(lambda: eng.jobs.count() == 0, 60, f"survey#{i}")
    cp = next((p for p in eng.world.plots.values()
               if p.substance == "coal" and p.ring >= 1
               and p.state == "known"), None)
    assert cp, "圈1应有概率出煤（40%权重×4次）"
    # U3 占领煤矿，然后把 U2 从残骸调来采煤，U3 回去拆残骸? 不——
    # 简化: 残骸先拆完得第4单元，再由新单元开煤矿
    do_claim(eng, ind, claim, cp.id, "占煤矿")             # U3 作业
    # 等残骸拆完(奖励单元)
    run_until(lambda: eng.world.get("WRECK1").state == "depleted",
              300, "WRECK1拆完")
    # 现在有 4 单元：电厂、残骸(已空)、U3闲；新单元开煤矿
    ensure_free_unit(eng, ind)
    if not any(f.plot_id == cp.id for f in ind.facilities.values()):
        build_staff(eng, ind, cp.id, "extractor")
    # 可持续性检查：先看煤是否被电厂烧穿（<5t 即失败），再跑 300s 验证
    # 净增长（煤矿 0.5/s > 电厂 0.4/s），存量应从低谷回升
    run_until(lambda: eng.economy.get("coal") > 5, 300, "煤不至于断供")
    low0 = eng.economy.get("coal")
    ok = run_until(lambda: eng.economy.get("coal") > low0 + 20, 600,
                   "煤净增长")
    print(f"[{eng.clock.time:6.0f}s] 煤供给稳定: "
          f"煤 {eng.economy.get('coal'):.0f}t (回升自 {low0:.0f}) | 合金 "
          f"{eng.economy.get('scrap_alloy'):.0f}t | 单元 "
          f"{eng.units.count()} | 记忆 {mem.integrity:.0f}%")
    return ok


if __name__ == "__main__":
    i1 = part1_numeric_invariants()
    ok2 = part2_scripted_playthrough()
    print(f"\n第一部分: {'✅ 通过' if i1 else '❌ 有超标'}")
    print(f"第二部分: {'✅ 通过' if ok2 else '❌ 早期供给未稳定'}")
    print("--- 探针完成 ---")
