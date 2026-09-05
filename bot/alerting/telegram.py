import logging

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramAlerter:
    """Thin wrapper around Telegram's sendMessage API (spec Section 9).
    Never raises — a failed alert should not crash the poll loop; it's
    logged instead. If bot_token/chat_id aren't set, messages are logged at
    WARNING and dropped rather than sent, so the bot is still usable (and
    testable) before Telegram is configured."""

    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            logger.warning("Telegram not configured — dropped alert:\n%s", text)
            return False
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url, data={"chat_id": self.chat_id, "text": text}, timeout=10
            )
            response.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("Failed to send Telegram alert")
            return False
