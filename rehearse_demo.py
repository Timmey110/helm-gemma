"""
Rehearsal tool for the Helm demo.

  python rehearse_demo.py claims          check every task's claims with every checker model
  python rehearse_demo.py claims ai-water check one task only
  python rehearse_demo.py small-models    list small-context models to use for the fallback demo
  python rehearse_demo.py fallback        test the staged fallback

Needs these environment variables (a .env file works too):
  OPENROUTER_API_KEY
  CHECKER_MODELS          comma separated, for example google/gemini-pro-latest,google/gemini-flash-latest,openai/gpt-5.2
  FALLBACK_PRIMARY_MODEL  only for the fallback test
  FALLBACK_BACKUP_MODEL   only for the fallback test
"""

import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

REPEATS = 2

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

VERDICT_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "claim_verdict",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["agree", "disagree", "unsure"]},
                "reason": {"type": "string"},
            },
            "required": ["verdict", "reason"],
            "additionalProperties": False,
        },
    },
}

CHECKER_PROMPT = (
    "You are a careful fact checker. Decide whether the claim is accurate as stated. "
    "Answer agree, disagree, or unsure, with a one-sentence reason."
)


def load_tasks():
    with open("demo_tasks.json") as file:
        return json.load(file)


def clean_json_text(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def check_claim(model, claim):
    start = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CHECKER_PROMPT},
                {"role": "user", "content": claim},
            ],
            response_format=VERDICT_FORMAT,
            max_tokens=1500,
        )
        data = json.loads(clean_json_text(response.choices[0].message.content))
        verdict = data["verdict"]
        reason = data["reason"]
        usage = response.usage.model_dump() if response.usage else {}
        cost = usage.get("cost")
        model_used = response.model
    except Exception as error:
        verdict = "error"
        reason = str(error)[:150]
        cost = None
        model_used = model
    return {
        "model": model,
        "model_used": model_used,
        "claim": claim,
        "verdict": verdict,
        "reason": reason,
        "seconds": round(time.time() - start, 1),
        "cost": cost,
    }


def describe_split(verdicts):
    real = [v for v in verdicts if v != "error"]
    if "agree" in real and "disagree" in real:
        return "SPLIT"
    if len(set(real)) > 1:
        return "soft split"
    return "agree"


def run_claims(only_task=None):
    checker_models = [m.strip() for m in os.environ["CHECKER_MODELS"].split(",") if m.strip()]
    tasks = load_tasks()["tasks"]
    if only_task:
        tasks = [t for t in tasks if t["id"] == only_task]

    jobs = []
    for task in tasks:
        for claim in task["seed_claims"]:
            for model in checker_models:
                for repeat in range(REPEATS):
                    jobs.append((task["id"], repeat, model, claim))

    print(f"Running {len(jobs)} checks across {len(checker_models)} models, {REPEATS} rounds each...\n")

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for job in jobs:
            futures.append((job, pool.submit(check_claim, job[2], job[3])))
        for job, future in futures:
            result = future.result()
            result["task"] = job[0]
            result["round"] = job[1]
            results.append(result)

    with open("rehearsal_results.json", "w") as file:
        json.dump(results, file, indent=2)

    scores = {}
    total_cost = 0
    for task in tasks:
        print("=" * 70)
        print(task["title"])
        print("=" * 70)
        score = 0
        for claim in task["seed_claims"]:
            round_labels = []
            for repeat in range(REPEATS):
                verdicts = [r["verdict"] for r in results
                            if r["task"] == task["id"] and r["claim"] == claim and r["round"] == repeat]
                round_labels.append(describe_split(verdicts))

            if all(label == "SPLIT" for label in round_labels):
                status = "STABLE SPLIT (great for the demo)"
                score += 3
            elif "SPLIT" in round_labels:
                status = "split sometimes (risky on stage)"
                score += 1
            elif "soft split" in round_labels:
                status = "soft split (agree vs unsure)"
                score += 1
            else:
                status = "all agree"

            print(f"\n  {claim}")
            print(f"  -> {status}")
            for r in results:
                if r["task"] == task["id"] and r["claim"] == claim and r["round"] == 0:
                    print(f"     {r['model']:<40} {r['verdict']:<9} {r['seconds']}s")
                    print(f"       {r['reason'][:110]}")
        scores[task["id"]] = score
        print(f"\n  Task score: {score}\n")

    for r in results:
        if r["cost"]:
            total_cost += r["cost"]

    print("=" * 70)
    print("Ranking (higher means more reliable disagreement):")
    for task_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        print(f"  {task_id:<25} {score}")
    print(f"\nTotal cost of this rehearsal: {round(total_cost, 4)} credits")
    print("Full results saved to rehearsal_results.json")
    print("Reminder: the control task should score 0. If it doesn't, your checker prompt is too touchy.")


def get_all_models():
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models") as response:
        return json.loads(response.read())["data"]


def list_small_models():
    small = []
    for model in get_all_models():
        context = model.get("context_length") or 0
        if 0 < context <= 16384:
            price = float(model.get("pricing", {}).get("prompt", "0") or 0)
            small.append((price, context, model["id"]))
    small.sort()
    print("Small-context models (cheapest first). Pick one as FALLBACK_PRIMARY_MODEL:\n")
    for price, context, model_id in small[:15]:
        print(f"  {model_id:<55} context {context:>6}   ${price * 1_000_000:.2f} per 1M input tokens")


def test_fallback():
    primary = os.environ["FALLBACK_PRIMARY_MODEL"]
    backup = os.environ["FALLBACK_BACKUP_MODEL"]

    context = 0
    for model in get_all_models():
        if model["id"] == primary:
            context = model.get("context_length") or 0
    if context == 0:
        print(f"Could not find {primary} in the OpenRouter model list. Check the slug.")
        return

    padding = "note " * (context + 4000)
    print(f"Primary {primary} holds about {context} tokens. Sending more than that...\n")

    start = time.time()
    response = client.chat.completions.create(
        model=primary,
        extra_body={"models": [backup]},
        messages=[
            {"role": "user", "content": padding + "\n\nIgnore the notes above. Reply with one short sentence: what is 2 + 2?"},
        ],
        max_tokens=50,
    )
    seconds = round(time.time() - start, 1)

    print(f"Requested: {primary}")
    print(f"Answered by: {response.model}")
    print(f"Reply: {response.choices[0].message.content}")
    print(f"Time: {seconds}s")
    if response.model != primary:
        print("\nFallback worked. This is what the models panel should show on stage.")
    else:
        print("\nThe primary answered, so no fallback happened. Pick a model with a smaller context.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "claims":
        run_claims(sys.argv[2] if len(sys.argv) > 2 else None)
    elif sys.argv[1] == "small-models":
        list_small_models()
    elif sys.argv[1] == "fallback":
        test_fallback()
    else:
        print(__doc__)
