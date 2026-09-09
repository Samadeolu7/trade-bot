import requests

FEAR_GREED_API_URL = "https://api.alternative.me/fng/"


def fetch_fear_greed_index(limit: int = 1, timeout: int = 10) -> list[dict]:
    """Crypto Fear & Greed Index (alternative.me) — public, free, no API key.
    Market-wide sentiment, not BTC-specific, updates once/day. Returns the
    `limit` most recent entries, newest first, each a dict with at least
    'value' (str, "0"-"100"), 'value_classification' (str, e.g. "Fear"), and
    'timestamp' (str, epoch seconds).

    Advisory context only — not wired into any strategy's entry/exit rule;
    see recommendation-system plan for why."""
    response = requests.get(FEAR_GREED_API_URL, params={"limit": limit}, timeout=timeout)
    response.raise_for_status()
    return response.json()["data"]
