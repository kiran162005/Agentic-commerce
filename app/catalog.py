"""
Agent-readable catalog interface.

A buyer agent should be able to query this the way it would query any
tool: structured in, structured out, no scraping or guessing required.
"""
import json


class Catalog:
    def __init__(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.merchant = self.data["merchant"]
        self.products = {p["sku"]: p for p in self.data["products"]}

    def search(self, category: str = None, max_price_paise: int = None, in_stock_only: bool = True) -> list:
        results = []
        for p in self.products.values():
            if category and p["category"] != category:
                continue
            if max_price_paise is not None and p["price_paise"] > max_price_paise:
                continue
            if in_stock_only and p["stock"] <= 0:
                continue
            results.append(p)
        return results

    def get(self, sku: str) -> dict | None:
        return self.products.get(sku)

    def is_purchasable(self, sku: str, qty: int) -> tuple[bool, str]:
        """Kept for backward compatibility — prefer eligibility() which distinguishes
        hard blocks from cases that should escalate to a human instead of rejecting outright."""
        result = self.eligibility(sku, qty)
        return result["eligible"], result["reason"]

    def eligibility(self, sku: str, qty: int) -> dict:
        """
        Returns {eligible, reason, requires_confirmation}.

        requires_confirmation=True means: this isn't a hard block (bad SKU, no stock,
        over max qty) — it's a case where the catalog says a human should sign off
        before the agent proceeds. The checkout agent uses this flag to decide
        whether to log a flat rejection or an escalation.
        """
        p = self.get(sku)
        if p is None:
            return {"eligible": False, "reason": f"no such SKU: {sku}", "requires_confirmation": False}
        if p["stock"] < qty:
            return {"eligible": False, "reason": f"insufficient stock: requested {qty}, have {p['stock']}", "requires_confirmation": False}
        if qty > p.get("max_qty_per_order", 999):
            return {"eligible": False, "reason": f"exceeds max qty per order ({p['max_qty_per_order']})", "requires_confirmation": False}
        if not p.get("agent_purchasable", False):
            return {
                "eligible": False,
                "reason": p.get("agent_purchasable_reason", "not eligible for agent purchase"),
                "requires_confirmation": p.get("requires_human_confirmation", False),
            }
        return {"eligible": True, "reason": "ok", "requires_confirmation": False}

    def reserve_stock(self, sku: str, qty: int):
        """Naive in-memory decrement — a real system would do this atomically at order-confirm time."""
        self.products[sku]["stock"] -= qty