"""
Buyer agent.

This is the part that's supposed to be genuinely agentic — it reasons over
the catalog against a goal and a budget, and decides what (if anything) to
buy, including deciding to buy NOTHING if nothing fits. It does NOT touch
Razorpay or the audit log directly; it calls the checkout agent, which is
the deterministic gate.

If GROQ_API_KEY is set, this uses a real LLM call (Groq's free tier,
Llama 3.1) to reason over the catalog. If the call fails for ANY reason
(bad key, network issue, rate limit, malformed response), this falls back
to the deterministic heuristic rather than crashing the request — an LLM
hiccup should degrade gracefully, the same principle as the payment-retry
logic in checkout_agent.py. The failure reason is surfaced in the result
so it's never silently swallowed.

Get a free Groq API key at https://console.groq.com/keys
"""
import os
import json
import urllib.request
import urllib.error

from app.catalog import Catalog
from app.checkout_agent import CheckoutAgent, CheckoutRequest

USE_LLM = os.environ.get("GROQ_API_KEY") is not None
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")


def _llm_choose(catalog_products: list, goal: str, budget_paise: int) -> dict:
    """Calls Groq (Llama 3.1) to pick a SKU + qty given a goal and budget.
    Raises on failure — the caller (shop()) is responsible for the fallback."""
    prompt = f"""You are a buyer agent shopping on behalf of a user.

Goal: {goal}
Budget: {budget_paise} paise (INR, 100 paise = 1 rupee)

Available products (JSON):
{json.dumps(catalog_products, indent=2)}

Pick at most ONE product and a quantity that best satisfies the goal within budget.
Respond with ONLY a JSON object, no other text, no markdown fences:
{{"sku": "<sku or null if nothing fits>", "qty": <int>, "reason": "<one sentence>"}}
"""
    body = json.dumps({
        "model": GROQ_MODEL,
        "max_tokens": 300,
        "temperature": 0.2,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode(errors="replace")
        raise RuntimeError(f"Groq API returned HTTP {e.code}: {error_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Groq API: {e.reason}") from e

    text = data["choices"][0]["message"]["content"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)


def _heuristic_choose(catalog_products: list, goal: str, budget_paise: int) -> dict:
    """Deterministic fallback: cheapest in-stock, agent-purchasable item whose category or name
    matches a keyword in the goal, within budget. This is NOT reasoning — it's a keyword match,
    labeled honestly as the fallback path."""
    goal_lower = goal.lower()
    candidates = [
        p for p in catalog_products
        if p["price_paise"] <= budget_paise
        and (p["category"] in goal_lower or any(w in goal_lower for w in p["name"].lower().split()))
    ]
    if not candidates:
        return {"sku": None, "qty": 0, "reason": "no in-budget product matched goal keywords (heuristic fallback)"}
    best = min(candidates, key=lambda p: p["price_paise"])
    return {"sku": best["sku"], "qty": 1, "reason": f"heuristic fallback: cheapest keyword match for '{goal}'"}


class BuyerAgent:
    def __init__(self, agent_id: str, catalog: Catalog, checkout_agent: CheckoutAgent):
        self.agent_id = agent_id
        self.catalog = catalog
        self.checkout_agent = checkout_agent

    def shop(self, goal: str, budget_paise: int):
        available = self.catalog.search(in_stock_only=True)
        mode = "llm" if USE_LLM else "heuristic"
        llm_error = None

        if USE_LLM:
            try:
                decision = _llm_choose(available, goal, budget_paise)
            except Exception as e:
                llm_error = str(e)
                decision = _heuristic_choose(available, goal, budget_paise)
                mode = "heuristic_fallback_after_llm_error"
        else:
            decision = _heuristic_choose(available, goal, budget_paise)

        if not decision.get("sku"):
            result = {"action": "no_purchase", "reason": decision.get("reason", "no suitable product"), "mode": mode}
            if llm_error:
                result["llm_error"] = llm_error
            return result

        result = self.checkout_agent.checkout(CheckoutRequest(
            sku=decision["sku"],
            qty=decision.get("qty", 1),
            buyer_agent_id=self.agent_id,
            stated_reason=decision.get("reason", ""),
        ))
        out = {
            "action": "purchase_attempt",
            "sku": decision["sku"],
            "qty": decision.get("qty", 1),
            "llm_reason": decision.get("reason", ""),
            "mode": mode,
            "result": result,
        }
        if llm_error:
            out["llm_error"] = llm_error
        return out