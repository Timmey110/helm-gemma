import asyncio
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend import agent, llm
from backend.events import ROOT_FOLDER, RUNS_FOLDER, bus

app = FastAPI(title="Helm")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TaskBody(BaseModel):
    task: str


class SteerBody(BaseModel):
    text: str


class GateBody(BaseModel):
    gate_id: str
    answer: str


def get_run(run_id):
    run = agent.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No run with that id")
    return run


@app.get("/")
def home():
    return FileResponse(os.path.join(ROOT_FOLDER, "frontend", "index.html"))


@app.get("/config")
def config():
    # Only model names and the mode, never keys.
    return {
        "mock": llm.MOCK,
        "agent_model": os.getenv("AGENT_MODEL", ""),
        "narrator_model": os.getenv("NARRATOR_MODEL", ""),
        "steer_model": os.getenv("STEER_MODEL", ""),
        "checker_models": llm.env_list("CHECKER_MODELS"),
    }


@app.post("/runs")
async def create_run(body: TaskBody):
    run = agent.start_run(body.task.strip())
    return {"run_id": run.run_id}


async def stream_events(run_id):
    queue = bus.subscribe(run_id)
    try:
        for event in list(bus.history.get(run_id, [])):
            yield f"data: {event.model_dump_json()}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield f"data: {event.model_dump_json()}\n\n"
    finally:
        bus.unsubscribe(run_id, queue)


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    get_run(run_id)
    return StreamingResponse(stream_events(run_id), media_type="text/event-stream")


@app.post("/runs/{run_id}/steer")
async def steer(run_id: str, body: SteerBody):
    run = get_run(run_id)
    message = {"text": body.text.strip(), "source": "ui"}
    run.inbox.append(message)
    bus.emit(run_id, "steer_received", message["text"], message)
    return {"ok": True}


@app.post("/runs/{run_id}/gate")
async def gate(run_id: str, body: GateBody):
    run = get_run(run_id)
    if not agent.answer_gate(run, body.gate_id, body.answer):
        raise HTTPException(status_code=409, detail="Gate already answered or unknown")
    return {"ok": True}


@app.get("/runs/{run_id}/summary")
def summary(run_id: str):
    get_run(run_id)
    return llm.get_totals(run_id)


@app.get("/demo-tasks")
def demo_tasks():
    with open(os.path.join(ROOT_FOLDER, "demo_tasks.json"), encoding="utf-8") as file:
        return json.load(file)


@app.get("/saved-runs")
def saved_runs():
    if not os.path.isdir(RUNS_FOLDER):
        return []
    names = []
    for file_name in sorted(os.listdir(RUNS_FOLDER), reverse=True):
        if file_name.endswith(".json"):
            names.append(file_name[:-5])
    return names


async def replay_events(events, speed):
    previous = None
    for event in events:
        if previous is not None:
            gap = (event["timestamp"] - previous) / speed
            await asyncio.sleep(max(0.0, min(gap, 3.0)))
        previous = event["timestamp"]
        yield f"data: {json.dumps(event)}\n\n"
    yield "event: replay_done\ndata: {}\n\n"


@app.get("/replay/{name}")
async def replay(name: str, speed: float = 1.0):
    safe_name = os.path.basename(name)
    path = os.path.join(RUNS_FOLDER, safe_name + ".json")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No saved run with that name")
    with open(path, encoding="utf-8") as file:
        events = json.load(file)
    return StreamingResponse(replay_events(events, max(speed, 0.1)), media_type="text/event-stream")
