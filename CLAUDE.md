# Helm: project spec for Claude Code

Helm is a cockpit for AI agents. A research agent runs a task while a person watches it in plain language, steers it mid-run, and answers decision gates when models disagree. It is a one-day hackathon build (8am to 5pm, Saturday Oct 17 2026) entered in the **Best use of OpenRouter** track. Gamma is a supporting piece: it turns a finished run into a shareable briefing.

**The pitch:** Helm uses OpenRouter as a team of models with different jobs. One does the work, one explains it, one listens to the human, and several check each other. When they disagree, a human decides.

## Hard rules

- **Feature freeze at 4:15pm.** After that, only bug fixes and demo prep. If a task would take longer than the time left, say so and propose the smaller version.
- **Working beats complete.** A narrow slice that runs live is better than a wide one that crashes. Keep the app runnable after every change.
- **Beginner-friendly code.** Plain functions, simple loops, clear names, no clever idioms or metaprogramming. Few comments, written like a person talking, not formal docs.
- **Tests live in `tests/`**, never mixed into implementation files.
- **Secrets stay on the backend.** Read keys from `.env`. Never log API keys or Gamma export URLs, and never send them to the browser.
- **Model slugs come from `.env`**, never hardcoded in logic.
- **Don't add new dependencies** beyond the list below without asking.

## Stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.11+, FastAPI, uvicorn, asyncio |
| LLM calls | `openai` Python SDK with `base_url="https://openrouter.ai/api/v1"` |
| Data models | Pydantic v2 |
| HTTP (Gamma) | `httpx` (async) |
| Live updates | Server-sent events (`StreamingResponse` with `text/event-stream`) |
| Config | `python-dotenv` |
| Frontend | React + Vite + Tailwind, `EventSource` for the stream |
| State | In-memory dict of runs, each run also saved to `runs/<run_id>.json` |

## Folder layout

```
backend/
  main.py        FastAPI app and endpoints
  events.py      Event model and the per-run event bus
  llm.py         The one function every model call goes through
  agent.py       The agent loop
  narrator.py    Plain-English summaries of each step
  steering.py    Turns human messages into plan edits
  verify.py      Claim checking across several models
  gamma.py       Gamma briefing (generate, poll, optional edits and comments)
  replay.py      Plays back a saved run
frontend/
  src/...        React app
tests/
runs/            saved runs (gitignored except the demo recordings)
demo_tasks.json  the rehearsed demo tasks
rehearse_demo.py standalone rehearsal script (already written, don't change it)
.env.example
```

## Environment variables (`.env.example`)

```
OPENROUTER_API_KEY=
GAMMA_API_KEY=
AGENT_MODEL=            # a Gemini Pro model, e.g. google/gemini-pro-latest
NARRATOR_MODEL=         # a fast Gemini Flash model
STEER_MODEL=            # a fast Gemini Flash model
CHECKER_MODELS=         # comma separated, 2 to 3 models, at least one non-Google if allowed
FALLBACK_MODELS=        # comma separated backups used for every role
DEMO_FALLBACK_PRIMARY=  # small-context model, only used when demo fallback mode is on
GATE_CONFIDENCE=0.6
```

Confirm exact slugs at openrouter.ai/models before filling these in.

## Event schema (decide once, don't change)

Every event the backend emits:

```python
class Event(BaseModel):
    run_id: str
    seq: int                # increasing number per run
    type: str               # see list below
    summary: str            # one line a human can read
    detail: dict = {}       # anything else the UI needs
    timestamp: float
```

Event types:

| type | when | detail fields |
| --- | --- | --- |
| `run_started` | run begins | `task` |
| `plan` | plan created or changed | `steps` (list of {id, text, status}) |
| `step_started` | a plan step begins | `step_id` |
| `finding` | the agent finished a step | `step_id`, `text`, `sources`, `confidence` |
| `narration` | narrator summary of a step | `step_id`, `text` |
| `claim_check` | checkers ruled on a claim | `claim`, `verdicts` (list of {model, verdict, reason}), `split` (bool) |
| `gate` | the agent is waiting for a human | `gate_id`, `reason`, `question`, `options` |
| `gate_answered` | human answered | `gate_id`, `answer` |
| `steer_received` | human sent a message | `text`, `source` ("ui" or "gamma") |
| `steer_applied` | plan was edited because of it | `edits` |
| `model_call` | after every model call | `role`, `model_requested`, `model_used`, `fallback_used`, `seconds`, `prompt_tokens`, `completion_tokens`, `cost` |
| `briefing` | Gamma status changed | `status`, `gamma_url` |
| `run_finished` | done | `total_cost`, `total_calls` |
| `error` | something failed | `where`, `message` |

