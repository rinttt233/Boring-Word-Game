# 版本列表（Release List）

> 本仓库用 **git 标签**记录版本，并把每个版本发布到 **GitHub Releases**（见下方「GitHub Release 下载」）；
> 每个版本的完整更新报告见 [`CHANGELOG.md`](CHANGELOG.md)。
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

生成附件与说明（含排除规则）：

```bash
python -X utf8 tools/make_release_notes.py --version 1.3.1
# dist/release-notes-v1.3.1.md + dist/Boring-Word-Game-v1.3.1.zip
# 打包时排除 saves/ releases/ _refactor_backup/ dist/ __pycache__/，
# 以及只作本地留档的 AI 试玩报告（AI-playtest-report.txt / AI-playtest-code-analysis.txt）。
```

## GitHub Release 下载

| 版本 | Release 页面 | 附件（sha256） |
|---|---|---|
| 1.3.1 | [releases/tag/v1.3.1](https://github.com/rinttt233/Boring-Word-Game/releases/tag/v1.3.1) | `Boring-Word-Game-v1.3.1.zip`（108 文件，346 KB，`6ab578cf…7151`） |
| 1.3 | [releases/tag/v1.3](https://github.com/rinttt233/Boring-Word-Game/releases/tag/v1.3) | `Boring-Word-Game-v1.3.zip`（91 文件，259 KB，`f119768b…9bb9`） |
| 1.2 | [releases/tag/v1.2](https://github.com/rinttt233/Boring-Word-Game/releases/tag/v1.2) | `Boring-Word-Game-v1.2.zip`（81 文件，206 KB，`ad3a2db1…0ada`） |

> 本机 `github.com` 被 hosts 屏蔽，推送/发布需先起本地代理：
> `python -X utf8 tools/gh_proxy.py --port 8443`，再设
> `$env:HTTPS_PROXY=http://127.0.0.1:8443` 并用 `git -c http.sslBackend=openssl push`。
> `gh release create` 若带附件失败会回滚整个 Release —— **先建 Release，再单独 `gh release upload`**。

## 历史重写记录

**2026-09-13 —— 移除 AI 试玩报告**
- 目的：两份 AI 试玩报告（`AI-playtest-report.txt` / `AI-playtest-code-analysis.txt`）只作本地留档，
  不再出现在仓库的任何提交里。
- 手段：`git filter-branch --index-filter "git rm --cached --ignore-unmatch <两文件>" --tag-name-filter cat -- --all`
  → 删除 `refs/original` → `reflog expire --expire=now --all` → `gc --prune=now` → 强推 `main` 与 `v1.3.1`。
- 影响：`main` 与 `v1.3.1` 的提交 SHA 全部变化（`v1.2` / `v1.3` 未受影响）；**旧克隆必须重新 clone**
  （不要 `git pull`）。Release 附件不受影响（附件按标签名关联，已复核三份 zip 的 sha256 未变）。
- 回滚备份：`_refactor_backup/history_purge_<时间戳>/all_refs.bundle`（含重写前全部引用，本地留档）。

## 发布新版本（本项目约定）

```bash
python -X utf8 release.py <版本> "<主题>" [--patch] [--check]
# 归档 _refactor_backup/v<版本>_<时间戳>/ + 写 VERSION + 插 CHANGELOG 骨架 + 复制 releases/v<版本>/
# 之后补全 CHANGELOG 三节（主要改动 / 影响范围 / 验证），再提交并打标签：
git add -A && git commit -m "v<版本>: <主题>"
git tag -a v<版本> -m "v<版本> <主题>"
git push origin main --tags
```
