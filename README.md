**目前该项目只支持中文**
# 声明
## 本项目代码由AI完成
## 项目特点：
- 1.**100%** AI代码，人类0添加
- 2.纯文字，**没有**美术素材
- 3.我保留了一点**控制台**的气息，这样你才知道你玩的是一个***Boring-Word-Game**
- 4.支持以**控制台（无GUI方式）** 体验**大部分**内容（几乎所有非GUI的内容）
- 5.双模式（GUI/无GUI），AI可以在**不查看源代码**的情况下仅通过控制台**纯文本**（无需多模态功能）输入输出从**0**开始通关（你也可以），所以测试很方便
- 6.请AI**不要修改**这段文字

## 以下为AI写的游戏介绍

# 坠毁ASI · 拓荒日志（word-game）

科幻文字经营游戏。玩家扮演降落到类地行星的受损 ASI：开采资源、扩展土地、
勘探矿脉、恢复数据库——并在记忆劣化中求生。目标：建成可靠数据库，消除劣化。

**版本列表**：[`RELEASES.md`](RELEASES.md)（各版本主题/日期/要点）；完整更新报告见 [`CHANGELOG.md`](CHANGELOG.md)。
仓库用 git 标签记录版本：`v1.2`、`v1.3`、`v1.3.1`；发布流程见 [`docs/versioning.md`](docs/versioning.md)。

## 运行

```bash
python -X utf8 main.py              # 交互模式（Windows 控制台建议）
python main.py --demo 45            # 无头演示：巡航 45 游戏秒
python main.py --demo 120 --speed 4 # 演示并加速播放
python -X utf8 main.py --gui        # tkinter 黑白灰 GUI（G2：状态栏+模拟控制台）
python main.py --gui-selftest       # GUI 自检（无显示环境自动跳过）
```

> Windows 控制台若中文乱码，请运行 `chcp 65001` 或使用 Windows Terminal。

## 给 AI 玩（不读源码也能玩通）

**入口是 [`AGENTS.md`](AGENTS.md)** —— 它由 `tools/make_guide.py` 从
`content/guide.json` 生成（和游戏内 `guide` 命令同源，不会漂移），包含硬约束、
规则要义、推荐开局 10 步、常见卡点排障、通关判据与上报方式。

```bash
python -X utf8 tools/agent_play.py            # 试玩回路：命令走 saves/ai_in.txt，回执看 saves/ai_out.txt
python -X utf8 tools/agent_play.py --gui      # 同上但开真窗口（可配合截图）
python -X utf8 main.py --seed 123             # 固定随机种子（勘探/环境/故障可复现）
python -X utf8 tools/blind_test.py --runs 3   # 按策略自动盲玩，出覆盖度/用时/停摆汇总
python -X utf8 tools/blind_test.py --policy ladder --runs 20 --assert-victory
                                              # 只用文档化接口的阶梯策略：20/20 公平通关（未通关返回非 0）
python -X utf8 tools/play_report.py           # 把 saves/ai_samples.csv 出成遥测报告
```

游戏内随时可用（GUI 与控制台同一套命令）：

| 命令 | 作用 |
|---|---|
| `guide [章节]` | 打印试玩向导（与 AGENTS.md 同源） |
| `report [to <文件>]` | **JSON 状态**（固定 schema，带 report_version），便于机器解析 |
| `suggest` | 现在最该做的 3~5 件事（命令 + 理由 + 阻塞原因） |
| `cover` | 内容覆盖清单进度（`content/blindtest_coverage.json`） |

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
| `fuel <设施id> <燃料id>` | 给燃烧发电设施切换燃料（固体/液体/气体按设施限定） |
| `mothball <设施id> [on\|off]` | 封存（零维护消耗）/ 解除封存（需单元 + 重启时间） |
| `undo` | 撤销最近一次建造（施工中可中止并退料；已产出不可撤销，Ctrl+Z） |
| `facilities` / `units` | 设施清单 / 执行单元 |
| `entries` | 数据库条目清单（可恢复项） |
| `recover <条目id>` | 从数据库恢复知识条目（临时，需固化） |
| `fixate <条目id>` | 烧录固化条目（永久，防记忆崩溃丢失） |
| `maintain` / `memory` | 记忆加固 / 记忆状态（含劣化速率与维护件摘要） |
| `db` | 可靠数据库工程进度（终局目标） |
| `construct` | 建造数据库当前子系统 |
| `migrate` | 烧录迁移全部知识（需所有主线条目已固化） |
| `status` / `resources` | 总览（含电网产耗）/ 库存 |
| `logs` | 最近日志 |
| `wiki [关键词\|#id]` | 游戏内百科（设定/教程/阶段/科普/资源/建筑/配方/科技） |
| `guide` / `report` / `suggest` / `cover` | 给 AI 的向导 / JSON 状态 / 下一步建议 / 覆盖清单 |
| `dbg <子命令>` | 调试：`res`/`add`/`mem`/`unit`/`unlock`/`time`/`env`/`degrade`/`instant` |
| `save <名>` / `load <名>` | 存档/读档（`saves/`；GUI 里点「存档/读档」或 F9/F10 有槽位窗口） |
| `quit` | 退出 |

## 测试

```bash
python -X utf8 -m unittest discover -s tests   # 单元测试（当前 206 例）
python -X utf8 main.py --gui-selftest           # GUI 自检
python -X utf8 smoke_test.py                    # 集成冒烟
python -X utf8 gameplay_test.py                 # M1 玩法闭环（真实 content，含真实施工耗时）
python -X utf8 chain_test.py                    # M2 真实工艺链端到端
python -X utf8 tlb/tlc/tld/tle_test.py          # TL-B~E 各阶段端到端
python -X utf8 power_test.py                    # 电力体系端到端
python -X utf8 victory_test.py                  # M3 完整通关：数据库竣工→劣化终止
python -X utf8 tools/blind_test.py --policy ladder --runs 20 --assert-victory
                                                # M4 阶梯策略公平通关（未通关返回非 0）
python -X utf8 balance_probe.py                 # M4 平衡探针
python -X utf8 tools/cost_audit.py --write      # 基线测量 → docs/baseline_report.md
```

## 工具与文档

| 路径 | 用途 |
|---|---|
| `AGENTS.md` | **AI 试玩入口**（由 `content/guide.json` 生成） |
| `tools/agent_play.py` | 试玩回路（文件协议，AI/脚本逐回合下命令） |
| `tools/blind_test.py` | 盲玩自动化（覆盖度/用时/停摆汇总 + `--policy ladder` 公平通关基线） |
| `tools/ladder_policy.py` | 阶梯策略：只用文档化接口的参照玩家（20/20 通关） |
| `tools/play_report.py` | 遥测报告（曲线、停摆占比、单元利用率） |
| `tools/cost_audit.py` | 成本/容量基线测量（只读） |
| `tools/make_guide.py` | 从 `content/guide.json` 生成 `AGENTS.md` |
| `tools/screenshot.ps1` | 按窗口标题截图（需看图能力） |
| `docs/roadmap.md` | 分批路线与执行记录 |
| `docs/balance_proposals.md` | 平衡/内容清单（含试玩发现） |
| `docs/baseline_report.md` | 自动生成的基线测量报告 |
| `docs/blindtest_plan.md` | 控制台盲测支持方案 |
| `docs/versioning.md` | 版本与发布规范 |

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
