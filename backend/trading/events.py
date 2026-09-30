"""Live updates to the browser. The engine and API publish here after a
change commits; the websocket consumer forwards events to subscribed
pages. Publishing never raises: a missed push only delays the page's
next refresh."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)


def account_group(account_id: int) -> str:
    return f"account.{account_id}"


def market_group(venue: str, symbol: str) -> str:
    return f"market.{venue}.{symbol.replace('/', '-')}"


SYSTEM_GROUP = "system"


def _send(group: str, event: str, data: dict) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group, {"type": "push", "event": event, "data": data})
    except Exception:
        logger.warning("could not publish %s to %s", event, group, exc_info=True)


def publish(group: str, event: str, data: dict | None = None) -> None:
    transaction.on_commit(lambda: _send(group, event, data or {}))
