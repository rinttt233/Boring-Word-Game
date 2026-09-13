# 方案：控制台「盲测」支持（让 AI 只靠文本体验并验证本作）

> 目标读者：想把本作交给 AI（或脚本）**盲测**的人 —— 没有画面、没有鼠标，
> 只有"读文本 → 下命令 → 再读文本"。本方案在不改玩法规则的前提下，把
> **可观测、可行动、可评估**三件事补齐，使 AI 能覆盖大部分内容并产出量化结论。
> 现状基础：`tools/agent_play.py`（试玩回路）+ 同一份 CommandRouter（GUI/控制台共用）
> + `engine.stats` 埋点 + `tools/play_report.py` 遥测报告。

---

## 一、目标与判定标准

| 目标 | 判定标准（可量化） |
|---|---|
| 体验大部分内容 | **覆盖清单**（§四）完成度 ≥ 目标比例，且每项有触发证据 |
| 能自查"卡住了" | 连续 N 游戏秒关键量无变化 → 记 `STUCK` 并落盘快照 |
| 能做横向对比 | 同一 seed 复现同一局；不同 seed 的 10 局可对比通关率/用时/卡点 |
| 结论可读 | 一局结束产出：覆盖度表 + 遥测报告 + 卡点/失败现场 |

**不追求**：真实手感、美术、音效、鼠标流畅度（这些必须人来看，或需要视觉模型）。

---

## 二、三层结构

### 1) 观测层：机器可读的状态（`report`）

现有 `status` / `plots` / `units` 是给人看的表格；AI 需要**可解析**输出。

- 新命令 `report`：输出 **JSON**（一行或缩进皆可），字段固定并带 `report_version`：
  ```json
  {"report_version": 1, "t": 720.5, "paused": true,
   "memory": {"integrity": 80.4, "degrade": 0.028, "crashed": false},
   "units": {"total": 3, "idle": 1, "efficiency": 1.25,
             "list": [{"id": "U1", "task": "F1"}]},
   "power": {"produce": 1.6, "consume": 0.1, "net": 1.5, "stored": 137.5},
   "facilities": [{"id": "F1", "def": "power_plant", "plot": "HOME",
                   "assigned": ["U1"], "state": "stalled",
                   "reason": "输入不足", "upkeep": 100}],
   "plots": [{"id": "P2", "ring": 1, "kind": "ore", "substance": "coal",
              "state": "known", "grade_est": 84, "reserve": 4101}],
   "entries": {"permanent": ["db_coking"], "active": [], "available": ["db_steel"]},
   "database": {"built": 2, "projects": 5, "can_migrate": false},
   "maintenance": {"enabled": true, "kit": 12.0, "demand": 0.15, "worst": 88},
   "stats": {"stall_seconds": 538, "breakdowns": 0, "maintains": 0}}
  ```
- `report to <file>`：写入文件，便于 agent 逐回合 diff（比在日志里翻更稳）。
- 人类可读性不受影响：`report --md` 或现有命令继续用。

### 2) 行动层：降低"多步操作"门槛（`suggest` / `play`）

AI 最容易卡在"不知道下一步该干什么"。给出**建议**（不自动执行，保留决策权）：

- `suggest`：基于状态推导**当前最该做的 3~5 件事**，每条含"命令 + 理由 + 阻塞原因"：
  ```
  1) build HOME power_plant        ← 无发电设施，全部产线会停摆
  2) assign F1                     ← F1 已建成但无执行单元
  3) survey 1                      ← 煤仅够 90s，需找矿脉
  4) recover db_coking (缺 电30)    ← 可恢复：解锁焦炉
  5) claim WRECK1                  ← 残骸回收可返还执行单元
  ```
- `play <剧本名>`：内置剧本（`content/scripts/*.json`），一键把局面推进到某个阶段，
  便于**跳过已验过的前期、直接测中后期**：
  - `play opening`：标准开局 10 分钟（发电→采铁→焦化）
  - `play tl-c`：稀有金属线起点（含已固化条目与单元池）
  - `play endgame`：数据库 5 子系统前夜（直接测终局与结算）
  剧本 = 一串命令 + 断言，可复用为**回归用例**。
- `auto <秒>`：按简单规则自动巡航（缺电→建电站、空闲单元→补员…），用于"观察"而非"微操"。

### 3) 评估层：覆盖清单 + 卡死判定 + 批量盲测

