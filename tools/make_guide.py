#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_guide.py —— 把 content/guide.json 渲染成 AGENTS.md 与游戏内文本。

单一来源：向导内容只写在 content/guide.json；本脚本生成根目录 `AGENTS.md`
（新会话的 AI 第一份要读的文件），游戏内 `guide` 命令用同一份数据，
所以文档与游戏内自述不会漂移。

用法:
    python -X utf8 tools/make_guide.py            # 生成 AGENTS.md
    python -X utf8 tools/make_guide.py --stdout   # 只打印（不写文件）
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_guide():
    with io.open(os.path.join(ROOT, "content", "guide.json"),
                 encoding="utf-8") as f:
        return json.load(f)


def render_section(sec: dict, md: bool = True) -> str:
    """渲染单节；md=True 输出 Markdown，否则输出纯文本（游戏内用）。"""
    out = []
    out.append(f"## {sec['title']}" if md else f"【{sec['title']}】")
    out.append("")
    body = sec.get("body")
    if body:
        out.append(body)
        out.append("")
    for b in sec.get("bullets") or []:
        out.append(f"- {b}" if md else f"  · {b}")
    if sec.get("bullets"):
        out.append("")
    for i, s in enumerate(sec.get("steps") or [], 1):
        out.append(f"{i}. {s}" if md else f"  {i}) {s}")
    if sec.get("steps"):
        out.append("")
    for it in sec.get("items") or []:
        if md:
            out.append(f"- **{it['symptom']}** → {it['fix']}")
        else:
            out.append(f"  · {it['symptom']}")
            out.append(f"      → {it['fix']}")
    if sec.get("items"):
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_markdown(g: dict) -> str:
    out = []
    out.append(f"# {g['title']} —— {g['subtitle']}")
    out.append("")
    out.append("> **本文件由 `tools/make_guide.py` 从 `content/guide.json` 生成，"
               "请勿手改；改内容请改 `content/guide.json` 后重新生成。**")
    out.append("")
    out.append("## 硬约束（先读这段）")
    out.append("")
    for c in g.get("constraints", []):
        out.append(f"- {c}")
    out.append("")
    out.append("## 三步开始")
    out.append("")
    for line in g.get("start", []):
        out.append(line if line.startswith("```") or line.startswith("python")
                   else f"- {line}" if not line.startswith("```") else line)
    out.append("")
    for sec in g.get("sections", []):
        out.append(render_section(sec, md=True))
        out.append("")
    out.append("---")
    out.append("")
    out.append("## 文件导航（除了本文件，你只需要这些）")
    out.append("")
    out.append("| 路径 | 用途 |")
    out.append("|---|---|")
    out.append("| `main.py` | 启动游戏（`--gui` / `--demo N` / `--seed N`） |")
    out.append("| `tools/agent_play.py` | 试玩回路（文件协议，AI 推荐入口） |")
    out.append("| `tools/play_report.py` | 把 `saves/ai_samples.csv` 出成遥测报告 |")
    out.append("| `tools/blind_test.py` | 按策略自动盲玩并汇总（覆盖度/卡点） |")
    out.append("| `tools/screenshot.ps1` | 截取游戏窗口为 PNG（需看图能力） |")
    out.append("| `content/wiki/*.json` | 游戏内百科原文（`wiki` 命令可查） |")
    out.append("| `content/guide.json` | 本文件的内容源 |")
    out.append("")
    out.append("游戏内随时可用：`help` / `guide` / `wiki <关键词>` / `report` / "
               "`suggest` / `cover` / `status`。")
    out.append("")
    return "\n".join(out)


def render_console(g: dict, section_id: str = "") -> str:
    out = []
    if not section_id:
        out.append(f"=== {g['title']} · 试玩向导 ===")
        for c in g.get("constraints", []):
            out.append("  ! " + c.replace("**", ""))
        out.append("")
        for line in g.get("start", []):
            if line.startswith("```") or line.startswith("python"):
                out.append("  " + line)
        out.append("")
        out.append("  章节: " + "、".join(s["id"] for s in g.get("sections", []))
                   + "（用 guide <章节id> 单看）")
        out.append("")
    for sec in g.get("sections", []):
        if section_id and sec["id"] != section_id:
            continue
        out.append(render_section(sec, md=False))
    return "\n".join(out).rstrip()


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染向导（AGENTS.md / 控制台文本）")
    ap.add_argument("--stdout", action="store_true", help="只打印不写文件")
    args = ap.parse_args()
    g = load_guide()
    md = render_markdown(g)
    if args.stdout:
        sys.stdout.write(md)
        return 0
    path = os.path.join(ROOT, "AGENTS.md")
    with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(md)
    print(f"[guide] 已生成 {os.path.relpath(path, ROOT)}"
          f"（{len(md.splitlines())} 行，源自 content/guide.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
