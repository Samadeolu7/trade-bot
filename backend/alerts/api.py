from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from alerts import push
from alerts.models import AlertEvent, AlertRule, PushSubscription
from alerts.service import add_default_rules, chat_for, deliver
from bot.broker.venues import VENUES
from core.audit import audit
from trading.models import Bot
from trading.permissions import visible_accounts

router = Router(tags=["alerts"])


class RuleOut(Schema):
    id: int
    kind: str
    kind_label: str
    params: dict
    account_id: int | None
    bot_id: int | None
    bot_name: str | None
    note: str
    enabled: bool
    once: bool
    cooldown_minutes: int
    last_fired_at: datetime | None


def _rule_out(r: AlertRule) -> dict:
    return {
        "id": r.pk, "kind": r.kind, "kind_label": r.get_kind_display(), "params": r.params,
        "account_id": r.account_id, "bot_id": r.bot_id, "bot_name": r.bot.name if r.bot else None,
        "note": r.note, "enabled": r.enabled, "once": r.once, "cooldown_minutes": r.cooldown_minutes,
        "last_fired_at": r.last_fired_at,
    }


class RuleIn(Schema):
    kind: str
    params: dict = {}
    account_id: int | None = None
    bot_id: int | None = None
    note: str = ""
    enabled: bool = True
    once: bool = False
    cooldown_minutes: int = 0


def _clean(request, payload: RuleIn) -> dict:
    if payload.kind not in AlertRule.Kind.values:
        raise HttpError(400, "unknown alert kind")
    params = dict(payload.params)
    kind = payload.kind
    if kind in (AlertRule.Kind.PRICE_ABOVE, AlertRule.Kind.PRICE_BELOW, AlertRule.Kind.PRICE_MOVE):
        params.setdefault("venue", "quidax_spot")
        params.setdefault("symbol", "BTC/USDT")
        if params["venue"] not in VENUES:
            raise HttpError(400, "unknown venue")
        try:
            if kind == AlertRule.Kind.PRICE_MOVE:
                if not 0 < float(params["pct"]) <= 50 or not 1 <= float(params.get("minutes", 60)) <= 1440:
                    raise HttpError(400, "a sudden move needs 0–50% within 1–1440 minutes")
                params["minutes"] = float(params.get("minutes", 60))
            elif Decimal(str(params["price"])) <= 0:
                raise HttpError(400, "the price must be positive")
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise HttpError(400, "price alerts need a price; sudden-move alerts need pct and minutes") from exc
    if kind == AlertRule.Kind.DAILY_SUMMARY:
        hour = int(params.get("hour", 7))
        if not 0 <= hour <= 23:
            raise HttpError(400, "hour must be 0–23 (UTC)")
        params = {"hour": hour}
    account = None
    if payload.account_id is not None:
        account = visible_accounts(request.user).filter(pk=payload.account_id).first()
        if account is None:
            raise HttpError(403, "no access to that account")
    bot = None
    if payload.bot_id is not None:
        bot = Bot.objects.filter(pk=payload.bot_id, account__in=visible_accounts(request.user)).first()
        if bot is None:
            raise HttpError(403, "no access to that bot")
    return {
        "kind": kind, "params": params, "account": account, "bot": bot, "note": payload.note[:200],
        "enabled": payload.enabled, "once": payload.once, "cooldown_minutes": max(0, payload.cooldown_minutes),
    }


@router.get("/rules", response=list[RuleOut])
def list_rules(request):
    return [_rule_out(r) for r in AlertRule.objects.filter(user=request.user).select_related("bot")]


@router.post("/rules", response=RuleOut)
def create_rule(request, payload: RuleIn):
    rule = AlertRule.objects.create(user=request.user, **_clean(request, payload))
    audit("alert.created", request=request, target=f"alert:{rule.pk}", kind=rule.kind, params=rule.params)
    return _rule_out(rule)


@router.put("/rules/{rule_id}", response=RuleOut)
def update_rule(request, rule_id: int, payload: RuleIn):
    rule = AlertRule.objects.filter(pk=rule_id, user=request.user).first()
    if rule is None:
        raise HttpError(404, "no such alert")
    for field, value in _clean(request, payload).items():
        setattr(rule, field, value)
    rule.state = {}
    rule.save()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(request, rule_id: int):
    deleted, _ = AlertRule.objects.filter(pk=rule_id, user=request.user).delete()
    if not deleted:
        raise HttpError(404, "no such alert")
    return {"ok": True}


