import os

from backend.events import bus
from backend.llm import call_model


async def narrate(run_id, step, finding_text):
    model = os.getenv("NARRATOR_MODEL", "")
    messages = [
        {"role": "system", "content": (
            "You narrate an AI research agent's work for a non-technical audience. "
            "Write one or two short, plain sentences about what the agent just found. No jargon, no lists."
        )},
        {"role": "user", "content": f"Step: {step['text']}\nWhat the agent found: {finding_text}"},
    ]

    # Demo fallback mode: ask a small-context model first and give it more text
    # than it can hold, so OpenRouter falls back to the next model for a real reason.
    small_model = os.getenv("DEMO_FALLBACK_PRIMARY", "").strip()
    if os.getenv("DEMO_FALLBACK", "0") == "1" and small_model:
        context = int(os.getenv("DEMO_FALLBACK_CONTEXT", "8192"))
        padding = "note " * (context + 2000)
        messages[1]["content"] = padding + "\n\nIgnore the notes above.\n\n" + messages[1]["content"]
        model = small_model

    try:
        text = await call_model(run_id, "narrator", model, messages, max_tokens=200)
        bus.emit(run_id, "narration", text.strip(), {"step_id": step["id"], "text": text.strip()})
    except Exception as error:
        bus.emit(run_id, "error", "Narrator failed", {"where": "narrator", "message": str(error)[:200]})