The event bus keeps every event in a list per run (so a page refresh can replay history) and pushes new ones to any open SSE connections.

## The model helper (`llm.py`)

Every model call in the app goes through one function. This is what makes the OpenRouter track visible.

```python
async def call_model(run_id, role, model, messages, schema=None, web_search=False, max_tokens=2000):
    ...
```

It must:

1. Send `model` plus `extra_body={"models": FALLBACK_MODELS}` so OpenRouter can fall back.
2. If `schema` is a Pydantic model, send `response_format` as a strict JSON schema and parse the reply into that model. If parsing fails, retry once with a short "reply with valid JSON only" message, then emit an `error` event.
3. If `web_search` is true, turn on OpenRouter's web search plugin. Check the current OpenRouter docs for the exact request shape before writing it.
4. Read `response.model` (the model that actually answered) and `response.usage` (tokens and `cost`; use `response.usage.model_dump().get("cost")`).
5. Emit a `model_call` event with role, requested vs used model, `fallback_used = (used != requested)`, latency, tokens, cost.
6. Run the sync OpenAI client in a thread (`asyncio.to_thread`) or use `AsyncOpenAI`, so the server never blocks.

Roles: `agent`, `narrator`, `steer`, `checker`.

## The agent loop (`agent.py`)

One run = one asyncio task.

