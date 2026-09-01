# Agentic Commerce Demo — Track 01

An agent-readable merchant catalog + a buyer agent that reasons over it within a
budget + a checkout agent that enforces bounds and gates before any money moves,
backed by Razorpay (test-mode) and a tamper-evident audit trail. Includes a live
web UI, not just a script.

## Hitting the bar, explicitly

| Requirement | Where |
|---|---|
| **Explainable** | Every decision carries a `stated_reason`, and the full chain of `eligibility_check` / `bound_check` / `escalated_to_human` / `human_confirmed` audit events shows exactly why something was allowed, blocked, or escalated |
| **Bounded** | Two caps in `checkout_agent.py`: a `per_order_cap_paise` for autonomous execution, and a `hard_cap_paise` that is *never* auto-approved regardless of human confirmation |
| **Gated** | `Catalog.eligibility()` checks stock, the `agent_purchasable` flag, and max-qty *before* any money moves. Items can be permanently blocked (out of stock — no confirmation fixes that) or blocked-pending-confirmation (high-value items — a human can actually approve these, see below) |
| **Audit trail** | `audit.py` — hash-chained JSONL log; `verify_chain()` detects any tampering after the fact; every action's log lines are grouped by a shared `request_id` so the trail reads as discrete actions, not one undifferentiated stream |
| **One failure handled gracefully** | The "Force declined payment" button in the UI triggers a card decline; the checkout agent retries once, then escalates cleanly — no crash, no infinite retry loop |

## Real human-in-the-loop escalation, not just a label

Escalation actually does something. When a purchase needs a human's sign-off
(over the autonomous spending cap, or a catalog item flagged
`requires_human_confirmation`), the checkout agent stops and returns
`escalated: true, escalation_type: "confirmation_required"` — no money moves.
The UI shows an **"Approve as human → complete purchase"** button on exactly
this kind of escalation. Clicking it re-runs the same checkout with
`human_confirmed: true`, which is logged as its own `human_confirmed` audit
event, and the purchase actually completes.

This is deliberately different from a **terminal** escalation — a payment that
failed after retries (`escalation_type: "payment_failure"`) gets no Approve
button, because no human confirmation makes a declined card succeed. The UI
distinguishes the two explicitly.

## Honest disclosure on AI vs. deterministic logic

- **Buyer agent (`buyer_agent.py`)**: if `GROQ_API_KEY` is set (Groq's free tier,
  Llama 3.1), this makes a real LLM call to reason over the catalog against a
  stated goal and budget. If the call fails for any reason — bad key, network
  issue, rate limit — it falls back to a keyword-match heuristic rather than
  crashing the request, and the failure reason is surfaced in the response as
  `llm_error` so it's never silently hidden. The response always labels which
  path ran: `"mode": "llm"`, `"heuristic"`, or `"heuristic_fallback_after_llm_error"`.
- **Checkout agent (`checkout_agent.py`)**: deliberately deterministic, not
  LLM-driven. The bound/gate/retry/escalation logic is plain code you can read
  and audit line by line. An LLM decides *what* to buy; it never decides
  *whether* money actually moves.
- **Razorpay integration (`razorpay_client.py`)**: mock mode by default (no
  keys needed, runs fully offline). Set `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`
  to create real orders against Razorpay's test-mode servers. One honest caveat:
  Razorpay's order-creation API always succeeds — a real decline only happens
  later, at payment capture, which needs the full checkout widget and a specific
  test card, not just a server call. So the "Force declined payment" path is
  always an explicit, clearly-labeled simulation, in both mock and live mode —
  it never claims to be a real Razorpay-side decline.

## Run it

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 9002
```

Open `http://127.0.0.1:9002`. No environment variables are required — it runs
fully offline in mock/heuristic mode out of the box.

### Optional: real LLM reasoning (free)

1. Get a free key at [console.groq.com/keys](https://console.groq.com/keys)
2. Copy `.env.example` to `.env` and set `GROQ_API_KEY=gsk_...`
3. Restart the server — the mode badge in the UI should switch from
   `heuristic` to `llm`

### Optional: real Razorpay test-mode orders

1. Sign up at Razorpay, switch to **Test Mode**, generate a test API key pair
2. Add `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` to `.env`
3. Restart — normal purchases will now create real orders visible in your
   Razorpay Test Mode dashboard (order IDs will look like `order_MxK7p...`
   instead of `order_mock_...`)

`.env` is git-ignored — `.env.example` shows the expected shape without real
secrets, so cloning this repo never leaks a key.

## Project structure

```
app/
  catalog.py          agent-readable product catalog + eligibility rules
  audit.py            hash-chained, self-healing audit log
  razorpay_client.py  Razorpay wrapper (mock + live modes)
  checkout_agent.py   deterministic bound/gate/retry/escalation logic
  buyer_agent.py       LLM (Groq) or heuristic buyer reasoning, with graceful fallback
  main.py             FastAPI layer wiring the agents to HTTP endpoints + serving the UI
data/catalog.json      sample merchant catalog
static/index.html      the ledger-style web UI
demo/demo_script.py    CLI walkthrough of the same pipeline, no server needed
```

## What's still a demo, not production

- Stock reservation is in-memory, not atomic/transactional
- No idempotency keys on retried Razorpay orders yet
- No webhook handling for async payment confirmation
- Single merchant, single currency (INR)
- The catalog resets to its original state (stock levels included) on server
  restart or via the "reset" button — intentional for repeatable demos, not a
  production persistence model