**覆盖清单**（新文件 `content/blindtest_coverage.json`）：把"要体验的内容"写成可判定条目：

```json
{"items": [
  {"id": "survey",  "desc": "勘探发现地块", "assert": "len(plots) >= 5"},
  {"id": "claim",   "desc": "占领地块",     "assert": "any(p.state=='claimed')"},
  {"id": "build",   "desc": "建造设施并完工", "assert": "any(f.state!='building')"},
  {"id": "assign",  "desc": "派员运转",     "assert": "units.idle < units.total"},
  {"id": "fuel",    "desc": "切换燃料",     "assert": "any(f.fuel != null)"},
  {"id": "stall",   "desc": "经历一次停摆", "assert": "stats.stall_seconds > 0"},
  {"id": "recover", "desc": "恢复条目",     "assert": "len(entries.active)+len(entries.permanent) > 0"},
  {"id": "fixate",  "desc": "固化条目",     "assert": "len(entries.permanent) > 0"},
  {"id": "crash",   "desc": "经历记忆崩溃", "assert": "memory.crashed == true"},
  {"id": "maintain","desc": "加固记忆",     "assert": "stats.maintains > 0"},
  {"id": "kit",     "desc": "维护件断供",   "assert": "maintenance.kit <= 0.05"},
  {"id": "mothball","desc": "封存设施",     "assert": "any(f.mothballed)"},
  {"id": "renewable","desc": "可再生昼夜出力变化", "assert": "见过 solar/pv 出力 0 与非 0"},
  {"id": "env",     "desc": "环境事件影响生产", "assert": "见过 production 倍率 != 1"},
  {"id": "byproduct","desc": "副产物入库",  "assert": "coaltar > 0"},
  {"id": "victory", "desc": "数据库竣工",   "assert": "database.built == database.projects"}
]}
```

- `cover` 命令：输出 `已覆盖 11/16`＋未覆盖项＋每项的**触发提示**（例如
  `crash`: "让完整度自然归零或 dbg mem 0"）。**这就是盲测的目标函数**。
- **卡死判定**：`suggest` 无可用项 且 连续 `--stuck-seconds`（默认 600）内
  `report` 关键量无变化 → 记 `STUCK`，附 `report` 快照 + 最后 50 行日志 + 截图（GUI 模式）。
- **批量盲测** `tools/blind_test.py`：
  ```
  python -X utf8 tools/blind_test.py --runs 10 --seed-base 100 \
      --policy suggest|scripted|random --out docs/blindtest_report.md
  ```
  每局输出：结果（通关/卡死/超时）、用时、覆盖度、死亡/卡点；汇总成表
  （通关率、平均用时中位数、最常见卡死点 Top3、覆盖缺口 Top3）。
  `--policy` 让同一批测不同策略，便于区分"游戏难"与"策略差"。

---

## 三、与现有代码的接缝

| 组件 | 落点 | 说明 |
|---|---|---|
| `report` / `suggest` / `cover` | `ui/commands.py` 新增命令 | 与 GUI/控制台共用；纯读，不改玩法 |
| 覆盖清单 | `content/blindtest_coverage.json` | 数据驱动，随内容增长补条目 |
| 剧本 | `content/scripts/*.json` | 命令序列 + 断言，同时是回归用例 |
| 批量盲测 | `tools/blind_test.py` | 驱动 `agent_play` 或直接内嵌引擎 |
| 遥测聚合 | `tools/play_report.py` 复用 | 单局报告已有，需加"多局汇总" |
| 截图 | `tools/screenshot.ps1` | 失败现场留证（需视觉模型或人工查看） |

**前置小改（必须）**：`systems/survey.py` 目前 `SurveySystem(regions_cfg)` 不接受
seed（`random.Random()` 无参），盲测要"同 seed 复现同一局"就得加
`SurveySystem(regions_cfg, seed=None)`；`EnvironmentSystem` 已有 `seed`。
（改动极小，但属于"可复现性"的地基。）

---

## 四、分期实施建议

