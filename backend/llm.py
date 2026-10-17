import asyncio
import json
import os
import random
import time
import zlib

from dotenv import load_dotenv
from openai import AsyncOpenAI

from backend.events import ROOT_FOLDER, bus

load_dotenv(os.path.join(ROOT_FOLDER, ".env"))


def env_list(name):
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
# No key means mock mode, so the UI can still be built and shown.
MOCK = os.getenv("MOCK_MODE", "0") == "1" or not API_KEY
FALLBACK_MODELS = env_list("FALLBACK_MODELS")

client = None
if not MOCK:
    client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=API_KEY)

# run_id -> running totals for the models panel and /summary
totals = {}


def get_totals(run_id):
    if run_id not in totals:
        totals[run_id] = {"calls": 0, "cost": 0.0, "models": [], "fallbacks": 0}
    return totals[run_id]


def clean_json_text(text):
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return text[start:end + 1]
    return text


def schema_hint(schema):
    return (
        "Reply with only a JSON object, no other text, matching this JSON schema:\n"
        + json.dumps(schema.model_json_schema())
    )


async def send(model, messages, schema, web_search, max_tokens):
    extra = {"usage": {"include": True}}
    backups = [m for m in FALLBACK_MODELS if m != model][:2]
    if backups:
        extra["models"] = [model] + backups
    if web_search:
        extra["plugins"] = [{"id": "web", "max_results": 3}]

    kwargs = {"model": model, "messages": messages, "max_tokens": max_tokens, "extra_body": extra}
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": False, "schema": schema.model_json_schema()},
        }

    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception:
        # Some providers reject the web plugin or response_format. Try once without them.
        kwargs.pop("response_format", None)
        extra.pop("plugins", None)
        response = await client.chat.completions.create(**kwargs)

    text = response.choices[0].message.content or ""
    usage = response.usage.model_dump() if response.usage else {}
    return text, response.model or model, usage


async def call_model(run_id, role, model, messages, schema=None, web_search=False, max_tokens=2000):
    if schema is not None:
        messages = [{"role": "system", "content": schema_hint(schema)}] + messages

    attempts = 0
    while True:
        attempts += 1
        start = time.time()
        if MOCK:
            await asyncio.sleep(random.uniform(0.4, 1.2))
            text = mock_reply(role, model, schema, messages)
            model_used = model
            usage = {"prompt_tokens": 400, "completion_tokens": 120, "cost": 0.0}
        else:
            text, model_used, usage = await send(model, messages, schema, web_search, max_tokens)
        seconds = round(time.time() - start, 2)
        log_call(run_id, role, model, model_used, seconds, usage)

        if schema is None:
            return text
        try:
            return schema.model_validate(json.loads(clean_json_text(text)))
        except Exception as error:
            if attempts >= 2:
                bus.emit(run_id, "error", f"{role} reply was not valid JSON", {"where": role, "message": str(error)[:200]})
                return None
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not valid JSON. Reply with valid JSON only."},
            ]


def log_call(run_id, role, model_requested, model_used, seconds, usage):
    cost = usage.get("cost") or 0.0
    fallback_used = model_used != model_requested
    run_totals = get_totals(run_id)
    run_totals["calls"] += 1
    run_totals["cost"] += cost
    if model_used not in run_totals["models"]:
        run_totals["models"].append(model_used)
    if fallback_used:
        run_totals["fallbacks"] += 1

    if fallback_used:
        summary = f"{role}: {model_requested} failed, served by {model_used}"
    else:
        summary = f"{role}: {model_used} in {seconds}s"
    bus.emit(run_id, "model_call", summary, {
        "role": role,
        "model_requested": model_requested,
        "model_used": model_used,
        "fallback_used": fallback_used,
        "seconds": seconds,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cost": cost,
    })


# ---------- mock mode, only used when there is no API key ----------

def load_seed_claims(messages):
    all_text = " ".join(str(m.get("content", "")) for m in messages)
    try:
        with open(os.path.join(ROOT_FOLDER, "demo_tasks.json"), encoding="utf-8") as file:
            tasks = json.load(file)["tasks"]
    except Exception:
        tasks = []
    for task in tasks:
        if task["prompt"][:60] in all_text:
            return task["seed_claims"]
    return ["The main source on this topic is widely cited.", "Recent estimates are lower than older ones."]


def mock_reply(role, model, schema, messages):
    name = schema.__name__ if schema else ""
    last = str(messages[-1].get("content", ""))
    if name == "PlanReply":
        return json.dumps({"steps": [
            "Find the most cited numbers on the topic",
            "Check where those numbers came from",
            "Look for newer estimates that revise them",
            "Work out what it means for one person",
        ]})
    if name == "FindingReply":
        claims = load_seed_claims(messages)
        picked = random.sample(claims, min(2, len(claims)))
        return json.dumps({
            "finding": "Mock finding: sources give different figures here, and newer estimates tend to be lower.",
            "claims": picked,
            "sources": ["https://example.org/mock-source"],
            "confidence": round(random.uniform(0.45, 0.9), 2),
        })
    if name == "Verdict":
        pick = zlib.crc32((model + last).encode()) % 3
        verdict = ["agree", "disagree", "unsure"][pick]
        return json.dumps({"verdict": verdict, "reason": f"Mock {verdict} from {model}."})
    if name == "SteeringResult":
        return json.dumps({
            "edits": [{"action": "change_focus", "step_id": None, "text": last[-200:]}],
            "reply": "Got it, I will keep that focus for every remaining step.",
        })
    if role == "narrator":
        return "Mock narration: the agent compared a few sources and found they don't fully agree."
    if role == "agent":
        return "# Mock report\n\nThis is a mock report.\n---\n## How humans shaped this run\n\nSee the timeline."
    return "Mock reply."