@router.post("/defaults", response=list[RuleOut])
def defaults(request):
    add_default_rules(request.user)
    return list_rules(request)


class EventOut(Schema):
    id: int
    kind: str
    title: str
    body: str
    delivered: bool
    dismissed_at: datetime | None
    created_at: datetime


@router.get("/events", response=list[EventOut])
def list_events(request, limit: int = 100, active: bool = False):
    """`active`: only alerts not yet dismissed, the ones the app keeps on screen."""
    events = AlertEvent.objects.filter(user=request.user)
    if active:
        events = events.filter(dismissed_at__isnull=True)
    return list(events[: min(limit, 500)])


@router.post("/events/{event_id}/dismiss", response=EventOut)
def dismiss(request, event_id: int):
    event = AlertEvent.objects.filter(pk=event_id, user=request.user).first()
    if event is None:
        raise HttpError(404, "no such alert")
    if event.dismissed_at is None:
        event.dismissed_at = timezone.now()
        event.save(update_fields=["dismissed_at"])
    return event


@router.post("/events/dismiss-all")
def dismiss_all(request):
    count = AlertEvent.objects.filter(user=request.user, dismissed_at__isnull=True).update(dismissed_at=timezone.now())
    return {"dismissed": count}


class TelegramOut(Schema):
    chat_id: str
    uses_default_chat: bool
    bot_configured: bool


def _telegram_out(user) -> dict:
    return {
        "chat_id": user.telegram_chat_id,
        "uses_default_chat": not user.telegram_chat_id and bool(chat_for(user)),
        "bot_configured": bool(settings.TELEGRAM_BOT_TOKEN),
    }


@router.get("/telegram", response=TelegramOut)
def get_telegram(request):
    return _telegram_out(request.user)


class TelegramIn(Schema):
    chat_id: str


@router.put("/telegram", response=TelegramOut)
def set_telegram(request, payload: TelegramIn):
    chat_id = payload.chat_id.strip()
    if chat_id and not chat_id.lstrip("-").isdigit():
        raise HttpError(400, "a Telegram chat ID is a number, like 123456789 or -1001234567890")
    request.user.telegram_chat_id = chat_id
    request.user.save(update_fields=["telegram_chat_id"])
    audit("alert.telegram", request=request, target=f"user:{request.user.pk}")
    return _telegram_out(request.user)


@router.post("/telegram/test")
def test_telegram(request):
    if not chat_for(request.user):
        raise HttpError(400, "set your Telegram chat ID first")
    if not deliver(request.user, "Test alert from the trade desk", "If you can read this, alerts will reach you here."):
        raise HttpError(502, "Telegram didn't accept the message; check the chat ID and that you've messaged the bot")
    return {"ok": True}


# --- browser notifications (Web Push) ------------------------------------------------


class PushKeyOut(Schema):
    public_key: str
    devices: int


@router.get("/push", response=PushKeyOut)
def push_status(request):
    return {"public_key": push.public_key(),
            "devices": PushSubscription.objects.filter(user=request.user).count()}


class PushSubscriptionIn(Schema):
    endpoint: str
    p256dh: str
    auth: str


@router.post("/push/subscribe", response=PushKeyOut)
def push_subscribe(request, payload: PushSubscriptionIn):
    if not payload.endpoint.startswith("https://"):
        raise HttpError(400, "not a push endpoint")
    PushSubscription.objects.update_or_create(
        endpoint=payload.endpoint,
        defaults={"user": request.user, "p256dh": payload.p256dh, "auth": payload.auth,
                  "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]},
    )
    audit("alert.push_on", request=request, target=f"user:{request.user.pk}")
    return push_status(request)


class EndpointIn(Schema):
    endpoint: str


@router.post("/push/unsubscribe", response=PushKeyOut)
def push_unsubscribe(request, payload: EndpointIn):
    PushSubscription.objects.filter(user=request.user, endpoint=payload.endpoint).delete()
    return push_status(request)


@router.post("/push/test")
def push_test(request):
    if not push.send(request.user, "Test notification from the trade desk",
                     "If you can see this, alerts will reach this device even when the app isn't open."):
        raise HttpError(400, "no browser accepted it; turn notifications on for this browser first")
    return {"ok": True}

