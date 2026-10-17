import asyncio
import os
import uuid

from pydantic import BaseModel

from backend import narrator, steering, verify
from backend.events import bus
from backend.llm import call_model, get_totals


class PlanReply(BaseModel):
    steps: list[str]


class FindingReply(BaseModel):
    finding: str
    claims: list[str]
    sources: list[str]
    confidence: float


class Run:
    def __init__(self, task):
        self.run_id = uuid.uuid4().hex[:8]
        self.task = task
        self.steps = []         # each step: {id, text, status, changed}
        self.inbox = []         # human messages waiting for the next step
        self.focus = []         # standing instructions from change_focus
        self.findings = []      # short notes the agent sees on later steps
        self.human_notes = []   # steers and gate answers, for the final report
        self.gates = {}         # gate_id -> {"done": asyncio.Event, "answer": str}
        self.auto_answer = False
        self.step_count = 0
        self.finished = False

    def add_step(self, text, changed=False):
        self.step_count += 1
        step = {"id": f"s{self.step_count}", "text": text, "status": "waiting", "changed": changed}
        self.steps.append(step)
        return step

    def find_step(self, step_id):
        for step in self.steps:
            if step["id"] == step_id:
                return step
        return None

    def emit_plan(self):
        bus.emit(self.run_id, "plan", f"Plan has {len(self.steps)} steps", {"steps": self.steps})


runs = {}


def context_text(run):
    lines = [f"Task: {run.task}"]
    if run.focus:
        lines.append("Human instructions to follow: " + " | ".join(run.focus))
    if run.findings:
        lines.append("What you found so far:\n" + "\n".join("- " + f for f in run.findings[-5:]))
    if run.human_notes:
        lines.append("Human decisions so far:\n" + "\n".join("- " + n for n in run.human_notes[-5:]))
    return "\n\n".join(lines)


async def make_plan(run):
    messages = [
        {"role": "system", "content": "You are a research agent. Make a plan of 4 to 6 short research steps for the task. Each step is one short sentence."},
        {"role": "user", "content": f"Task: {run.task}"},
    ]
    reply = await call_model(run.run_id, "agent", os.getenv("AGENT_MODEL", ""), messages, schema=PlanReply, max_tokens=800)
    steps = reply.steps[:6] if reply and reply.steps else ["Research the task", "Summarize what you found"]
    for text in steps:
        run.add_step(text)
    run.emit_plan()


async def wait_for_gate(run, reason, question, options, extra=None):
    gate_id = f"g{len(run.gates) + 1}"
    run.gates[gate_id] = {"done": asyncio.Event(), "answer": None}
    detail = {"gate_id": gate_id, "reason": reason, "question": question, "options": options}
    detail.update(extra or {})
    bus.emit(run.run_id, "gate", question, detail)

    if run.auto_answer:
        await asyncio.sleep(5)  # so a replay shows the gate for a moment
        answer_gate(run, gate_id, options[0])
    await run.gates[gate_id]["done"].wait()
    return run.gates[gate_id]["answer"]


def answer_gate(run, gate_id, answer):
    gate = run.gates.get(gate_id)
    if gate is None or gate["done"].is_set():
        return False
    gate["answer"] = answer
    gate["done"].set()
    bus.emit(run.run_id, "gate_answered", f"Human chose: {answer}", {"gate_id": gate_id, "answer": answer})
    return True


