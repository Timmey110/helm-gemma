# Helm: a cockpit for AI agents, powered by Gemma

**Tracks:** Best Use of Gemma · Best Open-Source AI Project
**Submit on MLH by 3:30 PM** (closes 4:00 PM).

## One line

Agents shouldn't be black boxes you review after the fact. Helm lets you watch an AI agent work in plain language, steer it mid-run, and decide when open models disagree.

## What it does

- **See:** Gemma plans the research, works each step with web search, and a second Gemma model narrates every step in one or two plain sentences.
- **Steer:** type an instruction at any time ("focus on one person's daily use"). A Gemma model turns it into plan edits, and the changed steps are highlighted as "changed by human".
- **Trust:** every key claim is fact-checked by three open-weight models from three different labs: **Gemma (Google), gpt-oss (OpenAI), Mistral Small (Mistral)**. When they split (one agrees, one disagrees), the agent pauses and shows each model's verdict and reason side by side. A human decides.

## How Gemma is used

Gemma 4 fills four of the five jobs in the system:

| Job | Model |
| --- | --- |
| Agent: plans, researches with web search, writes the report | `google/gemma-4-31b-it` |
| Narrator: plain-English play-by-play | `google/gemma-4-26b-a4b-it` (MoE, fast and cheap) |
| Steering interpreter: human message to structured plan edits | `google/gemma-4-26b-a4b-it` |
| Claim checker #1 | `google/gemma-4-31b-it` |
| Claim checkers #2 and #3 | `openai/gpt-oss-120b`, `mistralai/mistral-small-2603` |

Every model is open-weight. Every reply is structured JSON (Pydantic schemas), so different models plug into one pipeline. All calls go through OpenRouter with a fallback list, and the models panel shows role, model, latency, and cost for every call.

## Why it matters

Agents now run for minutes and take many steps. Today you either trust the output blindly or reread everything. Helm keeps a human in the loop *during* the work, and uses disagreement between independent open models to point the human at exactly the claims worth checking. A split never means "false"; it means "look here".

## How it's built

Python FastAPI backend with an asyncio agent loop, server-sent events to a single-page live cockpit, and every run saved to JSON so it can be replayed offline.

```
python -m uvicorn backend.main:app --port 8000     # then open http://localhost:8000
python run_task.py ai-water                         # terminal run, gates auto-answered
python -m pytest tests                              # unit tests
```

## Honest limits

- Agreement between models is not proof of truth. Helm tells humans where to look.
- Built in one day: Gamma briefing export and Gamma comment steering from the original plan were cut.

## Demo script (under 4 minutes)

1. **Problem (30s).** "Agents work for minutes now. You either trust them blindly or reread everything."
2. **Start (30s).** Pick "The real environmental cost of chatbots", press Start. Plan appears, Gemma narrates each step, the models panel fills.
3. **Steer (30s).** Type "Focus on what one person's daily use costs, skip data center construction." The plan rewrites and the changed steps light up amber.
4. **Disagreement gate (60s).** Gemma, gpt-oss and Mistral split on a per-query water or energy figure. Show the three verdicts side by side and pick an option. Line: "One model would have been confidently wrong. Three open models told us exactly where to look."
5. **Close (40s).** Point at run totals: calls, models, cost in fractions of a cent. "Helm uses Gemma as a team: one does the work, one explains it, one listens to you, and three open models check each other."

**Safety net:** if Wi-Fi fails, choose a saved run and press **Replay**.
