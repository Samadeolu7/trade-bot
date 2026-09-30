"""Browser notifications via Web Push. The browser's push service (Google,
Mozilla, Apple, Microsoft) delivers each alert to a service worker in the
app (frontend/public/sw.js), which shows it as a system notification, so it
arrives whether or not the app is open."""

import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.utils import timezone
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from alerts.models import PushKeys, PushSubscription

logger = logging.getLogger(__name__)

# how long a push service keeps trying to deliver to a device that's offline
TTL_SECONDS = 6 * 3600


def _keys() -> PushKeys:
    keys = PushKeys.objects.order_by("id").first()
    if keys is None:
        vapid = Vapid02()
        vapid.generate_keys()
        pem = vapid.private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        raw = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        keys = PushKeys.objects.create(private_pem=pem, public_key=base64.urlsafe_b64encode(raw).decode().rstrip("="))
    return keys


def public_key() -> str:
    return _keys().public_key


def send(user, title: str, body: str = "", url: str = "/alerts?tab=log", tag: str = "") -> int:
    """Pushes to every browser the person has turned notifications on for.
    Returns how many accepted it. Subscriptions the push service reports as
    gone (the person revoked permission or cleared the browser) are removed."""
    subscriptions = list(PushSubscription.objects.filter(user=user))
    if not subscriptions:
        return 0
    vapid = Vapid02.from_pem(_keys().private_pem.encode())
    claims = {"sub": f"mailto:{settings.PUSH_CONTACT_EMAIL}"}
    payload = json.dumps({"title": title[:200], "body": body[:1000], "url": url, "tag": tag})
    sent = 0
    for sub in subscriptions:
        try:
            webpush(
                {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload, vapid_private_key=vapid, vapid_claims=dict(claims), ttl=TTL_SECONDS, timeout=10,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                sub.delete()
            else:
                logger.warning("push to %s failed: %s", sub.endpoint[:60], exc)
            continue
        except Exception:
            logger.warning("push to %s failed", sub.endpoint[:60], exc_info=True)
            continue
        sub.last_success_at = timezone.now()
        sub.save(update_fields=["last_success_at"])
        sent += 1
    return sent
