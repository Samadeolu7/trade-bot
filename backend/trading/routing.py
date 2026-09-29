from django.urls import path

from trading.consumers import LiveConsumer

websocket_urlpatterns = [path("ws/live/", LiveConsumer.as_asgi())]
