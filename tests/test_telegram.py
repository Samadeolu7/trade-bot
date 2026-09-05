from unittest.mock import MagicMock, patch

import requests

from bot.alerting.telegram import TelegramAlerter


def test_not_enabled_when_missing_credentials():
    assert TelegramAlerter(None, None).enabled is False
    assert TelegramAlerter("token", None).enabled is False
    assert TelegramAlerter(None, "chat").enabled is False


def test_enabled_when_both_present():
    assert TelegramAlerter("token", "chat").enabled is True


def test_send_drops_message_when_not_configured():
    alerter = TelegramAlerter(None, None)
    assert alerter.send("hello") is False


@patch("bot.alerting.telegram.requests.post")
def test_send_posts_to_telegram_api_when_configured(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    alerter = TelegramAlerter("test-token", "12345")

    result = alerter.send("hello world")

    assert result is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["data"]["chat_id"] == "12345"
    assert kwargs["data"]["text"] == "hello world"


@patch("bot.alerting.telegram.requests.post")
def test_send_returns_false_and_does_not_raise_on_request_failure(mock_post):
    mock_post.side_effect = requests.RequestException("network error")
    alerter = TelegramAlerter("test-token", "12345")

    assert alerter.send("hello") is False
