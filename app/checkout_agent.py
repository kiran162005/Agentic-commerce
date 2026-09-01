"""
Checkout agent.

This is deliberately NOT "an LLM decides whether to charge the card."
The LLM (in buyer_agent.py) decides WHAT to buy and WHY. This agent is the
deterministic, auditable gate that decides WHETHER the purchase is allowed
to proceed and HOW to recover from a failure. Keeping these separate is the
whole point of "explainable, bounded, gated" — the money-moving logic
should never be something you have to hope the LLM got right.

Every audit entry produced by one checkout() call shares a `request_id`,
so the audit trail can be grouped by action instead of reading as one
undifferentiated stream.

Escalation is a REAL two-step flow, not just a label:
  1. checkout(req) with human_confirmed=False (the default) — if a gate
     needs a human's sign-off, this returns escalated=True and
     escalation_type='confirmation_required', and does NOT move any money.
  2. checkout(req, human_confirmed=True) — a human has now approved it.
     This re-runs the SAME checks, but gates that only needed confirmation
     (not a hard block) are allowed to pass, and execution proceeds.
A payment that fails after retries (escalation_type='payment_failure') is
a different, terminal kind of escalation — there's no "confirm" that makes
a declined card succeed, so there's no approve action for that case.
"""
import uuid
from dataclasses import dataclass
from app.catalog import Catalog
from app.audit import AuditLog
from app import razorpay_client


@dataclass
class CheckoutRequest:
    sku: str
    qty: int
    buyer_agent_id: str
    stated_reason: str  # why the buyer agent wants this — goes in the audit trail
    max_retries: int = 1


@dataclass
class CheckoutResult:
    success: bool
    reason: str
    order_id: str | None = None
    escalated: bool = False
    escalation_type: str | None = None  # 'confirmation_required' | 'payment_failure' | None


class CheckoutAgent:
    def __init__(self, catalog: Catalog, audit: AuditLog, hard_cap_paise: int, per_order_cap_paise: int):
        """
        hard_cap_paise: absolute ceiling this checkout agent will EVER process
                        without a human confirmation step, regardless of what
                        any buyer agent asks for.
        per_order_cap_paise: default per-order limit for autonomous execution.
        """
        self.catalog = catalog
        self.audit = audit
        self.hard_cap_paise = hard_cap_paise
        self.per_order_cap_paise = per_order_cap_paise

    def checkout(self, req: CheckoutRequest, human_confirmed: bool = False) -> CheckoutResult:
        request_id = uuid.uuid4().hex[:8]

        self.audit.record("purchase_intent", {
            "sku": req.sku, "qty": req.qty, "buyer_agent_id": req.buyer_agent_id,
            "stated_reason": req.stated_reason, "human_confirmed": human_confirmed,
        }, request_id=request_id)

        product = self.catalog.get(req.sku)
        if product is None:
            return self._fail(req, "no such SKU", request_id)

        amount_paise = product["price_paise"] * req.qty

        # --- GATE 1: catalog eligibility (stock, agent_purchasable flag, max qty) ---
        elig = self.catalog.eligibility(req.sku, req.qty)
        self.audit.record("eligibility_check", {
            "sku": req.sku, "qty": req.qty, "eligible": elig["eligible"], "reason": elig["reason"],
        }, request_id=request_id)

        if not elig["eligible"]:
            if elig["requires_confirmation"]:
                if not human_confirmed:
                    self.audit.record("escalated_to_human", {
                        "sku": req.sku, "reason": elig["reason"],
                    }, request_id=request_id)
                    return CheckoutResult(success=False, reason=elig["reason"], escalated=True, escalation_type="confirmation_required")
                # Human has approved — this gate no longer blocks. Fall through to GATE 2.
                self.audit.record("human_confirmed", {
                    "sku": req.sku, "reason": f"human approved override for: {elig['reason']}",
                }, request_id=request_id)
            else:
                # Hard block — no confirmation can fix this (bad SKU, no stock, over max qty)
                return self._fail(req, elig["reason"], request_id)

        # --- GATE 2: spending bound ---
        bound_ok = amount_paise <= self.per_order_cap_paise
        self.audit.record("bound_check", {
            "amount_paise": amount_paise, "per_order_cap_paise": self.per_order_cap_paise,
            "hard_cap_paise": self.hard_cap_paise, "within_per_order_cap": bound_ok,
        }, request_id=request_id)
        if not bound_ok:
            if amount_paise > self.hard_cap_paise:
                return self._fail(req, f"amount {amount_paise}p exceeds hard cap {self.hard_cap_paise}p — will never auto-approve", request_id)
            if not human_confirmed:
                self.audit.record("escalated_to_human", {
                    "sku": req.sku, "amount_paise": amount_paise,
                    "reason": "exceeds per-order autonomous cap, within hard cap — needs confirmation",
                }, request_id=request_id)
                return CheckoutResult(success=False, reason="needs human confirmation (over autonomous cap, under hard cap)", escalated=True, escalation_type="confirmation_required")
            self.audit.record("human_confirmed", {
                "sku": req.sku, "amount_paise": amount_paise,
                "reason": "human approved purchase over the autonomous cap",
            }, request_id=request_id)

        # --- EXECUTE with retry-then-escalate on decline ---
        attempts = 0
        last_reason = ""
        while attempts <= req.max_retries:
            result = razorpay_client.create_order(
                amount_paise=amount_paise,
                receipt=f"{req.buyer_agent_id}-{req.sku}-{attempts}",
                notes={"sku": req.sku, "qty": req.qty},
            )
            if result.success:
                self.catalog.reserve_stock(req.sku, req.qty)
                self.audit.record("payment_succeeded", {
                    "sku": req.sku, "qty": req.qty, "amount_paise": amount_paise,
                    "order_id": result.order_id, "attempt": attempts,
                }, request_id=request_id)
                return CheckoutResult(success=True, reason="ok", order_id=result.order_id)
            last_reason = result.reason
            self.audit.record("payment_declined", {
                "sku": req.sku, "amount_paise": amount_paise, "attempt": attempts, "reason": result.reason,
            }, request_id=request_id)
            attempts += 1

        # Retries exhausted -> graceful stop, not a crash, not a silent retry loop.
        # This is a TERMINAL escalation — no human "approve" click makes a declined card succeed.
        self.audit.record("escalated_to_human", {
            "sku": req.sku, "amount_paise": amount_paise,
            "reason": f"payment failed after {attempts} attempt(s): {last_reason}",
        }, request_id=request_id)
        return CheckoutResult(success=False, reason=f"payment failed after retries: {last_reason}", escalated=True, escalation_type="payment_failure")

    def _fail(self, req: CheckoutRequest, reason: str, request_id: str) -> CheckoutResult:
        self.audit.record("purchase_rejected", {"sku": req.sku, "qty": req.qty, "reason": reason}, request_id=request_id)
        return CheckoutResult(success=False, reason=reason)