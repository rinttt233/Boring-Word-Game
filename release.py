#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发版助手：归档备份 + 写 VERSION + CHANGELOG 条目骨架 + 工作区发布快照。

用法:
    python -X utf8 release.py 1.3 "阶段导读与 FAQ 补充"
    python -X utf8 release.py 1.2.1 "修复标签换行后选中丢失" --patch
    python -X utf8 release.py 1.3 "标题" --check      # 先跑单测作门禁

发版动作（规则第 10 条）:
    1) 归档全量源码 → _refactor_backup/v<版本>_<时间戳>/
    2) 写 VERSION
    3) 在 CHANGELOG.md 插入版本条目骨架（已有则跳过）
    4) 复制整个工作区 → releases/v<版本>/（**排除 releases 自身**，防递归）

约定（与 CHANGELOG.md 顶部规则一致）：
    X.Y   大修改批次（新系统/新玩法/重要架构）
    X.Y.Z 小修补（bug 修复/文案/平衡微调）
只在收到明确指令时执行；本脚本不做任何隐式版本决策。
"""
import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_ROOT = os.path.join(ROOT, "_refactor_backup")
RELEASE_ROOT = os.path.join(ROOT, "releases")      # 工作区内发布快照
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
VERSION_FILE = os.path.join(ROOT, "VERSION")

ARCHIVE_FILES = ["main.py", "VERSION", "CHANGELOG.md", "TODO.md", "README.md",
                 ".gitignore", "启动游戏_GUI.bat", "release.py"]
ARCHIVE_DIRS = ["content", "core", "systems", "ui", "docs", "tests"]
# 复制到发布快照时必须排除的目录（含 releases 自身，防止无限递归嵌套）
SKIP_DIRS = {"__pycache__", "saves", "_refactor_backup", "releases", ".git"}


def valid_version(v: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+(\.\d+)?", v))


def archive(version: str) -> str:
    """整包归档到 _refactor_backup/v<ver>_<时间戳>/。"""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_ROOT, f"v{version}_{ts}")
    os.makedirs(dst, exist_ok=True)
    for name in ARCHIVE_FILES:
        src = os.path.join(ROOT, name)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
    # 根目录下其它 .py（各端到端测试脚本）
    for name in sorted(os.listdir(ROOT)):
        if name.endswith(".py") and os.path.isfile(os.path.join(ROOT, name)) \
                and name not in ARCHIVE_FILES:
            shutil.copy2(os.path.join(ROOT, name), dst)
    for d in ARCHIVE_DIRS:
        src = os.path.join(ROOT, d)
        if not os.path.isdir(src):
            continue
        target = os.path.join(dst, d)
        shutil.copytree(src, target,
                        ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))
    return dst


def write_version(version: str) -> None:
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(version + "\n")


def copy_to_release_dir(version: str) -> str:
    """把整个工作区复制到 releases/v<版本>/（规则第 10 条）。

    安全要点：**显式排除 releases 自身**（以及存档/备份/缓存），
    避免出现 releases/v1.3/releases/v1.3/... 的递归嵌套。
    同一版本重复发版时先清空该目录，保证快照与当前代码一致。
    """
    dst = os.path.join(RELEASE_ROOT, f"v{version}")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    for name in sorted(os.listdir(ROOT)):
        src = os.path.join(ROOT, name)
        if os.path.isfile(src):
            if name.endswith(".pyc"):
                continue
            shutil.copy2(src, os.path.join(dst, name))
            continue
        if not os.path.isdir(src):
            continue
        if name in SKIP_DIRS:
            continue                     # 排除 releases 自身与存档/备份/缓存
        shutil.copytree(src, os.path.join(dst, name),
                        ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))
    return dst


def count_files(path: str):
    n = 0
    size = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            n += 1
            try:
                size += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return n, size


def update_changelog(version: str, title: str, archive_path: str) -> bool:
    """在 CHANGELOG 里插入版本条目骨架；已存在则跳过（幂等）。"""
    with open(CHANGELOG, "r", encoding="utf-8") as f:
        text = f.read()
    if re.search(rf"^## v{re.escape(version)}\b", text, re.M):
        return False
    date = datetime.datetime.now().strftime("%Y-%m-%d")
    rel = os.path.relpath(archive_path, ROOT).replace("\\", "/")
    entry = (
        f"## v{version} — {title}\n\n"
        f"**日期**：{date}\n"
        f"**归档**：`{rel}/`\n"
        f"**主题**：{title}\n\n"
        "### 一、主要改动\n"
        "- （发版时补全）\n\n"
        "### 二、影响范围\n"
        "- （玩法/内容/界面/存档兼容性）\n\n"
        "### 三、验证\n"
        "- 单测 / GUI 自检 / 端到端脚本结果\n\n"
        "---\n\n"
    )
    marker = "## 未归档历史"
    if marker in text:
        text = text.replace(marker, entry + marker, 1)
    else:
        text = text.rstrip() + "\n\n" + entry
    with open(CHANGELOG, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def run_checks() -> bool:
    print("[发版] 运行单元测试门禁 …")
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest",
                        "discover", "-s", "tests"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8")
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        print("   ", line)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="发版助手（归档+版本+更新报告骨架）")
    ap.add_argument("version", help="版本号，如 1.3 或 1.2.1")
    ap.add_argument("title", help="本次版本主题（一句话）")
    ap.add_argument("--patch", action="store_true",
                    help="标记为小修补（X.Y.Z）")
    ap.add_argument("--check", action="store_true",
                    help="先运行单元测试作为门禁")
    ap.add_argument("--no-archive", action="store_true",
                    help="只写版本与报告，不归档到 _refactor_backup（不推荐）")
    ap.add_argument("--no-copy", action="store_true",
                    help="跳过复制到工作区 releases/v<版本>/ 目录")
    args = ap.parse_args()

    if not valid_version(args.version):
        print(f"[发版] 版本号格式不正确: {args.version}")
        return 2
    if args.patch and args.version.count(".") != 2:
        print("[发版] --patch 需要 X.Y.Z 形式")
        return 2
    if args.check and not run_checks():
        print("[发版] 门禁未通过：单测失败，未发版。")
        return 1

    archive_path = ""
    if not args.no_archive:
        archive_path = archive(args.version)
    write_version(args.version)
    wrote = update_changelog(args.version, args.title, archive_path or ROOT)

    release_path = ""
    if not args.no_copy:
        release_path = copy_to_release_dir(args.version)

    print(f"[发版] 版本号: {args.version}")
    if archive_path:
        print(f"[发版] 归档: {os.path.relpath(archive_path, ROOT)}")
    if release_path:
        n, size = count_files(release_path)
        print(f"[发版] 发布快照: {os.path.relpath(release_path, ROOT)}"
              f"（{n} 文件 / {size / 1024:.1f}KB，已排除 releases 自身）")
    print(f"[发版] CHANGELOG: "
          + ("已插入条目骨架（请补全报告）" if wrote else "已存在该版本条目，跳过"))
    print("[发版] 提醒：请补全 CHANGELOG 的三节内容后再对用户汇报。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