| 期 | 内容 | 价值 | 预估 | 建议模型 |
|---|---|---|---|---|
| **M1** | `report`（JSON）+ `suggest` | AI 能读懂状态、知道下一步 → 立刻可盲测 | 30–40 min | pro |
| **M2** | 覆盖清单 + `cover` + survey seed | 盲测有目标函数与可复现性 | 30 min | pro |
| **M3** | `tools/blind_test.py` 批量 + 汇总报告 + 卡死落证 | 产出"通关率/卡点"这类可对比结论 | 40 min | pro |
| **M4** | 剧本 `play opening/tl-c/endgame` | 跳过前期直测中后期；剧本即回归用例 | 30–45 min | pro |

**建议先做 M1+M2**（约 1 小时）即可开始有效盲测；M3/M4 视需要再加。

---

## 六、执行记录

| 期 | 状态 | 交付物 |
|---|---|---|
| **M1 观测+行动层** | ✅ 完成 | `ui/agent_api.py`（`build_report` 固定 schema JSON / `suggest_actions` 14 条规则 / `CoverageTracker` / `guide_text`）+ 命令 `report [to <file>]`、`suggest`、`cover`、`guide`（与 GUI 共用同一路由）；`help` 增加 AI/盲测提示 |
| **M2 覆盖清单 + 可复现** | ✅ 完成 | `content/blindtest_coverage.json`（22 项，粘性判定）；`SurveySystem(regions_cfg, seed=None)` + `main.py --seed N`（勘探/环境/故障三处随机统一可复现，已由测试验证"同 seed 同世界"） |
| **M2.5 AI 入口** | ✅ 完成 | `content/guide.json`（唯一来源）→ `tools/make_guide.py` 生成根目录 `AGENTS.md`（硬约束/规则要义/开局 10 步/排障/接口/上报）+ 游戏内 `guide` 同源渲染；README 增补"给 AI 玩"与工具索引、修正过时命令表 |
| **M3 批量盲测** | ✅ 可用（策略有限） | `tools/blind_test.py`（`--runs/--seed-base/--policy suggest\|random\|none`、覆盖度/用时/停摆汇总、Markdown 落盘）+ `tests/test_agent_api.py`（16 例） |
| M4 剧本（`play opening/tl-c/endgame`） | ⏳ 未做 | 需要时可加 |

### M3 基线结果（如实记录）

`python -X utf8 tools/blind_test.py --runs 2 --seed-base 100 --max-minutes 300`：

| seed | 结果 | 用时 | 覆盖 | 设施 | 单元(效能) | 固化 | DB |
|---|---|---|---|---|---|---|---|
| 100 | 未通关（到时间上限） | 5:00:07 | 12/22 | 6 | 6(×1.25) | 6 | 0/5 |
| 101 | 未通关（到时间上限） | 5:02:40 | 11/22 | 23 | 4(×1.0) | 1 | 0/5 |

**结论（重要，避免误读）**：
- 工具链本身可用：能自动跑、能出覆盖度/停摆/用时与 Markdown 报告、能复现同一局。
- **内置的 `suggest` 阶梯策略不是 AI 玩家**：它只会"跟着优先级列表做最简单的一步"，
  在"腾单元 ↔ 建厂/研究"的资源取舍上不会规划，因此长期停在 11~14/22 覆盖面、
  走不到钢铁链之后的产线，也守不住记忆（实测记忆归零 1~2 次）。
- 因此 **盲测结论当前只能用于"覆盖缺口/卡点/遥测"，不能用于"论证游戏可通关"**；
  可通关性仍由 `victory_test.py`（13 条主线全固化 → 数据库竣工）保证。
- 想让盲测也具备"规划能力"，下一步是给它一个真正的策略层（LLM 或更强的启发式），
  或者做 M4 剧本把中后期直接铺好再测。


## 七、风险与注意

1. **JSON 契约要稳**：加 `report_version`，字段只增不改；AI 的断言依赖它。
2. **`suggest` 只建议不执行**：保留 AI/玩家的决策权，避免"自动玩游戏"消解玩法。
3. **可复现性**：勘探/环境/维护/故障四处随机都要能 seed；否则盲测结论不可比。
4. **不要用盲测替代人玩**：它能发现"卡死/不可达/数值失衡/覆盖缺口"，
   但不能判断手感与美观 —— 那部分仍需人（或视觉模型看截图）。
5. **`dbg` 只用于隔离变量**（如 instant 建造、加单元），正式统计里要标明是否使用过。
6. **区分"工具可用"与"策略能赢"**：见 §六 执行记录 —— 覆盖/卡点/遥测可用，
   通关论证仍以 `victory_test.py` 为准。
