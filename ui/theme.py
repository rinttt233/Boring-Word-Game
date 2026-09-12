"""黑白灰极简主题常量。

单色设计：层次靠灰度/留白/粗细，警示用反白(黑底白字)。
背景为白色（用户定案）。
"""

# ---- 灰度阶（白 → 黑）----
BG = "#FFFFFF"          # 主背景（白）
PANEL = "#F4F4F4"       # 次级面板（浅灰）
PANEL_ALT = "#ECECEC"   # 面板内交替行
BORDER = "#CCCCCC"      # 1px 分隔线
BORDER_DARK = "#999999" # 输入框/焦点边框
TEXT = "#111111"        # 主文字（近黑）
TEXT_SUB = "#666666"    # 次级文字（中灰）
TEXT_DIM = "#999999"    # 弱文字（浅灰）
ACCENT = "#111111"      # 强调（黑：选中/主按钮）

# ---- 警示（反白：黑底白字）----
WARN_BG = "#111111"
WARN_FG = "#FFFFFF"

# ---- 字体 ----
FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)
FONT_MONO_BOLD = ("Consolas", 10, "bold")
FONT_MONO_SMALL = ("Consolas", 9)
FONT_TITLE = ("Segoe UI", 11, "bold")

# ---- 符号统一（替代易渲染不稳/彩色的 emoji，保证黑白灰一致）----
# 彩色 emoji（⚠🔒⚡✅⏳）在黑白灰主题下会显示成彩色或豆腐块，改为稳定字符。
S_WARN = "!"          # 替代 ⚠ 警告
S_LOCK = "*"          # 替代 🔒 锁定
S_OK = "√"            # 替代 ✔/✅ 完成
S_BAD = "X"           # 替代 ✘ 失败/不足
S_POWER = "~"         # 替代 ⚡ 电力
S_TODO = "·"          # 替代 ⏳/▢ 未完成
S_BUILD = ">"         # 建造中（进行中的作业）
# 简形箭头（▸▾▶⏸←→）在等宽字体下基本稳定，保留原符号保证中文界面可读性。

# ---- 字体回退（部分系统缺 Segoe UI/Consolas，避免豆腐块/方块字）----
_UI_STACK = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
             "SimHei", "Arial")
_MONO_STACK = ("Consolas", "Cascadia Mono", "Courier New")


def apply_font_fallback(root) -> None:
    """检测首选字体是否可用；缺失则回退到系统可用的中文字体族。

    就地重绑模块级字体常量（各面板通过 T.FONT_* 取用，故即时生效）。
    在创建任何控件之前调用。
    """
    global FONT_UI, FONT_UI_BOLD, FONT_SMALL, FONT_TITLE
    global FONT_MONO, FONT_MONO_BOLD, FONT_MONO_SMALL
    try:
        import tkinter.font as tkfont
        families = set(tkfont.families(root))
    except Exception:
        return

    def pick(stack, size, weight=None):
        for name in stack:
            if name in families:
                base = (name, size)
                return base + ((weight,) if weight else ())
        base = (tkfont.nametofont("TkDefaultFont").actual("family"), size)
        return base + ((weight,) if weight else ())

    ui = pick(_UI_STACK, 10)
    FONT_UI = ui
    FONT_UI_BOLD = pick(_UI_STACK, 10, "bold")
    FONT_SMALL = pick(_UI_STACK, 9)
    FONT_TITLE = pick(_UI_STACK, 11, "bold")
    FONT_MONO = pick(_MONO_STACK, 10)
    FONT_MONO_BOLD = pick(_MONO_STACK, 10, "bold")
    FONT_MONO_SMALL = pick(_MONO_STACK, 9)
