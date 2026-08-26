"""
Tests for notifications/notifier.py: message formatting (real string
checks, no mocking needed) and send behavior (mocked HTTP, since this
sandbox has no network access to a real Telegram bot).

Run with: pytest tests/test_notifications.py -v
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.exceptions import DataProviderError, TransientProviderError
from db.models import TradedStatus, ZoneRecord
from notifications.notifier import TelegramNotifier, format_alert_message


def _make_record(**overrides) -> ZoneRecord:
    defaults = dict(
        id=1, symbol="DOT/USDT", name=None, timeframe="1D",
        zone_low=0.745, zone_high=0.765, creation_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        score=92.0, grade="A+", freshness="fresh", test_count=0,
        departure_pct=18.0, departure_atr_multiple=3.5, volume_expansion_ratio=2.8,
        break_of_structure=True, liquidity_sweep=True, fibonacci_confluence=0.5,
        current_price=0.762, zone_entered=True, status="ENTERED", btc_condition="Neutral",
        confluent_timeframes=["3D"], alert_sent=False, alert_time=None, result=None,
        traded_skipped=TradedStatus.UNDECIDED, updated_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ZoneRecord(**defaults)


class TestMessageFormatting:
    def test_matches_spec_example_shape(self):
        record = _make_record()
        msg = format_alert_message(record)

        assert "🚨 DEMAND ZONE REACHED" in msg
        assert "🪙 DOT/USDT" in msg
        assert "Timeframe: 1D" in msg
        assert "0.745" in msg and "0.765" in msg
        assert "0.762" in msg
        assert "Score: 92/100" in msg
        assert "PRICE ENTERED DEMAND" in msg
        assert "CHECK CHART" in msg

    def test_checkmarks_reflect_evidence(self):
        record = _make_record(
            departure_atr_multiple=3.5,  # >= threshold -> strong departure checked
            volume_expansion_ratio=2.8,  # >= threshold -> volume checked
            break_of_structure=True,
            liquidity_sweep=False,
        )
        msg = format_alert_message(record)
        lines = {line.split(":")[0].strip(): line for line in msg.split("\n") if ":" in line}

        assert "✅" in lines["Strong Departure"]
        assert "✅" in lines["Volume Expansion"]
        assert "✅" in lines["BOS"]
        assert "❌" in lines["Liquidity Sweep"]

    def test_weak_departure_and_volume_show_x(self):
        record = _make_record(departure_atr_multiple=0.5, volume_expansion_ratio=1.1)
        msg = format_alert_message(record)
        lines = {line.split(":")[0].strip(): line for line in msg.split("\n") if ":" in line}
        assert "❌" in lines["Strong Departure"]
        assert "❌" in lines["Volume Expansion"]

    def test_tested_zone_is_not_marked_fresh(self):
        record = _make_record(freshness="tested_once")
        msg = format_alert_message(record)
        lines = {line.split(":")[0].strip(): line for line in msg.split("\n") if ":" in line}
        assert "❌" in lines["Fresh Zone"]

    def test_3d_confluence_reflects_confluent_timeframes(self):
        with_3d = format_alert_message(_make_record(confluent_timeframes=["3D", "1D"]))
        without_3d = format_alert_message(_make_record(confluent_timeframes=["1D"]))

        d3_line_with = [l for l in with_3d.split("\n") if "3D Confluence" in l][0]
        d3_line_without = [l for l in without_3d.split("\n") if "3D Confluence" in l][0]
        assert "✅" in d3_line_with
        assert "✅" not in d3_line_without

    def test_4h_confirmation_shows_pending_when_absent(self):
        msg = format_alert_message(_make_record(confluent_timeframes=["3D", "1D"]))
        h4_line = [l for l in msg.split("\n") if "4H Confirmation" in l][0]
        assert "⏳" in h4_line

    def test_weak_btc_condition_adds_warning_block(self):
        msg = format_alert_message(_make_record(btc_condition="Weak"))
        assert "⚠️ BTC Market Condition: Weak" in msg
        assert "confirmation required" in msg.lower()

    def test_neutral_or_strong_btc_condition_has_no_warning_block(self):
        for condition in ("Neutral", "Strong"):
            msg = format_alert_message(_make_record(btc_condition=condition))
            assert "⚠️" not in msg

    def test_includes_tradingview_link(self):
        msg = format_alert_message(_make_record(symbol="BTC/USDT"))
        assert "tradingview.com" in msg
        assert "BINANCE:BTCUSDT" in msg

    def test_never_alerts_on_a_weak_condition_by_suppressing_it(self):
        # spec section 8: weak BTC must warn, never silently suppress the
        # alert itself - the message must still contain the core alert
        msg = format_alert_message(_make_record(btc_condition="Weak"))
        assert "DEMAND ZONE REACHED" in msg
        assert "PRICE ENTERED DEMAND" in msg


class TestTelegramNotifier:
    def test_requires_both_token_and_chat_id(self):
        try:
            TelegramNotifier(bot_token="", chat_id="123")
            assert False, "expected error for missing token"
        except DataProviderError:
            pass
        try:
            TelegramNotifier(bot_token="abc", chat_id="")
            assert False, "expected error for missing chat_id"
        except DataProviderError:
            pass

    @patch("notifications.notifier.post_with_retry")
    def test_send_text_success(self, mock_post):
        resp = MagicMock()
        resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
        mock_post.return_value = resp

        notifier = TelegramNotifier(bot_token="fake-token", chat_id="12345")
        assert notifier.send_text("hello") is True

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["chat_id"] == "12345"
        assert kwargs["json"]["text"] == "hello"

    @patch("notifications.notifier.post_with_retry")
    def test_send_text_telegram_rejects_message(self, mock_post):
        resp = MagicMock()
        resp.json.return_value = {"ok": False, "description": "chat not found"}
        mock_post.return_value = resp

        notifier = TelegramNotifier(bot_token="fake-token", chat_id="wrong-chat")
        assert notifier.send_text("hello") is False

    @patch("notifications.notifier.post_with_retry")
    def test_send_text_network_failure_returns_false_not_raises(self, mock_post):
        mock_post.side_effect = TransientProviderError("network down")
        notifier = TelegramNotifier(bot_token="fake-token", chat_id="12345")
        # must not raise - a failed alert shouldn't crash the whole scan
        assert notifier.send_text("hello") is False

    @patch("notifications.notifier.post_with_retry")
    def test_send_alert_uses_formatted_message(self, mock_post):
        resp = MagicMock()
        resp.json.return_value = {"ok": True}
        mock_post.return_value = resp

        notifier = TelegramNotifier(bot_token="fake-token", chat_id="12345")
        record = _make_record()
        notifier.send_alert(record)

        _, kwargs = mock_post.call_args
        assert "DEMAND ZONE REACHED" in kwargs["json"]["text"]
        assert record.symbol.split("/")[0] in kwargs["json"]["text"]


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
