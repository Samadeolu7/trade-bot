from core.models import AuditEvent


def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def audit(action: str, *, request=None, user=None, actor_label: str = "", account=None, target: str = "", **data):
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    return AuditEvent.objects.create(
        action=action,
        actor=user,
        actor_label=actor_label,
        account=account,
        target=str(target),
        data=data,
        ip=client_ip(request),
    )
