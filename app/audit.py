"""
Hash-chained audit log.

Every entry embeds the hash of the previous entry, so any tampering or
deletion breaks the chain and is detectable by verify_chain(). This is the
same pattern used in HealthThread's access log, applied here to money
actions instead of health-record access.

Design choice, stated plainly: this is NOT a blockchain, NOT distributed,
and NOT tamper-proof against someone with write access to the log file
itself. It IS tamper-evident: if the file is edited after the fact, the
chain verification will fail and say exactly where.
"""
import json
import hashlib
import os
import time
from typing import Optional

GENESIS_HASH = "0" * 64


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").close()

    def _ensure_file(self):
        """Recreates the log file if it's missing — e.g. someone deleted it by hand
        while the server was still running. Safe to call before every read/write."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if not os.path.exists(self.path):
            open(self.path, "w", encoding="utf-8").close()

    def _last_hash(self) -> str:
        self._ensure_file()
        last = None
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if last is None:
            return GENESIS_HASH
        return json.loads(last)["entry_hash"]

    def record(self, event_type: str, payload: dict, request_id: str = None) -> dict:
        """
        event_type: e.g. 'catalog_query', 'purchase_intent', 'bound_check',
                    'razorpay_order_created', 'payment_declined',
                    'payment_retry', 'payment_succeeded', 'escalated_to_human'
        payload: JSON-serializable dict with whatever is relevant to this event.
                 Never put raw card numbers or secrets in here.
        request_id: groups every entry produced by one checkout() call, so the
                    audit trail can be displayed grouped by action instead of
                    as one undifferentiated stream. Included inside the hashed
                    content, so it's covered by tamper-evidence too.
        """
        prev_hash = self._last_hash()
        entry_core = {
            "ts": time.time(),
            "event_type": event_type,
            "payload": payload,
            "request_id": request_id,
            "prev_hash": prev_hash,
        }
        entry_json = json.dumps(entry_core, sort_keys=True)
        entry_hash = hashlib.sha256((prev_hash + entry_json).encode()).hexdigest()
        entry = {**entry_core, "entry_hash": entry_hash}
        self._ensure_file()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def read_all(self) -> list:
        self._ensure_file()
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """Returns (is_valid, error_message_or_None)."""
        entries = self.read_all()
        expected_prev = GENESIS_HASH
        for i, entry in enumerate(entries):
            if entry["prev_hash"] != expected_prev:
                return False, f"broken link before entry {i} ({entry['event_type']})"
            entry_core = {
                "ts": entry["ts"],
                "event_type": entry["event_type"],
                "payload": entry["payload"],
                "request_id": entry.get("request_id"),
                "prev_hash": entry["prev_hash"],
            }
            recomputed = hashlib.sha256(
                (entry["prev_hash"] + json.dumps(entry_core, sort_keys=True)).encode()
            ).hexdigest()
            if recomputed != entry["entry_hash"]:
                return False, f"entry {i} ({entry['event_type']}) hash does not match its content — tampered"
            expected_prev = entry["entry_hash"]
        return True, None