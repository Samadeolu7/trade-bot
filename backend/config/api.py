from django.core.exceptions import PermissionDenied
from ninja import NinjaAPI
from ninja.security import django_auth

from alerts.api import router as alerts_router
from bot.broker.base import BrokerError
from core.api import audit_router, router as auth_router, users_router
from core.status import router as status_router
from recommendations.api import router as recommendations_router
from research.api import router as research_router
from trading.api import router as trading_router
from trading.services.orders import OrderError

# Session auth: the React app is served from the same origin, and
# django_auth enforces CSRF on every state-changing request.
api = NinjaAPI(title="Trade desk API", version="1.0.0", auth=django_auth, urls_namespace="api")

api.add_router("/auth", auth_router)
api.add_router("/public", status_router)
api.add_router("/users", users_router)
api.add_router("/audit", audit_router)
api.add_router("/research", research_router)
api.add_router("/alerts", alerts_router)
api.add_router("/recommendations", recommendations_router)
api.add_router("/", trading_router)


@api.exception_handler(OrderError)
def order_error(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=400)


@api.exception_handler(PermissionDenied)
def permission_denied(request, exc):
    return api.create_response(request, {"detail": str(exc) or "permission denied"}, status=403)


@api.exception_handler(BrokerError)
def broker_error(request, exc):
    return api.create_response(request, {"detail": f"venue unavailable: {exc}"}, status=502)
