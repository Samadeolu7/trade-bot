"""Pushes live events to the browser. A page subscribes to the groups it
shows (an account, a market); the account groups check the user's grants
first, the same way the API does."""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from trading.events import SYSTEM_GROUP
from trading.permissions import can_view_account


class LiveConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.groups_joined: set[str] = set()
        await self.accept()
        await self._join(SYSTEM_GROUP)
        await self._join(f"user.{user.pk}")

    async def disconnect(self, code):
        for group in getattr(self, "groups_joined", set()):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        action, group = content.get("action"), str(content.get("group", ""))
        if action == "subscribe" and await self._allowed(group):
            await self._join(group)
            await self.send_json({"event": "subscribed", "group": group})
        elif action == "unsubscribe" and group in self.groups_joined:
            await self.channel_layer.group_discard(group, self.channel_name)
            self.groups_joined.discard(group)

    async def push(self, message):
        await self.send_json({"event": message["event"], "data": message["data"]})

    async def _join(self, group: str):
        await self.channel_layer.group_add(group, self.channel_name)
        self.groups_joined.add(group)

    async def _allowed(self, group: str) -> bool:
        if group.startswith("market."):
            return len(group) < 90
        if group.startswith("account."):
            try:
                account_id = int(group.split(".", 1)[1])
            except ValueError:
                return False
            return await database_sync_to_async(can_view_account)(self.scope["user"], account_id)
        return False
