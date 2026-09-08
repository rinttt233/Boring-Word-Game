"""作业队列：带持续时间的任务（勘探、占领、建造…）。

一个作业占用一个执行单元，运行 duration 游戏秒后完成并发 'job_done' 事件，
由各模块订阅做领域处理。这是"执行单元调度"的时间维度：
有限单元 × 有限时间 = 玩家永远在做"把执行器拨给谁"的决策。
"""
from typing import Dict, List, Optional


class Job:
    def __init__(self, job_id: str, kind: str, unit_id: str, target_id: str,
                 duration: float, payload: Optional[dict] = None) -> None:
        self.id = job_id
        self.kind = kind                  # 'survey' / 'claim' / 'build' / ...
        self.unit_id = unit_id            # 占用哪个执行单元
        self.target_id = target_id        # 作用目标（地块/设施 id）
        self.remaining = float(duration)
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "unit_id": self.unit_id,
                "target_id": self.target_id, "remaining": self.remaining,
                "payload": self.payload}

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(data["id"], data["kind"], data["unit_id"],
                   data["target_id"], float(data["remaining"]),
                   dict(data.get("payload", {})))


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._seq = 0

    # ---- 操作 ------------------------------------------------------
    def add(self, kind: str, unit_id: str, target_id: str,
            duration: float, payload: Optional[dict] = None) -> Job:
        self._seq += 1
        j = Job(f"J{self._seq}", kind, unit_id, target_id, duration, payload)
        self._jobs[j.id] = j
        return j

    def cancel(self, job_id: str) -> Optional[Job]:
        return self._jobs.pop(job_id, None)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[Job]:
        return list(self._jobs.values())

    def count(self) -> int:
        return len(self._jobs)

    # ---- 推进 ------------------------------------------------------
    def tick(self, engine: object, dt: float) -> None:
        done = []
        for j in list(self._jobs.values()):
            j.remaining -= dt
            if j.remaining <= 0.0:
                done.append(j)
        for j in done:
            self._jobs.pop(j.id, None)
            # 归还执行单元
            u = engine.units.get(j.unit_id)
            if u is not None:
                u.release()
            engine.bus.emit("job_done", {
                "id": j.id, "kind": j.kind, "unit_id": j.unit_id,
                "target_id": j.target_id, "payload": dict(j.payload),
            })

    # ---- 存档 ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"seq": self._seq,
                "jobs": [j.to_dict() for j in self._jobs.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "JobRegistry":
        r = cls()
        r._seq = int(data.get("seq", 0))
        for jd in data.get("jobs", []):
            j = Job.from_dict(jd)
            r._jobs[j.id] = j
        return r