async def do_step(run, step):
    step["status"] = "running"
    bus.emit(run.run_id, "step_started", step["text"], {"step_id": step["id"]})
    run.emit_plan()

    messages = [
        {"role": "system", "content": (
            "You are a careful research agent. Do one research step and report what you found. "
            "Give a short finding (2 to 4 sentences), up to 3 key factual claims written as standalone sentences "
            "someone could fact-check, the source URLs you used, and your confidence from 0 to 1. "
            "Each claim must state one specific figure or fact, the way it is commonly quoted, not a range or a hedge."
        )},
        {"role": "user", "content": context_text(run) + f"\n\nCurrent step: {step['text']}"},
    ]
    reply = await call_model(run.run_id, "agent", os.getenv("AGENT_MODEL", ""), messages,
                             schema=FindingReply, web_search=os.getenv("WEB_SEARCH", "1") == "1", max_tokens=2000)
    if reply is None:
        step["status"] = "done"
        run.emit_plan()
        return

    claims = [c for c in reply.claims if c.strip()][:3]
    confidence = max(0.0, min(1.0, reply.confidence))
    bus.emit(run.run_id, "finding", reply.finding, {
        "step_id": step["id"], "text": reply.finding, "claims": claims,
        "sources": reply.sources[:5], "confidence": confidence,
    })
    run.findings.append(f"{step['text']}: {reply.finding[:300]}")

    # The narrator runs in the background so it never slows the agent down.
    asyncio.create_task(narrator.narrate(run.run_id, step, reply.finding))

    checks = await verify.check_claims(run.run_id, claims)
    split_checks = [c for c in checks if c["split"]]
    threshold = float(os.getenv("GATE_CONFIDENCE", "0.6"))

    if split_checks:
        check = split_checks[0]
        answer = await wait_for_gate(
            run, "split",
            f"The checker models disagree on this claim: \"{check['claim']}\" What should the agent do?",
            ["Keep it, but mark it as disputed", "Drop this claim", "Dig deeper on this claim"],
            {"claim": check["claim"], "verdicts": check["verdicts"], "step_id": step["id"]},
        )
        run.human_notes.append(f"Gate on \"{check['claim']}\": human chose \"{answer}\"")
        if answer == "Dig deeper on this claim":
            run.add_step(f"Dig deeper on whether this is true: {check['claim']}", changed=True)
    elif confidence < threshold:
        answer = await wait_for_gate(
            run, "low_confidence",
            f"The agent is only {int(confidence * 100)}% sure about \"{step['text']}\". What next?",
            ["Keep going", "Dig deeper on this step"],
            {"confidence": confidence, "step_id": step["id"]},
        )
        run.human_notes.append(f"Low confidence on \"{step['text']}\": human chose \"{answer}\"")
        if answer == "Dig deeper on this step":
            run.add_step(f"Find stronger sources for: {step['text']}", changed=True)

    step["status"] = "done"
    run.emit_plan()


async def finish(run):
    humans = "\n".join("- " + n for n in run.human_notes) or "- No human input this run."
    messages = [
        {"role": "system", "content": (
            "Write a final research report in markdown with 6 to 8 short sections. Put a line with only --- "
            "between sections. Mark any claim the human flagged as disputed. The last section must be titled "
            "'How humans shaped this run' and use the human decisions given."
        )},
        {"role": "user", "content": context_text(run) + "\n\nHuman decisions:\n" + humans},
    ]
    try:
        report = await call_model(run.run_id, "agent", os.getenv("AGENT_MODEL", ""), messages, max_tokens=1500)
    except Exception as error:
        # A failed report shouldn't hide the run totals.
        bus.emit(run.run_id, "error", "Final report failed", {"where": "report", "message": str(error)[:200]})
        report = ""
    run_totals = get_totals(run.run_id)
    bus.emit(run.run_id, "run_finished",
             f"Done: {run_totals['calls']} model calls, {len(run_totals['models'])} models, ${run_totals['cost']:.4f}",
             {"total_cost": run_totals["cost"], "total_calls": run_totals["calls"],
              "models": run_totals["models"], "fallbacks": run_totals["fallbacks"], "report": report})


async def run_agent(run):
    bus.emit(run.run_id, "run_started", run.task, {"task": run.task})
    try:
        await make_plan(run)
        while True:
            while run.inbox:
                message = run.inbox.pop(0)
                await steering.apply(run, message)
            next_steps = [s for s in run.steps if s["status"] == "waiting"]
            if not next_steps:
                break
            await do_step(run, next_steps[0])
        await finish(run)
    except Exception as error:
        bus.emit(run.run_id, "error", f"Run stopped: {str(error)[:120]}", {"where": "agent", "message": str(error)[:300]})
    run.finished = True


def start_run(task):
    run = Run(task)
    runs[run.run_id] = run
    asyncio.create_task(run_agent(run))
    return run
