from datetime import datetime

import qrcode
import qrcode.image.svg
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from ninja import Router, Schema
from ninja.errors import HttpError

from core.audit import audit, client_ip
from core.models import AuditEvent, User
from trading.models import AccountGrant, TradingAccount
from trading.permissions import needs_2fa, require_owner, visible_accounts

router = Router(tags=["auth"])
users_router = Router(tags=["users"])

LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60


class MeOut(Schema):
    id: int
    username: str
    email: str
    role: str
    two_factor_enabled: bool
    two_factor_verified: bool
    # an owner/trader who hasn't set up 2FA yet can only view until they do
    needs_2fa_setup: bool


def _confirmed_device(user) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def me_payload(request) -> dict:
    user = request.user
    enabled = _confirmed_device(user) is not None
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "two_factor_enabled": enabled,
        "two_factor_verified": bool(getattr(user, "is_verified", lambda: False)()),
        "needs_2fa_setup": needs_2fa(user) and not enabled,
    }


@router.get("/csrf", auth=None)
def csrf(request):
    """Sets the CSRF cookie; the frontend calls this once on load."""
    return {"csrf": get_token(request)}


class LoginIn(Schema):
    username: str
    password: str
    otp: str | None = None


@router.post("/login", auth=None, response=MeOut)
def login_view(request, payload: LoginIn):
    key = f"login-fail:{client_ip(request)}:{payload.username.lower()}"
    if cache.get(key, 0) >= LOGIN_ATTEMPTS:
        raise HttpError(429, "too many failed attempts; try again in 15 minutes")
    user = authenticate(request, username=payload.username, password=payload.password)
    device = _confirmed_device(user) if user else None
    if user is not None and device is not None:
        if not payload.otp:
            raise HttpError(401, "otp_required")
        if not device.verify_token(payload.otp.replace(" ", "")):
            user = None
    if user is None:
        cache.set(key, cache.get(key, 0) + 1, LOGIN_WINDOW_SECONDS)
        audit("auth.login_failed", request=request, target=payload.username[:100])
        raise HttpError(401, "invalid credentials")
    cache.delete(key)
    login(request, user)
    if device is not None:
        otp_login(request, device)
    audit("auth.login", request=request, user=user, target=f"user:{user.pk}")
    return me_payload(request)


@router.post("/logout")
def logout_view(request):
    audit("auth.logout", request=request)
    logout(request)
    return {"ok": True}


@router.get("/me", response=MeOut)
def me(request):
    return me_payload(request)


class TwoFactorSetupOut(Schema):
    otpauth_uri: str
    qr_svg: str


@router.post("/2fa/setup", response=TwoFactorSetupOut)
def two_factor_setup(request):
    user = request.user
    if _confirmed_device(user) is not None:
        raise HttpError(400, "two-factor authentication is already set up")
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    device = TOTPDevice.objects.create(user=user, name="authenticator", confirmed=False)
    image = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage)
    return {"otpauth_uri": device.config_url, "qr_svg": image.to_string(encoding="unicode")}


class TokenIn(Schema):
    token: str


@router.post("/2fa/confirm", response=MeOut)
def two_factor_confirm(request, payload: TokenIn):
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if device is None or not device.verify_token(payload.token.replace(" ", "")):
        raise HttpError(400, "that code didn't match; check the time on your phone and try again")
    device.confirmed = True
    device.save()
    otp_login(request, device)
    audit("auth.2fa_enabled", request=request, target=f"user:{request.user.pk}")
    return me_payload(request)


class PasswordIn(Schema):
    current_password: str
    new_password: str


@router.post("/password")
def change_password(request, payload: PasswordIn):
    user = request.user
    if not user.check_password(payload.current_password):
        raise HttpError(400, "current password is wrong")
    try:
        validate_password(payload.new_password, user)
    except ValidationError as exc:
        raise HttpError(400, " ".join(exc.messages)) from exc
    user.set_password(payload.new_password)
    user.save()
    update_session_auth_hash(request, user)
    audit("auth.password_changed", request=request, target=f"user:{user.pk}")
    return {"ok": True}


# --- users, grants, audit (owner) -------------------------------------------------


class GrantOut(Schema):
    account_id: int
    account_name: str
    role: str