1. **Plan.** Ask the agent model for 4 to 6 short research steps (structured output). Emit `plan`.
2. **For each step:**
   1. Check the steering inbox. If it has messages, call `steering.apply()`, update the plan, emit `steer_applied` and a new `plan`.
   2. Emit `step_started`. Call the agent model with web search on, asking for a finding, up to 3 key claims, sources, and a confidence from 0 to 1. Emit `finding`.
   3. Call the narrator (in the background, don't wait on it) for a one to two sentence plain-English summary. Emit `narration`.
   4. Run `verify.check_claims()` on the step's claims.
   5. Open a gate if confidence is below `GATE_CONFIDENCE` or any claim has `split=True`. Emit `gate` and wait (asyncio.Event) until `/gate` is answered. Feed the answer back into the agent's context.
3. **Finish.** Ask the agent model for a final report in markdown with card breaks (`\n---\n`) between sections, 6 to 8 sections, including a short "how humans shaped this run" section built from `steer_applied` and `gate_answered` events. Start the Gamma briefing. Emit `run_finished` with totals.
4. Save every event to `runs/<run_id>.json` as it happens.

Keep the agent's context small: task, current plan, previous findings (short), and human answers.

## Steering (`steering.py`)

Input: the current plan and one human message. Output (structured):

```python
class PlanEdit(BaseModel):
    action: str        # "add_step" | "remove_step" | "change_step" | "change_focus"
    step_id: str | None
    text: str | None

class SteeringResult(BaseModel):
    edits: list[PlanEdit]
    reply: str         # one sentence telling the human what changed
```

Only edit steps that haven't started. `change_focus` adds a standing instruction the agent sees on every later step.

## Claim checking (`verify.py`)

For each claim, ask every model in `CHECKER_MODELS` in parallel with this structured output:

```python
class Verdict(BaseModel):
    verdict: str   # "agree" | "disagree" | "unsure"
    reason: str    # one sentence
```

`split = True` only when at least one checker says `agree` and another says `disagree`. Agree vs unsure is not a split. Never label a claim true or false in the UI; a split means "a human should look here."

## Gamma (`gamma.py`)

Base URL `https://public-api.gamma.app`, header `X-API-KEY`, not Bearer.

**Required (do this first):**

1. `POST /v1.0/generations` with `inputText` (the final report), `textMode: "preserve"`, `format: "presentation"`, `cardSplit: "inputTextBreaks"`, `imageOptions: {"source": "noImages"}`, `title`.
2. Poll `GET /v1.0/generations/{generationId}` every 5 seconds, up to 5 minutes, until `status` is `completed` or `failed`. Emit `briefing` events for pending, completed (with `gammaUrl`), failed.
3. Never block the agent loop on Gamma; run it as a background task.

**Optional, only if everything else works and before 4:15:**

- `GET /v1.0/gammas/{gammaId}/comments?updatedSince=<iso>` every 20 seconds; comments whose text starts with `@helm` go into the steering inbox with `source="gamma"`. The gamma ID is the id in the `gamma.app/docs/<id>` URL.
- `POST /v1.0/agent/gammas/{gammaId}/edits` with a `prompt` to update one page after a steer, then poll `GET /v1.0/agent/edits/{editId}`. Only one edit runs per page; on `409` skip it.

## API endpoints (`main.py`)

| Method and path | What it does |
| --- | --- |
| `POST /runs` | body `{task}`; starts a run, returns `{run_id}` |
| `GET /runs/{id}/events` | SSE stream; sends all past events first, then live ones |
| `POST /runs/{id}/steer` | body `{text}`; adds to the inbox, emits `steer_received` |
| `POST /runs/{id}/gate` | body `{gate_id, answer}`; releases the gate |
| `POST /runs/{id}/briefing` | starts a Gamma briefing now |
| `GET /runs/{id}/summary` | totals: calls, cost, models used, fallbacks |
| `GET /replay/{name}` | SSE stream replaying `runs/<name>.json` with the original timing (speed multiplier as a query param) |
| `GET /demo-tasks` | returns `demo_tasks.json` so the UI can offer them |

Enable CORS for the Vite dev server.

## Frontend

One page, three columns on a laptop screen:

- **Left: plan.** Steps with status (waiting, running, done, changed by human). Highlight steps that changed because of steering.
- **Middle: timeline.** Narration first, in large readable text. Findings collapsed under each step. Steering and gate events styled differently so the human's influence stands out.
- **Right: models panel.** A row per `model_call`: role, model used, seconds, cost. Fallbacks marked clearly ("primary failed, served by X"). Run totals at the top (calls, distinct models, total cost).
- **Bottom: steer box** (text input plus send) always visible.
- **Gate modal:** when a `gate` arrives, show the question and, for a split, each model's verdict and reason side by side, with buttons for the options.
- **Top bar:** task input with a dropdown of demo tasks, a Replay button, and the Gamma briefing link when ready.

Keep styling clean and readable from the back of a room: big type, high contrast, no clutter.

## Demo support

- `demo_tasks.json` holds the rehearsed tasks, their steer lines, and seed claims. The UI dropdown reads it.
- **Demo fallback mode:** an env flag or UI toggle that, for the narrator role only, makes `DEMO_FALLBACK_PRIMARY` (a small-context model) the requested model and pads the input past its context window, so OpenRouter falls back for a real reason. The models panel should show it. Never use a fake model ID; OpenRouter rejects unknown IDs instead of falling back.
- **Replay mode** must work with the network off (except loading the page), so it's the safety net on stage.

## Build order and cut order

Build in this order, and tell me when each "done when" passes:

1. `events.py`, `llm.py`, `agent.py` in the terminal. Done when one demo task runs start to finish and prints events.
2. SSE + frontend timeline + narrator. Done when a run is watchable in the browser.
3. Steering + gates. Done when a typed message visibly changes the plan.
4. `verify.py` + split gates. Done when a disagreement shows in the gate modal.
5. Models panel + fallback lists + demo fallback mode. Done when the panel fills and one fallback is visible.
6. Gamma briefing (required part). Done when a finished run shows a Gamma link.
7. Replay mode. Done when a saved run plays back.
8. Optional Gamma comments and page edits.

If time runs short, cut from the bottom: Gamma comments and edits first, then drag-and-drop or any plan editing beyond text steering. **Never cut:** live narration, text steering, split gates, the models panel, replay.

## When unsure

- Pick the simpler option and mention it in one line.
- If an external API behaves differently than this spec says, trust the API's current docs, adapt, and tell me what changed.
