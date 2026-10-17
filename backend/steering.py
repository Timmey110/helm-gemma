import os

from pydantic import BaseModel

from backend.events import bus
from backend.llm import call_model


class PlanEdit(BaseModel):
    action: str        # "add_step" | "remove_step" | "change_step" | "change_focus"
    step_id: str | None = None
    text: str | None = None


class SteeringResult(BaseModel):
    edits: list[PlanEdit]
    reply: str         # one sentence telling the human what changed


async def apply(run, message):
    open_steps = [s for s in run.steps if s["status"] == "waiting"]
    plan_text = "\n".join(f"{s['id']}: {s['text']}" for s in open_steps) or "(no steps left)"
    messages = [
        {"role": "system", "content": (
            "You turn a human's message into edits to an AI research agent's plan. "
            "You may only edit the steps listed (they have not started yet). "
            "Actions: add_step (text = new step), remove_step (step_id), "
            "change_step (step_id and new text), change_focus (text = a standing instruction for all later steps). "
            "Use as few edits as needed. reply is one sentence telling the human what you changed."
        )},
        {"role": "user", "content": f"Task: {run.task}\n\nSteps not started yet:\n{plan_text}\n\nHuman message: {message['text']}"},
    ]
    result = await call_model(run.run_id, "steer", os.getenv("STEER_MODEL", ""), messages, schema=SteeringResult, max_tokens=800)
    if result is None:
        return

    applied = []
    open_ids = [s["id"] for s in open_steps]
    for edit in result.edits:
        if edit.action == "add_step" and edit.text:
            run.add_step(edit.text, changed=True)
            applied.append({"action": "add_step", "text": edit.text})
        elif edit.action == "remove_step" and edit.step_id in open_ids:
            step = run.find_step(edit.step_id)
            step["status"] = "removed"
            step["changed"] = True
            applied.append({"action": "remove_step", "step_id": edit.step_id, "text": step["text"]})
        elif edit.action == "change_step" and edit.step_id in open_ids and edit.text:
            step = run.find_step(edit.step_id)
            step["text"] = edit.text
            step["changed"] = True
            applied.append({"action": "change_step", "step_id": edit.step_id, "text": edit.text})
        elif edit.action == "change_focus" and edit.text:
            run.focus.append(edit.text)
            applied.append({"action": "change_focus", "text": edit.text})

    run.human_notes.append(f"Steer ({message['source']}): \"{message['text']}\" led to: {result.reply}")
    bus.emit(run.run_id, "steer_applied", result.reply, {"edits": applied, "message": message["text"]})
    run.emit_plan()
