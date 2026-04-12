"""
Lightweight JSON-backed state store.

Tracks:
  - which event reminders have already been sent  (event_reminders dict)
  - the last reimbursement sheet row we processed  (last_reimbursement_row int)
"""

import json
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_DEFAULT_STATE: dict = {
    "event_reminders": {},   # key: "<event_id>|<days_before>", value: ISO timestamp sent
    "last_reimbursement_row": 1,  # 1-based row index; row 1 is the header
}


def _load() -> dict:
    path = Path(config.STATE_FILE)
    if not path.exists():
        return dict(_DEFAULT_STATE)
    try:
        with path.open() as f:
            data = json.load(f)
        # Merge any missing keys from the default so old state files stay compatible
        for k, v in _DEFAULT_STATE.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load state file (%s); starting fresh: %s", path, exc)
        return dict(_DEFAULT_STATE)


def _save(state: dict) -> None:
    path = Path(config.STATE_FILE)
    try:
        with path.open("w") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        logger.error("Could not save state file: %s", exc)


# ── Public API ────────────────────────────────────────────────

def reminder_already_sent(event_id: str, days_before: int) -> bool:
    state = _load()
    key = f"{event_id}|{days_before}"
    return key in state["event_reminders"]


def mark_reminder_sent(event_id: str, days_before: int, sent_at: str) -> None:
    state = _load()
    key = f"{event_id}|{days_before}"
    state["event_reminders"][key] = sent_at
    _save(state)


def get_last_reimbursement_row() -> int:
    return _load()["last_reimbursement_row"]


def set_last_reimbursement_row(row: int) -> None:
    state = _load()
    state["last_reimbursement_row"] = row
    _save(state)
