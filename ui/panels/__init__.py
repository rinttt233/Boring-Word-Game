"""ui.panels 包：面板系统（P1 重构由单文件拆分为子包）。

对外保持与旧 ui/panels.py 完全一致的导出，外部 `from ui.panels import X`
无需改动。
"""
from ui.panels.base import Panel, PanelRegistry, style_tree  # noqa: F401
from ui.panels.buildings import BuildingsPanel            # noqa: F401
from ui.panels.plots import PlotsPanel                     # noqa: F401
from ui.panels.right import (                              # noqa: F401
    ResourcesPanel, UnitsPanel, RecoveryPanel,
    ProcessesPanel, ChainPanel, PowerPanel, LayersPanel,
    WikiPanel, DatabasePanel, EnvironmentPanel,
)
