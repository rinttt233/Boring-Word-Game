# 坠毁ASI · 拓荒日志（word-game）

科幻文字经营游戏。玩家扮演降落到类地行星的受损 ASI：开采资源、扩展土地、
勘探矿脉、恢复数据库——并在记忆劣化中求生。目标：建成可靠数据库，消除劣化。

## 运行

```bash
python -X utf8 main.py              # 交互模式（Windows 控制台建议）
python main.py --demo 45            # 无头演示：巡航 45 游戏秒
python main.py --demo 120 --speed 4 # 演示并加速播放
python -X utf8 main.py --gui        # tkinter 黑白灰 GUI（G2：状态栏+模拟控制台）
python main.py --gui-selftest       # GUI 自检（无显示环境自动跳过）
```

> Windows 控制台若中文乱码，请运行 `chcp 65001` 或使用 Windows Terminal。

## 交互指令

| 命令 | 作用 |
|---|---|
| `pause` / `resume` / `toggle` | 暂停/继续（快捷键：空格） |
| `speed <倍率>` | 播放倍率 0.25~8 |
| `cruise <秒>` / `cruise off` | 巡航 N 游戏秒后自动暂停 / 取消 |
| `survey <圈层1-5>` | 派执行单元勘察圈层，作业完成后发现新地块 |
| `plots` | 已勘察地块清单（矿种/品位/储量/设施） |
| `claim <地块id>` | 占领已勘探地块 |
| `build <地块id> <设施id>` | 在已占领地块建厂（`facilities_help` 看图鉴，部分需先恢复条目） |
| `assign <设施id>` / `unassign` | 分配/调离执行单元到设施 |
| `facilities` / `units` | 设施清单 / 执行单元 |
| `entries` | 数据库条目清单（可恢复项） |
| `recover <条目id>` | 从数据库恢复知识条目（临时，需固化） |
| `fixate <条目id>` | 烧录固化条目（永久，防记忆崩溃丢失） |
| `maintain` / `memory` | 记忆加固 / 记忆状态 |
| `db` | 可靠数据库工程进度（终局目标） |
| `construct` | 建造数据库当前子系统 |
| `migrate` | 烧录迁移全部知识（需所有条目已固化） |
| `status` / `resources` | 总览（含电网产耗）/ 库存 |
| `logs` | 最近日志 |
| `save <名>` / `load <名>` | 存档/读档（saves/ 目录） |
| `quit` | 退出 |

## 测试

```bash
python -m unittest discover -s tests   # 46 单元测试
python smoke_test.py                   # 集成冒烟
python gameplay_test.py                # M1 玩法闭环（真实 content）
python chain_test.py                   # M2 真实工艺链端到端
python victory_test.py                 # M3 完整通关：数据库竣工→劣化终止
python balance_probe.py                # M4 平衡探针（数值不变量+早期冒烟）
```

## 结构

```
core/       模拟内核（与 UI 完全解耦，可 headless 测试）
  bus.py       事件总线（模块解耦）
  clock.py     游戏时钟（变速/暂停/cruise）
  economy.py   资源经济（断供即停）
  world.py     地块（圈层/品位/储量/状态/物流惩罚）
  units.py     执行单元调度
  jobs.py      作业队列（勘探/占领等耗时任务，占用执行单元）
  registry.py  模块注册表（start/tick/to_dict/load）
  engine.py    总装配（tick 唯一入口 + JSON 存档）
systems/    活动模块（每模块一个玩法循环）
  survey.py    勘探：作业完成后按圈层概率池发现矿脉
  claim.py     扩展：占领地块
  industry.py  工业：设施建造/单元分配/配方生产/开采枯竭/恢复门控/电网汇总
  memory.py    记忆劣化/加固 + 崩溃事件 + 劣化终止
  recovery.py  数据库恢复树：恢复/固化条目，门控设施
  database.py  终局工程：可靠数据库五子系统 → 烧录迁移 → 通关
  environment.py 第5模块(验收样例)：行星环境事件调制生产/记忆
content/    全部游戏内容 = JSON 数据
ui/         薄界面层（实时 tick + 按键即暂停）
saves/      存档（运行时生成）
```

## 里程碑

- M0 ✅ 内核骨架：时钟/变速/暂停/cruise、地块、资源、单元、注册表、事件总线、JSON 存档
- M1 ✅ 最小闭环：勘探→扩展→开采→生产 + 记忆劣化/加固维护 + 执行单元调度（可玩）
- M2 ✅ 恢复树 + 真实工艺链：煤焦化→高炉→炼钢→合成氨/接触法硫酸；条目门控 + 固化保护
- M3 ✅ 完整 v1：可靠数据库终局线（五子系统+烧录迁移+劣化终止）+ 电网汇总 + 引导文本
- M4 ✅ 拓展性验收（第5模块环境系统零内核改动）+ 平衡校准（出生残骸/3单元/速率调整）
