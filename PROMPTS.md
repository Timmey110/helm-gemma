# Helm: prompts to paste into Claude Code

Put `CLAUDE.md`, `demo_tasks.json`, and `rehearse_demo.py` in the repo root first. Claude Code reads `CLAUDE.md` on its own at the start of every session, so these prompts stay short. Paste them one at a time and check each "done when" yourself before moving on.

## 0. Kickoff (paste first)

```
Read CLAUDE.md, demo_tasks.json, and whatever code already exists in this repo.
Then give me, in under 10 lines:
1. What already exists and works, compared to the spec's build order.
2. Which build step we should start from.
3. Anything in the spec you think will break or take too long, given the 4:15pm feature freeze.
Don't write code yet.
```

## 1. Backend core

```
Build step 1 from CLAUDE.md: events.py, llm.py, agent.py, plus .env.example and requirements.txt.
Add a small script so I can run one demo task from the terminal and see the events print.
Skip steering, gates, verify, and Gamma for now, but leave clear places for them in the loop.
Stop when step 1's "done when" passes and tell me the exact command to run.
```

## 2. Live UI

```
Build step 2: the SSE endpoint, the React + Vite + Tailwind frontend (plan, timeline, models panel placeholder, steer box placeholder), and the narrator.
The page should load past events on refresh.
Tell me the commands to start the backend and frontend.
```

## 3. Steering and gates

```
Build step 3: the steering inbox, steering.py, the /steer and /gate endpoints, the confidence gate, and the gate modal in the UI.
Make human influence visually obvious: changed plan steps and steer events should stand out.
```

## 4. Claim checking

```
Build step 4: verify.py with parallel checker calls, claim_check events, and split gates.
In the gate modal, show each checker model's verdict and reason side by side.
Then run the ai-water demo task and tell me whether any claim split.
```

## 5. Models panel and fallbacks

```
Build step 5: the full models panel with run totals, fallback lists on every call, and demo fallback mode for the narrator as described in CLAUDE.md.
Make a fallback impossible to miss in the panel.
```

## 6. Gamma briefing

```
Build step 6: gamma.py required part only. Generate the briefing from the final report as a background task, poll every 5 seconds, emit briefing events, and show the link in the top bar.
Include the "how humans shaped this run" section in the report.
```

## 7. Replay

```
Build step 7: replay mode. Save runs as they happen, add GET /replay/{name} with a speed option, and a Replay button in the UI.
Then tell me how to record the demo run I'll use as a backup.
```

## 8. Optional, only before 4:15

```
If it's before 3:45pm and everything above works: add the optional Gamma comment polling (@helm comments become steering with source "gamma").
Skip the page edits unless this takes under 15 minutes.
```

## Useful one-liners

- When something breaks: `The app fails with this error: <paste>. Find the cause, fix it with the smallest change, and tell me what it was.`
- When Claude Code goes too big: `Too much. Give me the smallest version that works for the demo.`
- At 4:15: `Feature freeze. From now on only fix bugs. Review the app for anything likely to crash during a live demo and list it, most likely first.`
- Before presenting: `Walk through the demo script in CLAUDE.md's spirit: start ai-water, steer, trigger a split gate, show a fallback. List anything that didn't behave.`
