"""扩展模块：占领已勘探地块（'claim' 作业）。

占领需要执行单元耗时作业；完成后 plot.state → claimed，之后可建厂/开采。
"""
from typing import Optional

from core.world import Plot


class ClaimSystem:
    def start(self, engine: object) -> None:
        self._engine = engine
        engine.bus.on("job_done", self._on_job_done)

    def claim(self, engine: object, plot_id: str) -> Optional[str]:
        plot = engine.world.get(plot_id)
        if plot is None:
            return f"地块不存在: {plot_id}"
        if plot.state == Plot.STATE_UNKNOWN:
            return "该地块尚未勘探，先 survey 勘察。"
        if plot.state in (Plot.STATE_CLAIMED, Plot.STATE_DEVELOPED):
            return "该地块已被占领。"
        unit = engine.units.assign_any("claim")
        if unit is None:
            return "没有空闲执行单元，无法占领。"
        duration = 3.0 + plot.ring * 2.0     # 越远越久
        engine.jobs.add("claim", unit.id, plot_id, duration,
                        {"plot": plot_id})
        engine.log(f"[扩展] 单元 {unit.id} 前往占领 {plot_id}，"
                   f"预计 {duration:.0f}s。")
        return None

    def _on_job_done(self, payload: dict) -> None:
        if payload.get("kind") != "claim":
            return
        pid = payload.get("target_id")
        plot = self._engine.world.get(pid)
        if plot is None:
            return
        plot.state = Plot.STATE_CLAIMED
        self._engine.log(f"[扩展] {pid} 占领完成，可建造设施。")
