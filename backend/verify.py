import asyncio

from pydantic import BaseModel

from backend.events import bus
from backend.llm import call_model, env_list


class Verdict(BaseModel):
    verdict: str   # "agree" | "disagree" | "unsure"
    reason: str    # one sentence


CHECKER_PROMPT = (
    "You are a careful fact checker. Decide whether the claim is accurate as stated. "
    "Answer agree, disagree, or unsure, with a one-sentence reason."
)


def is_split(verdicts):
    # Only agree vs disagree counts. Agree vs unsure is not a split.
    labels = [v["verdict"] for v in verdicts]
    return "agree" in labels and "disagree" in labels


def clean_verdict(text):
    text = (text or "").strip().lower()
    if text in ("agree", "disagree", "unsure"):
        return text
    return "unsure"


async def ask_one(run_id, model, claim):
    messages = [
        {"role": "system", "content": CHECKER_PROMPT},
        {"role": "user", "content": claim},
    ]
    try:
        result = await call_model(run_id, "checker", model, messages, schema=Verdict, max_tokens=800)
    except Exception as error:
        # 402 here usually means too many calls in flight for the credit balance, so wait and try once more.
        if "402" not in str(error):
            return {"model": model, "verdict": "error", "reason": str(error)[:150]}
        await asyncio.sleep(3)
        try:
            result = await call_model(run_id, "checker", model, messages, schema=Verdict, max_tokens=800)
        except Exception as error_again:
            return {"model": model, "verdict": "error", "reason": str(error_again)[:150]}
    if result is None:
        return {"model": model, "verdict": "error", "reason": "No valid reply"}
    return {"model": model, "verdict": clean_verdict(result.verdict), "reason": result.reason}


async def check_claim(run_id, claim):
    models = env_list("CHECKER_MODELS")
    verdicts = await asyncio.gather(*[ask_one(run_id, model, claim) for model in models])
    verdicts = list(verdicts)
    split = is_split(verdicts)
    if split:
        summary = f"Models disagree: {claim}"
    else:
        summary = f"No split: {claim}"
    bus.emit(run_id, "claim_check", summary, {"claim": claim, "verdicts": verdicts, "split": split})
    return {"claim": claim, "verdicts": verdicts, "split": split}


async def check_claims(run_id, claims):
    results = await asyncio.gather(*[check_claim(run_id, claim) for claim in claims])
    return list(results)
