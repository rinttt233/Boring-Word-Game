#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_release_notes.py —— 为一个版本生成 GitHub Release 的说明与可下载快照。

产物（默认写入 dist/）：
    dist/release-notes-v<版本>.md        发布说明（通用头 + CHANGELOG 对应章节）
    dist/Boring-Word-Game-v<版本>.zip    该版本的可运行快照（排除 saves/缓存）

用法：
    python -X utf8 tools/make_release_notes.py --version 1.3.1
    python -X utf8 tools/make_release_notes.py --version 1.3.1 --no-zip
    python -X utf8 tools/make_release_notes.py --version 1.2 --snapshot releases/v1.2

之后（本机 github.com 若被 hosts 屏蔽，需先起代理，见 tools/gh_proxy.py 与
tools/push_to_github.ps1 顶部说明）：
    gh release create v<版本> --title "v<版本> <主题>" \
        --notes-file dist/release-notes-v<版本>.md --latest
    gh release upload v<版本> dist/Boring-Word-Game-v<版本>.zip
注意：`gh release create` 若带附件失败会回滚删除 Release —— 先建 Release，再单独 upload。
"""
import argparse
import io
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"saves", "__pycache__", ".git", "releases", "_refactor_backup",
             "dist"}
HEADER = """# 坠毁 ASI · 拓荒日志 —— v{version}

科幻文字经营游戏：你是坠落到类地行星的受损 ASI，在**记忆持续劣化**中开采、扩张、
恢复数据库，最终建成可靠数据库让记忆不再流失。无敌人、无时限，唯一的对手是
**记忆劣化**与**执行单元稀缺**。

## 下载与运行
- 附件 **`Boring-Word-Game-v{version}.zip`** 是本版本的可运行快照（解压即用）；
  也可以直接下载 GitHub 自动生成的两个源码包（Source code）。
- 需要 **Python 3.8+**（只用标准库；Windows 上建议 `python -X utf8` 以正确显示中文）。
- 双击 `启动游戏_GUI.bat`（图形界面），或：
  ```bash
  python -X utf8 main.py --gui        # 黑白灰 GUI
  python -X utf8 main.py              # 纯终端
  python -X utf8 main.py --seed 123   # 固定随机种子（可复现同一局）
  ```
- 想交给 AI 玩？入口是仓库里的 **`AGENTS.md`**（由 `content/guide.json` 生成，和游戏内
  `guide` 命令同源），配套 `tools/agent_play.py`（试玩回路）与游戏内
  `report` / `suggest` / `cover` 接口。

## 版本列表
见 [RELEASES.md](https://github.com/rinttt233/Boring-Word-Game/blob/main/RELEASES.md)；
完整更新历史见 [CHANGELOG.md](https://github.com/rinttt233/Boring-Word-Game/blob/main/CHANGELOG.md)。

---

## 更新报告（摘自 CHANGELOG）

"""


def make_zip(version: str, snapshot: str, out_dir: str) -> str:
    path = os.path.join(out_dir, f"Boring-Word-Game-v{version}.zip")
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        for base, dirs, files in os.walk(snapshot):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in sorted(files):
                if fn.endswith(".pyc"):
                    continue
                fp = os.path.join(base, fn)
                rel = os.path.relpath(fp, snapshot).replace("\\", "/")
                z.write(fp, f"Boring-Word-Game-v{version}/{rel}")
                n += 1
    print(f"[release] zip: {os.path.relpath(path, ROOT)}"
          f"（{n} 文件 / {os.path.getsize(path) / 1024:.1f} KB）")
    return path


def make_notes(version: str, out_dir: str) -> str:
    with io.open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## v{re.escape(version)}\b.*?(?=^## |\Z)", text,
                  re.M | re.S)
    section = m.group(0).strip() if m else f"（CHANGELOG 中没有 v{version} 章节）"
    # Release 页不需要这两行（归档/快照是本地目录）
    section = "\n".join(l for l in section.splitlines()
                        if not l.startswith(("**归档**", "**发布快照**")))
    path = os.path.join(out_dir, f"release-notes-v{version}.md")
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(HEADER.format(version=version) + section + "\n")
    print(f"[release] notes: {os.path.relpath(path, ROOT)}"
          f"（{len(section.splitlines())} 行更新报告）")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 GitHub Release 说明与快照 zip")
    ap.add_argument("--version", required=True, help="版本号，如 1.3.1")
    ap.add_argument("--snapshot", default="", help="快照目录（默认 releases/v<版本>）")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--no-zip", action="store_true", help="只生成说明")
    args = ap.parse_args()
    snapshot = args.snapshot or os.path.join(ROOT, "releases",
                                             f"v{args.version}")
    os.makedirs(args.out, exist_ok=True)
    make_notes(args.version, args.out)
    if not args.no_zip:
        if not os.path.isdir(snapshot):
            print(f"[release] 快照不存在，跳过打包: {snapshot}")
            return 1
        make_zip(args.version, snapshot, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
