"""Who may see and do what. Checked by every API endpoint and websocket
subscription; the frontend hiding a button is never the protection."""

from django.conf import settings
from django.core.exceptions import PermissionDenied

from trading.models import AccountGrant, TradingAccount


def visible_accounts(user):
    if not user.is_authenticated:
        return TradingAccount.objects.none()
    if user.is_owner:
        return TradingAccount.objects.all()
    return TradingAccount.objects.filter(grants__user=user).distinct()


def can_view_account(user, account_id: int) -> bool:
    return visible_accounts(user).filter(pk=account_id).exists()


def needs_2fa(user) -> bool:
    """Owners and traders must use an authenticator app, unless disabled
    for local development."""
    return settings.REQUIRE_2FA and user.role in ("owner", "trader")


def require_verified(request) -> None:
    user = request.user
    if needs_2fa(user) and not getattr(user, "is_verified", lambda: False)():
        raise PermissionDenied("two-factor authentication is required for this action")


def can_trade_account(user, account: TradingAccount) -> bool:
    if user.is_owner:
        return True
    if user.role != "trader":
        return False
    return AccountGrant.objects.filter(user=user, account=account, role=AccountGrant.Role.TRADER).exists()


def require_view(request, account_id: int) -> TradingAccount:
    account = visible_accounts(request.user).filter(pk=account_id).first()
    if account is None:
        raise PermissionDenied("no access to this account")
    return account


def require_trade(request, account: TradingAccount) -> None:
    if not can_trade_account(request.user, account):
        raise PermissionDenied("you can view this account but not trade it")
    require_verified(request)


def require_owner(request) -> None:
    if not request.user.is_owner:
        raise PermissionDenied("only the owner can do this")
    require_verified(request)
