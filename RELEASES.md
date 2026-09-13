# 版本列表（Release List）

> 本仓库用 **git 标签**记录版本；每个版本的完整更新报告见 [`CHANGELOG.md`](CHANGELOG.md)。
> 版本号规则见 [`docs/versioning.md`](docs/versioning.md)。

| 版本 | 标签 | 日期 | 主题 | 本地归档 | 快照 |
|---|---|---|---|---|---|
| **1.3.1** | `v1.3.1` | 2026-09-13 | 品位接入 · 单元扩容 · 拆除复用 · 记忆口径 · 副产物积压 | `_refactor_backup/v1.3.1_20260913-075914/` | `releases/v1.3.1/` |
| **1.3** | `v1.3` | 2026-09-12 | 单元效能 · 建造耗时 · 记忆与维护压力 | `_refactor_backup/v1.3_20260912-192534/` | `releases/v1.3/` |
| **1.2** | `v1.2` | 2026-09-08 | 百科程序完成（WIKI 全流程） | `_refactor_backup/v1.2_20260912-164152/` | `releases/v1.2/` |
| 1.1 | — | — | 开发期里程碑（未正式归档）：M4 拓展性验收 + TL-0/TL-A + UI 重构 | — | — |
| 1.0 | — | — | 开发期里程碑（未正式归档）：内核 + M1 生产闭环 + M2 恢复树 + M3 胜利线 | — | — |

> 注：`_refactor_backup/` 与 `releases/` 是本地留档目录，**不入版本库**（见 `.gitignore`）；
> 需要随仓库分发的产物请见「发布产物」一节。

## 各版本要点

**v1.3.1（当前）**
- **缺陷修复（P0）**：`db` 命令崩溃、`report` 的 `migrated`/`can_migrate`/`victory` 语义、
  电力汇总把停摆发电机算作产电、`developed`/`depleted` 地块误报、读档能源死锁（一次性应急煤）。
- **结构性调整（P1）**：品位接入产出（参考品位归一）、执行单元装配厂 + 主线固化奖励单元、
  `demolish` 拆除返还 50%、记忆运转口径修正。
- **批次3**：副产物积压限产 + 四种应对（转化 / 放空 / 回注 / 政策开关）。
- **AI 试玩与盲测**：`tools/agent_play.py`、`tools/play_report.py`、`tools/blind_test.py`、
  `tools/make_guide.py`；入口文档 `AGENTS.md`（由 `content/guide.json` 生成）。
- 单测 **181 例**；主线 **14 条**；设施 **48** 个；百科 **232** 条。

**v1.3**
- 单元效能（×1.0~×2.5）与 4 条调度知识条目；建造耗时作业；记忆劣化随规模增长；
  维护件体系（维护站 / 待机 33% / 封存 / 故障停机）；基线测量工具。

**v1.2**
- 游戏内百科：`systems/wiki.py` 数据层 + GUI 百科页 + 控制台 `wiki`；自动词条 161 + 手写 53 条；
  双击跳转百科、词条轻量排版。

## 发布产物

仓库只跟踪**源代码与文档**（`core/ systems/ ui/ content/ tests/ tools/ docs/` + 根目录脚本）。
每个版本的**可运行快照**在本地 `releases/v<版本>/`（不入库），需要分发时可：
- 直接把 `releases/v<版本>/` 打包成 zip，作为 GitHub Release 附件上传；
- 或从对应标签自行 checkout：`git checkout v1.3.1`。

## 发布新版本（本项目约定）

```bash
python -X utf8 release.py <版本> "<主题>" [--patch] [--check]
# 归档 _refactor_backup/v<版本>_<时间戳>/ + 写 VERSION + 插 CHANGELOG 骨架 + 复制 releases/v<版本>/
# 之后补全 CHANGELOG 三节（主要改动 / 影响范围 / 验证），再提交并打标签：
git add -A && git commit -m "v<版本>: <主题>"
git tag -a v<版本> -m "v<版本> <主题>"
git push origin main --tags
```
