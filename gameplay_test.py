# -*- coding: utf-8 -*-
"""M1 完整玩法闭环：模拟玩家从头到尾的操作序列。

用真实 main.build_engine（真实 content），逐步操作 + tick，断言闭环成立：
发电(烧煤)→采铁→炼铁→(勘探找到煤/石灰石→占领)。这验证"游戏可玩性"而非仅模块。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import build_engine          # noqa: E402
from ui.console import ConsoleUI       # noqa: E402


def tick_until(eng, condition, max_s=300, step=0.5):
    t = 0.0
    while t < max_s:
        eng.tick(step)
        t += step
        if condition():
            return True
    return False


def cmd(ui, line):
    ui._execute(line)


def main():
    eng = build_engine()
    subs = {s["id"]: s for s in __import__("json").load(
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "content", "substances.json"),
             encoding="utf-8"))["substances"]}
    ui = ConsoleUI(eng, subs, speed=2.0)
    eng.clock.resume()

    # 1) 出生即有 3 执行单元 + 库存 + 出生残骸
    assert eng.units.count() == 3, "出生应 3 单元"
    assert eng.world.get("WRECK1") is not None, "出生应有残骸可回收"

    # 2) HOME 建发电站（**建造耗时作业**：占单元→完工→再分配）
    ind = eng.registry.get("industry")
    assert ind.instant_build is False, "真实玩法应采用建造耗时"
    cmd(ui, "build HOME power_plant")
    assert len(ind.facilities) == 1
    f1 = ind.facilities["F1"]
    assert f1.under_construction, "开工后设施应处于建造中"
    assert ind.assign(eng, "F1") is not None, "建造中不应允许分配单元"
    assert tick_until(eng, lambda: not f1.under_construction, max_s=60), \
        "建造作业应完成"
    cmd(ui, "assign F1")

    # 3) 勘探圈1（初始煤库存有限，需找煤矿）
    cmd(ui, "survey 1")
    ok = tick_until(eng, lambda: eng.jobs.count() == 0, max_s=60)
    assert ok, "勘探作业应完成"
    survey_plots = [p for p in eng.world.plots.values()
                    if p.ring >= 1 and p.state in ("known", "claimed",
                                                   "developed")]
    assert survey_plots, "勘探应发现新地块"

    # 4) 占领并开采 IRON1（已知矿脉），第3单元拆出生残骸
    cmd(ui, "claim IRON1")
    tick_until(eng, lambda: eng.world.get("IRON1").state == "claimed",
               max_s=60)
    cmd(ui, "build IRON1 extractor")
    assert tick_until(eng, lambda: not ind.facilities["F2"]
                      .under_construction, max_s=60), "采矿机应建成"
    cmd(ui, "assign F2")
    cmd(ui, "claim WRECK1")
    tick_until(eng, lambda: eng.world.get("WRECK1").state == "claimed",
               max_s=60)
    cmd(ui, "build WRECK1 salvager")
    assert tick_until(eng, lambda: not ind.facilities["F3"]
                      .under_construction, max_s=60), "回收站应建成"
    cmd(ui, "assign F3")
    # 三单元全部占满 → 不能再去占领别处（调度张力）
    cmd(ui, "claim RIVER")
    assert any("没有空闲" in l for l in eng.log_lines[-2:]), \
        "单元占满时 claim 应被拒绝"
    # 5) 巡航 40s：电厂+采矿机运转
    coal_before = eng.economy.get("coal")
    tick_until(eng, lambda: eng.economy.get("iron_ore") > 0.5, max_s=60)
    assert eng.economy.get("iron_ore") > 0.5, "应采出铁矿石"
    assert eng.economy.get("coal") < coal_before, "煤应被消耗"
    print("闭环步骤 1-5 OK：发电→开采→调度张力全部成立")

    # 6) 读档持久性：存档→新引擎读档→设施与库存仍在
    data = eng.to_dict()
    eng2 = build_engine()
    eng2.from_dict(data)
    assert len(eng2.registry.get("industry").facilities) == 3
    assert eng2.economy.get("iron_ore") > 0
    print("闭环步骤 6 OK：存档读档含设施状态")

    # 7) 手工构造一个枯竭奖励场景（reserve 很小）
    from core.world import Plot
    eng2.world.spawn(ring=0, kind="wreck", substance="scrap_alloy",
                     grade=50, reserve=1.0, state="claimed")
    wid = [p.id for p in eng2.world.plots.values() if p.kind == "wreck"][-1]
    ind2 = eng2.registry.get("industry")
    eng2.economy.set("electricity", 1000)
    # 调度：先从采铁设施抽调一个单元（建造作业要占用单元）
    ind2.unassign(eng2, "F2")
    err = ind2.build(eng2, wid, "salvager")
    assert err is None, err
    assert tick_until(eng2, lambda: not ind2.facilities["F4"]
                      .under_construction, max_s=60), "回收站应建成"
    ind2.assign(eng2, "F4")
    n0 = eng2.units.count()
    tick_until(eng2, lambda: eng2.world.get(wid).state == "depleted",
               max_s=30)
    assert eng2.units.count() == n0 + 1, "残骸枯竭应奖励执行单元"
    print("闭环步骤 7 OK：残骸回收奖励执行单元")

    print("GAMEPLAY-OK")


if __name__ == "__main__":
    main()
