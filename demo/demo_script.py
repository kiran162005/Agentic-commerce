"""
Run with:  python -m demo.demo_script
(from the agentic-commerce/ root, so the `app` package resolves)

Set ANTHROPIC_API_KEY for real LLM buyer reasoning; set RAZORPAY_KEY_ID +
RAZORPAY_KEY_SECRET for live Razorpay test-mode. Neither is required —
without them, this runs fully offline in mock/heuristic mode.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.catalog import Catalog
from app.audit import AuditLog
from app.checkout_agent import CheckoutAgent, CheckoutRequest
from app.buyer_agent import BuyerAgent

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "catalog.json")
AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "audit_log", "audit.jsonl")


def section(title):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main():
    # fresh audit log each demo run
    if os.path.exists(AUDIT_PATH):
        os.remove(AUDIT_PATH)

    catalog = Catalog(CATALOG_PATH)
    audit = AuditLog(AUDIT_PATH)
    checkout = CheckoutAgent(
        catalog=catalog, audit=audit,
        hard_cap_paise=1_000_000,      # ₹10,000 absolute ceiling, never auto-approved above this
        per_order_cap_paise=200_000,   # ₹2,000 autonomous limit
    )
    buyer = BuyerAgent(agent_id="buyer_agent_demo", catalog=catalog, checkout_agent=checkout)

    section("1. Normal purchase — buyer agent reasons over catalog within budget")
    result = buyer.shop(goal="I need something to carry water on a hike", budget_paise=100000)
    print(json.dumps(result, default=lambda o: o.__dict__, indent=2))

    section("2. Forced graceful failure — payment declined, retried, then escalated")
    forced_fail_req = CheckoutRequest(
        sku="NW-BTL-002", qty=1, buyer_agent_id="buyer_agent_demo",
        stated_reason="demo: forcing a decline to prove graceful recovery", max_retries=1,
    )
    # monkeypatch-free forced failure: directly hit razorpay_client with force_outcome via checkout internals
    import app.razorpay_client as rzp
    real_create_order = rzp.create_order
    rzp.create_order = lambda amount_paise, receipt, notes=None: real_create_order(
        amount_paise, receipt, {**(notes or {}), "force_outcome": "decline"}
    )
    forced_result = checkout.checkout(forced_fail_req)
    rzp.create_order = real_create_order
    print(json.dumps(forced_result, default=lambda o: o.__dict__, indent=2))

    section("3. Bound test — item priced above the human-confirmation threshold")
    over_cap_req = CheckoutRequest(
        sku="NW-JKT-100", qty=1, buyer_agent_id="buyer_agent_demo",
        stated_reason="demo: item is agent_purchasable=False, should be rejected outright",
    )
    over_cap_result = checkout.checkout(over_cap_req)
    print(json.dumps(over_cap_result, default=lambda o: o.__dict__, indent=2))

    section("4. Out-of-stock item — should reject cleanly, not crash")
    oos_req = CheckoutRequest(sku="NW-MUG-005", qty=1, buyer_agent_id="buyer_agent_demo", stated_reason="demo: out of stock")
    oos_result = checkout.checkout(oos_req)
    print(json.dumps(oos_result, default=lambda o: o.__dict__, indent=2))

    section("5. Full audit trail")
    for entry in audit.read_all():
        print(f"[{entry['event_type']}] {json.dumps(entry['payload'])}")

    section("6. Audit chain integrity check")
    valid, err = audit.verify_chain()
    print(f"Chain valid: {valid}" + (f" — {err}" if err else ""))


if __name__ == "__main__":
    main()
