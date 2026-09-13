# 坠毁 ASI · 拓荒日志 —— 给 AI 的试玩向导（不读源码也能玩通）

> **本文件由 `tools/make_guide.py` 从 `content/guide.json` 生成，请勿手改；改内容请改 `content/guide.json` 后重新生成。**

## 硬约束（先读这段）

- **不要读 core/ 与 systems/ 的源码**：本文件 + 游戏内 `guide` / `wiki` / `help` / `report` / `suggest` 已足够玩通。
- 只用文档化接口下命令；数值与规则以游戏内 `report`/`wiki` 与本文为准。
- 遇到卡点先 `report` + `suggest`，再查本文「常见卡点」一节。

## 三步开始

- 启动试玩回路（长驻进程，默认暂停，只在被要求时推进时间）：
```bash
python -X utf8 tools/agent_play.py            # 无头（推荐给 AI）
python -X utf8 tools/agent_play.py --gui      # 开真窗口（可选，配合截图）
python -X utf8 main.py --seed 123             # 固定随机种子（可复现同一局）
```
- 然后把命令**追加**到 `saves/ai_in.txt`，从 `saves/ai_out.txt` 读回执。
- 回执里的 `> 命令` 段落 = 该命令的结果；`@view` = 完整状态摘要。

## 目标：建成可靠数据库，让记忆不再流失

- **终局**：数据库 5 个子系统全部建成（`db` 看进度、`construct` 逐个建造），然后 `migrate` 烧录迁移。
- **迁移的前置**：所有**主线**知识条目必须已永久固化（`entries` 里非 optional 的 13 条）；未固化会迁移失败。
- 没有敌人、没有时间限制，唯一的对手是**记忆劣化**与**执行单元稀缺**。


## 必须知道的 10 条规则

- **执行单元是唯一硬瓶颈**：开局 3 个。设施必须有单元才运转；**勘探、占领、建造、恢复、固化、加固、解封重启也都要占单元**。
- **建造要时间**（各设施 5~30 游戏秒）：开工时扣材料并占一个单元，建造中不产不耗、不可派员；`undo` 可中止并全额退料。
- **单元效能**（×1.00 → ×2.50）：恢复调度类知识（执行单元总线复用 / 并行作业编排等）会同时提高产能与作业速度；条目丢失效能立刻回落。
- **电力**：配方里的 electricity 是普通输入 —— 缺电按比例减产（不会停机）。燃煤电站 `power_plant` 烧煤 0.4/s；光热/光伏只在白天出力，风电全天（沙暴增强）；燃烧设施用 `fuel <设施id> <燃料id>` 换燃料。
- **记忆劣化随规模增长**：劣化 = 0.02 + 0.002×已固化条目数 + 0.004×运转设施数（每秒）。`maintain` 加固恢复量越接近满值越少。完整度归零 → **记忆崩溃** → 所有**未固化**条目丢失，依赖它的设施立即停摆。
- **恢复 ≠ 完成**：`recover <条目>` 只是临时可用（约 90 秒），必须 `fixate <条目>` 永久固化。**先固化正在用的产线条目**。
- **维护件**（恢复 `db_upkeep` 后启用）：所有设施持续消耗维护件（运转 100%、停用的 33%、`mothball` 封存的 0%）。断供 → 设备状态下滑 → 概率故障停机。用 `maintenance_depot` 生产。
- **勘探有估值误差**（±若干 %），开采会逐步逼近真值；圈层越远**物流惩罚越高**（圈1 ×2、圈2 ×4 …），远矿更贵。
- **环境事件**（沙暴/电离风暴/陨石/极光）会乘算生产、记忆劣化与可再生出力；`environment` / `report` 可看当前事件。
- **副产物直接入库**（焦炉煤气/煤焦油/氢…），它们往往就是上游原料；矿脉会枯竭（地块变 depleted、设施自动拆除），残骸回收站枯竭时会**返还一个执行单元**。
- **品位决定产出速率**：采掘产出 = 标称速率 × clamp(品位/参考品位, 0.25, 2.0) × 单元效能 × 环境倍率 × 供电比例。参考品位是各矿的中位值（如铁 51%、煤 78%、铜 2.1%）：中位品位的矿正好是标称产量，贫矿最低打到 25%、富矿最高翻倍。**圈层物流惩罚目前只是显示（预留），不参与产出计算。**
- **设施可以拆除**（`demolish <设施id>`，返还建造成本的一半，地块恢复可建）；采空的 depleted 地块也能重建。一地块一设施仍然成立。
- **执行单元可以造**：恢复主线 `db_units` 后解锁「执行单元装配厂」（钢 0.08/s + 铜 0.05/s + 电 0.6/s → 约 200 秒 1 个单元）；另外**每永久固化 2 条主线条目自动 +1 单元（上限 +6）**。所以中后期不要再靠 `dbg unit` 补单元。
- **副产物积压会限产**：焦炉煤气/煤焦油/高炉煤气/裂解干气/氢/钒 超过阈值时，产出它的设施按 `clamp(阈值/库存, 0.15, 1.0)` 限产。四种应对：①转化（水煤气变换炉：煤气→氢）②放空塔（烧掉，浪费但最快）③回注井（压进地下，容量满则停）④`policy backlog ignore`（关闭限产，纯沙盒）。


## 推荐开局 10 步（照抄即可）