class UserOut(Schema):
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    two_factor_enabled: bool
    last_login: datetime | None
    grants: list[GrantOut]


def _user_out(user: User) -> dict:
    return {
        "id": user.pk,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "two_factor_enabled": _confirmed_device(user) is not None,
        "last_login": user.last_login,
        "grants": [
            {"account_id": g.account_id, "account_name": g.account.name, "role": g.role}
            for g in user.grants.select_related("account")
        ],
    }


@users_router.get("", response=list[UserOut])
def list_users(request):
    require_owner(request)
    return [_user_out(u) for u in User.objects.order_by("username")]


class UserIn(Schema):
    username: str
    email: str = ""
    role: str = "viewer"
    password: str


@users_router.post("", response=UserOut)
def create_user(request, payload: UserIn):
    require_owner(request)
    if payload.role not in User.Role.values:
        raise HttpError(400, "unknown role")
    if User.objects.filter(username=payload.username).exists():
        raise HttpError(400, "that username is taken")
    user = User(username=payload.username, email=payload.email, role=payload.role)
    try:
        validate_password(payload.password, user)
    except ValidationError as exc:
        raise HttpError(400, " ".join(exc.messages)) from exc
    user.set_password(payload.password)
    user.save()
    audit("user.created", request=request, target=f"user:{user.pk}", role=user.role)
    return _user_out(user)


class UserPatch(Schema):
    role: str | None = None
    is_active: bool | None = None
    reset_2fa: bool = False


@users_router.patch("/{user_id}", response=UserOut)
def update_user(request, user_id: int, payload: UserPatch):
    require_owner(request)
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise HttpError(404, "no such user")
    if user == request.user and (payload.role not in (None, "owner") or payload.is_active is False):
        raise HttpError(400, "you can't demote or disable yourself")
    if payload.role is not None:
        if payload.role not in User.Role.values:
            raise HttpError(400, "unknown role")
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    user.save()
    if payload.reset_2fa:
        TOTPDevice.objects.filter(user=user).delete()
    audit("user.updated", request=request, target=f"user:{user.pk}", role=user.role,
          is_active=user.is_active, reset_2fa=payload.reset_2fa)
    return _user_out(user)


class GrantIn(Schema):
    account_id: int
    role: str


@users_router.put("/{user_id}/grants", response=UserOut)
def set_grants(request, user_id: int, payload: list[GrantIn]):
    """Replaces the user's grants with exactly this list."""
    require_owner(request)
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise HttpError(404, "no such user")
    with transaction.atomic():
        AccountGrant.objects.filter(user=user).delete()
        for grant in payload:
            if grant.role not in AccountGrant.Role.values:
                raise HttpError(400, f"unknown grant role {grant.role!r}")
            account = TradingAccount.objects.filter(pk=grant.account_id).first()
            if account is None:
                raise HttpError(400, f"no account {grant.account_id}")
            AccountGrant.objects.create(user=user, account=account, role=grant.role)
    audit("user.grants", request=request, target=f"user:{user.pk}",
          grants=[{"account": g.account_id, "role": g.role} for g in payload])
    return _user_out(user)


class AuditOut(Schema):
    id: int
    created_at: datetime
    actor: str
    action: str
    account_id: int | None
    target: str
    data: dict
    ip: str | None


audit_router = Router(tags=["audit"])


@audit_router.get("", response=list[AuditOut])
def list_audit(request, account_id: int | None = None, action: str | None = None,
               before_id: int | None = None, limit: int = 100):
    events = AuditEvent.objects.select_related("actor").order_by("-id")
    if not request.user.is_owner:
        # non-owners see activity on the accounts they can see, never logins or user changes
        events = events.filter(account__in=visible_accounts(request.user))
    if account_id is not None:
        events = events.filter(account_id=account_id)
    if action:
        events = events.filter(action__startswith=action)
    if before_id:
        events = events.filter(id__lt=before_id)
    return [
        {
            "id": e.pk, "created_at": e.created_at,
            "actor": e.actor.username if e.actor else (e.actor_label or "system"),
            "action": e.action, "account_id": e.account_id, "target": e.target, "data": e.data,
            "ip": e.ip if request.user.is_owner else None,
        }
        for e in events[: min(limit, 500)]
    ]
