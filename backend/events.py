import asyncio
import json
import os
import time

from pydantic import BaseModel

ROOT_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_FOLDER = os.path.join(ROOT_FOLDER, "runs")


class Event(BaseModel):
    run_id: str
    seq: int
    type: str
    summary: str
    detail: dict = {}
    timestamp: float


class EventBus:
    def __init__(self):
        self.history = {}    # run_id -> list of every event so far
        self.listeners = {}  # run_id -> list of queues, one per open browser tab
        self.printing = False

    def emit(self, run_id, type, summary, detail=None):
        events = self.history.setdefault(run_id, [])
        event = Event(
            run_id=run_id,
            seq=len(events) + 1,
            type=type,
            summary=summary,
            detail=detail or {},
            timestamp=time.time(),
        )
        events.append(event)
        for queue in self.listeners.get(run_id, []):
            queue.put_nowait(event)
        self.save(run_id)
        if self.printing:
            print(f"[{event.seq:>3}] {type:<15} {summary}")
        return event

    def save(self, run_id):
        os.makedirs(RUNS_FOLDER, exist_ok=True)
        path = os.path.join(RUNS_FOLDER, run_id + ".json")
        data = [event.model_dump() for event in self.history[run_id]]
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=1)

    def subscribe(self, run_id):
        queue = asyncio.Queue()
        self.listeners.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id, queue):
        queues = self.listeners.get(run_id, [])
        if queue in queues:
            queues.remove(queue)


bus = EventBus()
