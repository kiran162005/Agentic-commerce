"""
FastAPI layer over the existing agents — no changes to catalog/audit/checkout
logic, just HTTP endpoints so the UI (and, later, a real external agent) can
call this over the network instead of importing Python directly.

Run with:  python -m uvicorn app.main:app --reload
Then open: http://127.0.0.1:8000
"""
import os
import shutil

from dotenv import load_dotenv
load_dotenv()  # reads .env in the project root, so GROQ_API_KEY etc. don't need to be set every terminal session

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.catalog import Catalog
from app.audit import AuditLog
from app.checkout_agent import CheckoutAgent, CheckoutRequest
from app.buyer_agent import BuyerAgent, USE_LLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(ROOT, "data", "catalog.json")
CATALOG_BACKUP_PATH = os.path.join(ROOT, "data", "catalog.original.json")
AUDIT_PATH = os.path.join(ROOT, "audit_log", "audit.jsonl")

if not os.path.exists(CATALOG_BACKUP_PATH):
    shutil.copy(CATALOG_PATH, CATALOG_BACKUP_PATH)

app = FastAPI(title="Agentic Commerce Demo")

state = {}


def _init_state():
    if os.path.exists(AUDIT_PATH):
        os.remove(AUDIT_PATH)
    shutil.copy(CATALOG_BACKUP_PATH, CATALOG_PATH)
    catalog = Catalog(CATALOG_PATH)
    audit = AuditLog(AUDIT_PATH)
    checkout = CheckoutAgent(
        catalog=catalog, audit=audit,
        hard_cap_paise=1_000_000,
        per_order_cap_paise=200_000,
    )
    buyer = BuyerAgent(agent_id="buyer_agent_ui", catalog=catalog, checkout_agent=checkout)
    state["catalog"] = catalog
    state["audit"] = audit
    state["checkout"] = checkout
    state["buyer"] = buyer


_init_state()


class ShopRequest(BaseModel):
    goal: str
    budget_paise: int


class CheckoutBody(BaseModel):
    sku: str
    qty: int = 1
    force_outcome: str | None = None  # "decline" to force the graceful-failure demo path
    human_confirmed: bool = False  # set True to approve an escalated (confirmation_required) purchase


@app.get("/api/config")
def config():
    return {"mode": "llm" if USE_LLM else "heuristic",
            "hard_cap_paise": state["checkout"].hard_cap_paise,
            "per_order_cap_paise": state["checkout"].per_order_cap_paise}


@app.get("/api/catalog")
def get_catalog():
    return {"merchant": state["catalog"].merchant, "products": list(state["catalog"].products.values())}


@app.post("/api/shop")
def shop(req: ShopRequest):
    result = state["buyer"].shop(goal=req.goal, budget_paise=req.budget_paise)
    result["result"] = result.get("result").__dict__ if result.get("result") else None
    return result


@app.post("/api/checkout")
def checkout(body: CheckoutBody):
    import app.razorpay_client as rzp
    real_create_order = rzp.create_order
    if body.force_outcome:
        rzp.create_order = lambda amount_paise, receipt, notes=None: real_create_order(
            amount_paise, receipt, {**(notes or {}), "force_outcome": body.force_outcome}
        )
    result = state["checkout"].checkout(CheckoutRequest(
        sku=body.sku, qty=body.qty, buyer_agent_id="manual_ui_action",
        stated_reason="manual checkout triggered from UI",
    ), human_confirmed=body.human_confirmed)
    rzp.create_order = real_create_order
    return result.__dict__


@app.get("/api/audit")
def get_audit():
    return {"entries": state["audit"].read_all()}


@app.get("/api/verify")
def verify():
    valid, err = state["audit"].verify_chain()
    return {"valid": valid, "error": err}


@app.post("/api/reset")
def reset():
    _init_state()
    return {"ok": True}


app.mount("/", StaticFiles(directory=os.path.join(ROOT, "static"), html=True), name="static")