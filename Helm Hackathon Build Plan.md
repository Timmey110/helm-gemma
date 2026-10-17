# Helm: Hackathon Build Plan

Oct 17, 2026 · @Pape Ndiaye

## Concept and pitch

Helm is a cockpit for AI agents: you watch the agent work in plain language, steer it mid-run, and share a living Gamma briefing that your teammates can steer too.

**Track: Best use of OpenRouter.** Helm's core is a team of models with different jobs, routed through one API: one does the work, one explains it, one listens to you, and several check each other. When they disagree, a human decides. Gamma stays in the project as the shareable briefing, but it is a supporting piece, not the headline.

**One-line pitch:** "Agents shouldn't be black boxes you review after the fact. Helm lets humans see, steer, and share AI work while it's happening."

Three layers:

- **See:** every agent action streams into a live timeline, narrated in plain English by a fast model.
- **Steer:** type an instruction, edit the plan, or answer a decision gate when the agent is unsure.
- **Trust:** key claims are cross-checked by multiple models, and disagreements are flagged for a human.

**How the two sponsor tools fit:**

| Tool | Job in Helm |
| --- | --- |
| OpenRouter (Gemini) | The agent itself, the live narrator, the steering interpreter, and the multi-model claim checker. Four distinct model roles routed through one API. |
| Gamma | The shareable briefing. Generated at the first milestone, then updated page by page through the agent edit API. Comments people leave on the Gamma are read back through the comments API and fed to the agent as steering. |

The Gamma comment loop is a strong supporting moment (keep it only if it already works): a manager who never opens Helm can comment "dig deeper on pricing" on the shared Gamma, and the agent changes course. Gamma stops being an export format and becomes a second steering surface.

## Tech stack

Use a Python FastAPI backend with a React frontend, run locally for the demo. The agent loop runs for minutes and polls Gamma in the background, which is awkward on serverless hosting with time limits.

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | Python 3.11+, FastAPI, asyncio | Simple long-running loops, easy background tasks, readable code |
| Live updates | Server-sent events (SSE) | One-way stream server to browser, simpler than WebSockets; steering goes back as plain POST requests |
| LLM calls | `openai` Python SDK pointed at `https://openrouter.ai/api/v1` | OpenRouter is OpenAI-compatible, so no extra SDK |
| Structured output | Pydantic models + OpenRouter `response_format` (JSON schema) | Every agent event, claim, and plan edit is typed JSON the UI can render |
| Web research | OpenRouter web search plugin | No separate search API key to manage |
| Gamma | `httpx` calls to `public-api.gamma.app`, header `X-API-KEY` | Generate, edit, comments, poll every 5 s |
| Frontend | React + Vite + Tailwind | Fast to set up, `EventSource` handles SSE natively |
| State | In-memory Python dict per run, plus a JSON file dump | No database needed for one demo; the dump is your fallback replay |
| Hosting | Your laptop for the demo; Railway or Render only if time allows | Fewer moving parts on stage |

