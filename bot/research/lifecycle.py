import sqlite3

from bot.storage.db import get_state, query_state_prefix, set_state

STATE_PREFIX = "lifecycle:"

# A strategy's progress from idea to (eventually, if ever) something safe to
# automate. Stages are never advanced by backtest/sweep/shadow/recommend — a
# good holdout result does not self-promote a strategy; every transition is
# an explicit human call via `python main.py lifecycle set`.
STAGES = [
    "research",
    "backtested",
    "holdout_passed",
    "shadowing",
    "shadow_review",
    "approved",
    "automation_ready",
    "disabled",
    "retired",
]

DEFAULT_STAGE = "research"


def get_lifecycle_stage(conn: sqlite3.Connection, strategy_label: str) -> str:
    return get_state(conn, f"{STATE_PREFIX}{strategy_label}") or DEFAULT_STAGE


def set_lifecycle_stage(conn: sqlite3.Connection, strategy_label: str, stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"invalid lifecycle stage {stage!r}, must be one of {STAGES}")
    set_state(conn, f"{STATE_PREFIX}{strategy_label}", stage)


def list_lifecycle_stages(conn: sqlite3.Connection) -> dict[str, str]:
    raw = query_state_prefix(conn, STATE_PREFIX)
    return {key[len(STATE_PREFIX):]: value for key, value in raw.items()}


def is_automation_ready(conn: sqlite3.Connection, strategy_label: str) -> tuple[bool, list[str]]:
    """Pure scaffolding for a future Phase 5 automation gate — no caller
    anywhere in the codebase yet. A strategy can never self-promote to this;
    it only becomes true once a human has explicitly set the stage."""
    stage = get_lifecycle_stage(conn, strategy_label)
    if stage == "automation_ready":
        return True, []
    return False, [f"lifecycle stage is '{stage}', not 'automation_ready'"]
