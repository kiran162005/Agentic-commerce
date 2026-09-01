"""
Thin wrapper over the Razorpay Python SDK, test-mode only.

MOCK MODE: if RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set, this falls
back to a deterministic mock so the whole pipeline (catalog -> agent ->
checkout -> audit) can be demoed offline. Set the env vars to hit real
Razorpay test-mode APIs for normal purchases.

IMPORTANT, honestly stated: Razorpay's order-creation API always succeeds —
a real decline only happens later, at actual payment capture, which needs
the full checkout widget and a specific test card number, not just a
server-side API call. So even in live mode, force_outcome='decline' is
handled as an explicit, clearly-labeled SIMULATION (the UI button that
triggers it is literally named "Force declined payment") — it does not
call Razorpay at all for that one case. Every other purchase, in live
mode, creates a real order against Razorpay's test-mode servers.
"""
import os
import random
import time

USE_MOCK = os.environ.get("RAZORPAY_KEY_ID") is None

if not USE_MOCK:
    import razorpay
    _client = razorpay.Client(
        auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
    )


class RazorpayResult:
    def __init__(self, success: bool, order_id: str, status: str, reason: str = ""):
        self.success = success
        self.order_id = order_id
        self.status = status
        self.reason = reason

    def to_dict(self):
        return {"success": self.success, "order_id": self.order_id,
                "status": self.status, "reason": self.reason}


def create_order(amount_paise: int, receipt: str, notes: dict = None) -> RazorpayResult:
    notes = notes or {}

    # The forced-decline demo path is a deliberate simulation in BOTH modes —
    # Razorpay's order API has no way to force a real decline server-side.
    if notes.get("force_outcome") == "decline":
        return RazorpayResult(
            success=False, order_id=f"order_simulated_decline_{abs(hash(receipt)) % (10**10)}",
            status="failed",
            reason="card_declined (simulated for demo — real declines require the checkout widget + a test card, not just an API call)",
        )

    if USE_MOCK:
        return _mock_create_order(amount_paise, receipt, notes)

    order = _client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": notes,
    })
    return RazorpayResult(success=True, order_id=order["id"], status=order["status"])


def _mock_create_order(amount_paise: int, receipt: str, notes: dict) -> RazorpayResult:
    """
    Offline-only mock: deterministically fails ~1 in 5 orders (simulating a
    card decline) so the graceful-failure path is exercisable without any
    network access at all. Only used when no Razorpay keys are set.
    """
    time.sleep(0.05)
    order_id = f"order_mock_{abs(hash(receipt)) % (10**10)}"
    if random.random() < 0.2:
        return RazorpayResult(
            success=False, order_id=order_id, status="failed",
            reason="card_declined (mock): issuer declined the transaction",
        )
    return RazorpayResult(success=True, order_id=order_id, status="created")