1. `plots` 看已知地块；`claim IRON1`、`claim WRECK1`（占领要占单元，两个单元并行）。
2. `build HOME power_plant` → `@tick 20` → `assign F1`（先解决电）。
3. `build IRON1 extractor` → `@tick 20` → `assign F2`（采铁）。
4. `survey 1` 找**煤矿**（开局库存只有 60 煤，电站 0.4/s ≈ **150 秒**见底；`report` 看 coal）。
5. `claim <煤矿地块>` → `build <煤矿> extractor` → 等完工 → `assign F3`（第三个单元给煤矿）。
6. 煤快没了而单元不够：`unassign` 掉不急的设施（如采铁），或 `mothball <设施>` 封存。
7. `entries` 看可恢复条目；`recover db_coking` → 等作业完 → **立刻 `fixate db_coking`**。
8. `build <空闲地块> cokery` → `assign <F?>`（焦炭是高炉燃料）。
9. `recover`/`fixate db_blast_furnace` → 建 `blast_furnace` → 派员 → 出铁后接 `steel_mill`。
10. 自此沿主线推进：`db_sulfuric → db_ammonia → db_petrol → db_distill → db_copper → db_refractory → db_rare_earth → db_upkeep → db_unit_bus → db_unit_parallel`，每步都是「recover → fixate → build 设施 → assign」，并保持：记忆 ≥ 40%、有维护站、电站 ≥ 2 座、空闲单元 ≥ 1。


## 常见卡点与排障

- **设施停摆（`report` 里 state=stalled）** → 看 reason：`输入不足`=上游断供（查该产线上游单元与原料）；`缺电`=加电站/给燃烧设施设燃料；`缺燃料`=没设或燃料类别不符（`fuel_options`）；`知识条目已丢失`=重新 recover 或 fixate；`维护失效停机`=维护件断供。
- **命令报「没有空闲执行单元」** → `units` 看谁被占用；`unassign <不急的设施>` 或 `mothball <设施>` 腾出单元；建造/勘探/恢复都在抢单元，排队是常态。
- **煤/燃料耗尽导致断电** → 立刻 `survey 1` 找煤矿并占领开采；同时 `fuel <设施> <燃料>` 改用现有燃料（如焦炭、煤焦油、汽油）；实在不行 `mothball` 掉耗电产线保命。
- **记忆完整度一路下滑** → `maintain` 加固（占一个单元）；把关键条目 `fixate`；`dbg degrade off` 仅用于隔离实验，正式试玩不要用。
- **维护件断供、设备状态下滑** → 建/加 `maintenance_depot`（钢 + 残骸合金造维护件）；`mothball` 停掉不用的设施把需求降下来。
- **建不了某设施** → 三种原因：该设施需要先恢复对应条目（`entries` 里看 unlocks_facility）；地块类型不符（矿脉/水源只能建采矿机、残骸只能建回收站、工厂要空地）；材料不足（`resources`）。
- **`migrate` 失败** → 仍有主线条目未固化 —— `entries` 看非 optional 且状态不是「已固化」的条目，逐个 `recover` + `fixate`。


## Agent 接口与协议

- `report` 输出**固定 schema 的 JSON** 状态（含 report_version，字段只增不改）：time/memory/units/power/facilities/plots/entries/database/maintenance/stats。
- `report to saves/last.json` 写入文件，便于逐回合 diff。
- `suggest` 给出**当前最该做的 3~5 件事**（命令 + 理由 + 阻塞原因）。不知道下一步就问它。
- `cover` 显示**内容覆盖清单**进度（`content/blindtest_coverage.json`），盲测时以它为进度条。
- `guide` 打印本向导；`guide <章节id>` 看单节（goal/rules/opening/trouble/agent_api）。
- 试玩回路的 agent 指令：`@view`（状态摘要+日志尾部）、`@stats`、`@tick <秒> [步长]`（推进时间）、`@run <秒>`、`@pause`、`@resume`、`@note <文字>`、`@quit`。
- 任意游戏命令都可直接用（`build/assign/claim/survey/recover/fixate/maintain/fuel/mothball/construct/migrate/report/suggest/cover/...`）。


## 怎么算完成、怎么上报

- **通关判据**：`db` 显示 5/5 且 `migrate` 成功（日志出现「烧录迁移完成 / 记忆劣化永久终止」），`report` 里 database.built == database.projects 且 memory 显示劣化终止。
- **上报内容**：游戏内用时（`report.t`）、覆盖度（`cover`）、关键卡点与处置、是否用了 `dbg`（用了要说明）。
- **遥测**：试玩回路会写 `saves/ai_samples.csv`，用 `python -X utf8 tools/play_report.py` 出曲线与占比报告。
- **可复现**：`main.py --seed <N>` 固定勘探/环境/故障随机；同 seed 同命令序列应得到同一局。


---

## 文件导航（除了本文件，你只需要这些）

| 路径 | 用途 |
|---|---|
| `main.py` | 启动游戏（`--gui` / `--demo N` / `--seed N`） |
| `tools/agent_play.py` | 试玩回路（文件协议，AI 推荐入口） |
| `tools/play_report.py` | 把 `saves/ai_samples.csv` 出成遥测报告 |
| `tools/blind_test.py` | 按策略自动盲玩并汇总（覆盖度/卡点） |
| `tools/screenshot.ps1` | 截取游戏窗口为 PNG（需看图能力） |
| `content/wiki/*.json` | 游戏内百科原文（`wiki` 命令可查） |
| `content/guide.json` | 本文件的内容源 |

游戏内随时可用：`help` / `guide` / `wiki <关键词>` / `report` / `suggest` / `cover` / `status`。
