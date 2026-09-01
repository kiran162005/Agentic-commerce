# Agentic Commerce Demo — Track 01

An agent-readable catalog + a buyer agent that shops within a budget + a checkout
agent that executes bounded, gated purchases on Razorpay (test-mode) with a
tamper-evident audit trail.

## Hitting the bar, explicitly

| Requirement | Where |
|---|---|
| **Explainable** | Every purchase decision comes with a `stated_reason` (buyer agent) and a chain of audit events (`eligibility_check`, `bound_check`) showing exactly why it was allowed or rejected |
| **Bounded** | Two caps in `checkout_agent.py`: a `per_order_cap_paise` for autonomous execution and a `hard_cap_paise` that is *never* auto-approved, no matter what the buyer agent asks for |
| **Gated** | `Catalog.is_purchasable()` checks stock, `agent_purchasable` flag, and max-qty *before* any money moves; items like the leather jacket are permanently excluded from autonomous purchase |
| **Audit trail** | `audit.py` — hash-chained JSONL log, `verify_chain()` detects any tampering after the fact |
| **One failure handled gracefully** | `demo_script.py` step 2 forces a card decline; the checkout agent retries once, then escalates cleanly instead of crashing or silently retrying forever |

## Honest disclosure on AI vs. deterministic logic

- **Buyer agent (`buyer_agent.py`)**: if `ANTHROPIC_API_KEY` is set, this makes a real
  LLM call to reason over the catalog against a stated goal and budget. Without a key,
  it falls back to a keyword-match heuristic — labeled `"mode": "heuristic"` in the
  output so it's never mistaken for the AI path.
- **Checkout agent (`checkout_agent.py`)**: deliberately deterministic, not LLM-driven.
  The bound/gate/retry logic is code you can read and audit line by line — that's the
  point. An LLM decides *what* to buy; it never decides *whether* money moves.
- **Razorpay integration**: mock mode by default (no keys needed, runs offline).
  Set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` to hit real Razorpay test-mode APIs.

## Run it

```bash
pip install -r requirements.txt
python -m demo.demo_script
```

No environment variables required for the offline demo. To use real LLM reasoning:

```bash
export ANTHROPIC_API_KEY=sk-...
python -m demo.demo_script
```

To use live Razorpay test-mode instead of the mock:

```bash
export RAZORPAY_KEY_ID=rzp_test_...
export RAZORPAY_KEY_SECRET=...
python -m demo.demo_script
```

## Project structure

```
app/
  catalog.py          agent-readable product catalog
  audit.py            hash-chained audit log
  razorpay_client.py  Razorpay wrapper (mock + live modes)
  checkout_agent.py   deterministic bound/gate/retry logic
  buyer_agent.py       LLM (or heuristic) buyer reasoning
data/catalog.json      sample merchant catalog
demo/demo_script.py    runs the full pipeline + prints the audit trail
```

## What's still a demo, not production

- Stock reservation is in-memory, not atomic/transactional
- No idempotency keys on retried Razorpay orders yet
- No webhook handling for async payment confirmation
- Single merchant, single currency (INR)