**Models (Gemini through OpenRouter).** OpenRouter currently lists Gemini 3.5 to 3.8 Flash plus "Gemini Pro Latest" and "Gemini Flash Latest" aliases. Confirm exact slugs on [openrouter.ai/google](https://openrouter.ai/google) tonight and put them in `.env`, never hardcoded.

| Role | Suggested model | Reason |
| --- | --- | --- |
| Agent (plans, researches, writes) | Gemini Pro Latest | Best reasoning; slow is fine |
| Narrator (plain-English play-by-play) | Newest Gemini Flash | Must be fast and cheap, called on every event |
| Steering interpreter (message to plan edits) | Newest Gemini Flash | Short structured output |
| Claim checkers (2 to 3) | One Gemini plus one or two non-Google models | Models from the same family share blind spots; a different provider makes disagreement meaningful |

If the hackathon requires Gemini only, keep all checkers Gemini but vary the version (Pro vs Flash), and say so honestly in the pitch.

**OpenRouter features to make visible (track judging):**

- **Fallbacks:** pass a `models` list in the request; the first is primary, the rest are fallbacks OpenRouter tries if it fails. The response's `model` field says which one actually answered, so you can show it.
- **Cost and tokens:** every response's `usage` object includes token counts and cost in credits. Log it per call; that feeds the models panel.
- **Structured outputs:** every role returns a JSON schema, which is what lets four different models plug into one pipeline.

Sources: [OpenRouter Google models](https://openrouter.ai/google), [Gamma API docs](https://developers.gamma.app/llms.txt)

## Architecture

Everything the agent does flows out through one event stream, and every human instruction flows back through one inbox the agent reads between steps.

&#91;embedded content: Helm architecture · 3 backend parts, 3 outside parties\]

The UI and Gamma are both steering surfaces; the agent never needs to know which one a message came from.

**One step of the agent loop:**

1. Read the steering inbox. If it has messages, send them to the interpreter model, apply the returned plan edits, and emit a `steer_applied` event.
2. Take the next plan step and call the agent model with the web search plugin. Ask for structured output: finding, claims, sources, confidence.
3. Emit the events; the narrator turns the step into one or two plain sentences.
4. If confidence is below the threshold or the claim checkers disagree, emit a `gate` event and wait for the human's answer.
5. At a milestone (the first findings, then after each steer), queue a Gamma job: a full generation the first time, a single-page edit after that.

**Background tasks:** a Gamma job poller every 5 seconds, and a comment poller every 15 to 20 seconds using `updatedSince`, which drops `@helm` comments into the inbox.

| Endpoint | Purpose |
| --- | --- |
| `POST /runs` | Start a run with a task |
| `GET /runs/{id}/events` | SSE stream of events |
| `POST /runs/{id}/steer` | Send a steering message |
| `POST /runs/{id}/gate` | Answer a decision gate |
| `POST /runs/{id}/briefing` | Generate or refresh the Gamma now |
| `GET /runs/{id}/replay` | Play back a recorded run |

## Key design decisions

The most important call: the agent loop checks a steering inbox between every step, so humans can change its course without killing the run.

1. **One event schema for everything.** Every action becomes `{run_id, step, type, summary, detail, confidence, timestamp}` with types `plan`, `search`, `read`, `finding`, `claim`, `gate`, `steer_applied`, `briefing`. The timeline, narrator, and Gamma briefing all read the same stream. Decide this first and don't change it after 10 am.
2. **Steer between steps, never mid-call.** The agent finishes its current model call, then reads the inbox (UI messages plus Gamma comments). A steering message goes to the interpreter model, which returns structured plan edits (`add_step`, `remove_step`, `reorder`, `change_focus`). Applied edits are logged as `steer_applied` events, so humans see that the agent listened.
3. **Decision gates on two triggers only.** The agent pauses when its self-reported confidence is under a threshold, or when claim checkers disagree. Keep the rule this simple; judges should be able to predict when a gate appears.
4. **Narrate in batches.** Calling the narrator on every tiny event is slow and noisy. Narrate once per completed step, in one or two sentences.
5. **Gamma for sharing, the UI for live.** Gamma jobs take one to three minutes, so the live view never waits on Gamma. Keep Gamma simple: generate one briefing at the end of the run. Only add page edits or comment steering if they already work.
6. **Comments as steering, with a filter.** Poll Gamma comments with `updatedSince` every 15 to 20 seconds. Only treat comments that start with a keyword (for example `@helm`) as instructions, so casual remarks don't redirect the agent.
7. **Disagreement means "look here," not "false."** When models disagree on a claim, the UI says it needs human review. Never label claims true or false.
8. **Record every run to a JSON file.** That gives you a replay mode: if the network fails on stage, you play back a perfect run with the same UI.
9. **Keep code beginner-friendly.** Plain functions, one file per concern (`agent.py`, `narrator.py`, `steering.py`, `verify.py`, `gamma.py`, `main.py`), tests in a separate `tests/` folder. You'll be debugging tired at 3 pm; clever code costs you there.
10. **Secrets stay on the server.** OpenRouter and Gamma keys live in `.env` on the backend. Gamma export links work for anyone who has them, so don't log them or ship them to the browser.

**Added for the OpenRouter track:**

1. **Every model call goes through one function** that records role, requested model, model used, latency, tokens, and cost, then emits a `model_call` event. The models panel just renders those events.
2. **Disagreement is the headline.** Claim checking moves from "cut first" to "never cut." Use at least one non-Gemini checker if the rules allow, so a split between providers means something.
3. **Show one fallback on purpose.** For the demo, make one role's primary a small-context model and send it more text than it can hold. OpenRouter treats the context-length error as a failure and serves the next model in the list, so judges see Helm recover for a real reason. Rehearse it; don't improvise it.

## Hour-by-hour build plan

The morning built the loop; from 2 pm every hour goes to making OpenRouter's role visible, and feature work stops at 4:15.

&#91;embedded content: Build day timeline · 8 phases, revised at 2pm, freeze at 4:15\]

Each phase has a "done when" test. Don't start the next phase until it passes.

1. **8:00 to 10:00, skeleton.** Repo, `.env`, Pydantic event schema, and an agent loop that plans 4 to 6 steps and runs web search through OpenRouter. *Done when* one task runs start to finish in the terminal.
2. **10:00 to 11:30, live timeline and narrator.** SSE endpoint, React timeline and plan panel, narrator summarizing each step. *Done when* you can watch a run in the browser.
3. **11:30 to 1:00, steering and gates.** Steer endpoint into the inbox, interpreter returns plan edits, low-confidence gate pauses until answered. *Done when* a typed instruction visibly changes the plan.
4. **1:00 to 2:00, Gamma briefing (basic).** One `POST /generations` at the end of a run, poll, show the link. Keep page edits and comment steering only if they already work. *Done when* a finished run produces a Gamma link.
5. **2:00 to 2:45, claim checking (never cut).** Extract 3 to 5 key claims, ask 2 to 3 models (at least one non-Gemini if allowed) to agree or disagree with structured output, and turn any split into a decision gate showing each model's answer side by side. *Done when* a real disagreement appears in the UI.
6. **2:45 to 3:30, models panel and fallbacks.** Route every call through one helper that logs role, model used, latency, tokens, and `usage.cost`. Show a live panel plus a run total. Add a `models` fallback list to each role and stage one fallback for the demo. *Done when* the panel fills during a run and one fallback shows up.
7. **3:30 to 4:15, polish and replay.** Record a clean run to JSON, add replay mode, loading states, and error messages.
8. **4:15 to 5:00, freeze and rehearse.** No new features. Rehearse the demo twice and generate your pitch deck through Gamma.

**If you fall behind, cut in this order:** Gamma page edits and comment steering, then drag-to-edit plan (keep text steering), then the staged fallback demo (keep the fallback list in code). **Never cut:** live narration, text steering, claim-checking disagreements, and the models panel.

## Demo script and pitch

The demo should land three OpenRouter moments in under four minutes: the models disagree and a human decides, the models panel shows who did what for how much, and a fallback keeps the run alive.

**Pick one rehearsed task.** Something judges relate to, with a known disagreement in the sources, for example "Should URI students use AI tutors? Research the evidence and costs."

1. **Problem (30 s):** "Agents now work for minutes or hours. Today you either trust the output blindly or reread everything. Neither keeps humans in control."
2. **Start the run (30 s):** type the task. The timeline fills, the narrator explains each step, and the models panel starts filling with each call's role, model, and cost.
3. **Steer (30 s):** type "focus on community colleges, skip pricing." The plan visibly rewrites and a `steer_applied` event appears.
4. **Disagreement gate (60 s), the centerpiece:** the agent pauses on a claim where checkers from different providers split. Show each model's answer side by side; you pick one. Line: "One model would have been confidently wrong. Several models, routed through one API, told us exactly where to look."
5. **Fallback (30 s):** point at the staged fallback in the models panel: the first model failed, OpenRouter served the next one, and the run never stopped.
6. **Close (40 s):** show the run total (calls, models, cost), open the Gamma briefing, and say: "Helm uses OpenRouter as a team of models with different jobs: one does the work, one explains it, one listens to you, and several check each other. Next: an SDK so any agent can plug into Helm."

**Pitch points to have ready for questions:**

- Who it's for: anyone the agent's work is *for*, not just the engineer who built the agent.
- Why now: agents run longer and more autonomously, and human oversight is becoming a requirement for high-risk AI systems in places like the EU.
- Business path: free for individuals, paid team workspaces, then an SDK and MCP server so other agent products can embed the cockpit.
- Honest limit: multi-model agreement isn't proof of truth; Helm tells humans where to look.

**Demo task suite** (in `demo_tasks.json`; test with `python rehearse_demo.py claims`):

| Task | Steer line | Where models should split | Use |
| --- | --- | --- | --- |
| Environmental cost of chatbots | "Focus on one person's daily use, skip data center construction." | Water and energy per query | Main demo |
| Should URI students use AI tutors? | "Focus on intro STEM courses, skip pricing." | The "twice as much learning" study | Backup, best for a student audience |
| Remote work vs the office | "Focus on software engineering teams." | Whether fully remote is more productive | Backup |
| Is the 10,000-hour rule real? | "Focus on learning to code in college." | How much practice explains skill | Backup |
| Photosynthesis (control) | "Focus on the leaf, skip the equations." | None, should all agree | Proves Helm doesn't cry wolf |

The table order is a guess; the script's ranking is what decides. Demo whichever task gets a stable split in both rehearsal rounds.

## Tonight checklist and risks

Do the account setup tonight and sleep; every item here is a thing that can eat your first hour tomorrow.

- [ ] Gamma API key from Settings > API Keys (needs a Pro, Ultra, Teams, or Business plan); check your credit balance
- [ ] One test `POST /v1.0/generations` with 3 cards and `imageOptions.source: noImages` to confirm the key works
- [ ] OpenRouter API key with credits; one test call to a Gemini Flash model
- [ ] Confirm exact Gemini model slugs on openrouter.ai/google and note which support structured outputs and tool calling
- [ ] Ask organizers or check the rules: do the checker models have to be Gemini?
- [ ] Create the repo with `backend/`, `frontend/`, `tests/`, and a `.env.example`
- [ ] Sleep at least four hours

| Risk | Fallback |
| --- | --- |
| Checkers agree on everything during the demo | Rehearse with a task that has a known disputed fact, and keep that run in replay |
| The staged fallback doesn't trigger | Show it from the recorded replay run |
| `usage.cost` missing for some model | Show tokens and latency; cost is a bonus |
| A Gemini model slug changes or rate-limits | The `models` fallback list in every request covers it |
| Gamma generation is slow during judging | Generate the briefing before you present and have it open in another tab |
| Gamma page edits or comment polling misbehave | Drop them; one end-of-run briefing is enough for this track |
| Wi-Fi fails | Replay mode from the recorded JSON run |
| Running out of Gamma credits | Use `noImages` and 5 to 8 cards per briefing |
| Scope creep | Follow the cut order in the build plan |
