"""GUI 偏好持久化（黑白灰 GUI 的布局/选择记忆）。

- 保存到 saves/gui_prefs.json（独立于游戏存档，重启 GUI 恢复布局）。
- 结构为任意 JSON dict；提供 get/set 便捷接口。
- 面板/窗格可注册"读钩子"（build 后调用）与"写钩子"（退出时收集状态）。
"""
import json
import os
from typing import Any, Dict, List, Optional


class GUIPrefs:
    def __init__(self, path: str = "saves/gui_prefs.json") -> None:
        self.path = path
        self.data: Dict[str, Any] = {}
        self._read_hooks: List[callable] = []   # (fn) 读取时调用
        self._write_hooks: List[callable] = []  # (fn) 写回时调用
        self._load()

    # ---- 加载/保存 -----------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        # 先跑写钩子收集最新状态
        for fn in self._write_hooks:
            try:
                fn(self)
            except Exception:
                pass
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ---- 便捷访问 ------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    # ---- 钩子注册 ------------------------------------------------
    def add_hooks(self, read_fn: Optional[callable] = None,
                  write_fn: Optional[callable] = None) -> None:
        """注册读钩子（加载后调用，用于恢复界面状态）与写钩子（保存时调用）。"""
        if read_fn is not None:
            self._read_hooks.append(read_fn)
        if write_fn is not None:
            self._write_hooks.append(write_fn)

    def run_read_hooks(self) -> None:
        for fn in self._read_hooks:
            try:
                fn(self)
            except Exception:
                pass
