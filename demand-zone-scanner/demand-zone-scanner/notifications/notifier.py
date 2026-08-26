"""
Telegram notifier.

format_alert_message() builds the message exactly to the spec's example
format (section 10) - the demand-zone-reached alert, with the BTC
condition warning appended per section 8 when conditions are weak.
TelegramNotifier.send_alert() sends it via the Bot API's sendMessage
endpoint and reports success/failure, but never marks the corresponding
zone as alerted itself - that's confirm_alert_sent()'s job (db/service.py),
called only after a confirmed successful send. Keeping "did we send it"
and "did we record that we sent it" as separate steps, in that order, is
what prevents an alert from being silently lost if the send fails.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from data.exceptions import DataProviderError
from data.http_client import post_with_retry
from db.models import ZoneRecord

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# a departure of 2x ATR or more counts as "strong" for the checklist
# display - matches the threshold used for full marks in scoring.py
STRONG_DEPARTURE_ATR_THRESHOLD = 2.0
VOLUME_EXPANSION_THRESHOLD = 1.5


def _check(condition: bool) -> str:
    return "✅" if condition else "❌"


def format_alert_message(zone: ZoneRecord) -> str:
    coin = zone.symbol.split("/")[0] if "/" in zone.symbol else zone.symbol
    quote = zone.symbol.split("/")[1] if "/" in zone.symbol else "USDT"

    fresh_ok = zone.freshness == "fresh"
    departure_ok = zone.departure_atr_multiple >= STRONG_DEPARTURE_ATR_THRESHOLD
    volume_ok = zone.volume_expansion_ratio >= VOLUME_EXPANSION_THRESHOLD
    d3_confluence = "3D" in zone.confluent_timeframes
    h4_confirmation = "4H" in zone.confluent_timeframes

    lines = [
        "🚨 DEMAND ZONE REACHED",
        "",
        f"🪙 {coin}/{quote}",
        "",
        f"Timeframe: {zone.timeframe}",
        "",
        "Demand Zone:",
        f"{zone.zone_low:.6g} – {zone.zone_high:.6g}",
        "",
        "Current Price:",
        f"{zone.current_price:.6g}",
        "",
        f"⭐ Score: {zone.score:.0f}/100 ({zone.grade})",
        "",
        f"Fresh Zone: {_check(fresh_ok)}",
        f"Strong Departure: {_check(departure_ok)}",
        f"Volume Expansion: {_check(volume_ok)}",
        f"BOS: {_check(zone.break_of_structure)}",
        f"Liquidity Sweep: {_check(zone.liquidity_sweep)}",
        f"3D Confluence: {_check(d3_confluence)}",
        f"4H Confirmation: {'✅' if h4_confirmation else '⏳'}",
        "",
        f"BTC Condition: {zone.btc_condition or 'Unknown'}",
    ]

    # spec section 8: a weak BTC condition must warn, never suppress or
    # auto-convert the alert into anything more than "confirmation required"
    if zone.btc_condition == "Weak":
        lines += ["", "⚠️ BTC Market Condition: Weak", "Demand Zone reached — confirmation required."]

    lines += [
        "",
        "STATUS:",
        "PRICE ENTERED DEMAND",
        "",
        "ACTION:",
        "CHECK CHART",
    ]

    tv_link = _tradingview_link(coin, quote)
    if tv_link:
        lines += ["", tv_link]

    return "\n".join(lines)


def _tradingview_link(coin: str, quote: str) -> Optional[str]:
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{coin}{quote}"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        if not bot_token or not chat_id:
            raise DataProviderError("Telegram bot_token and chat_id are both required")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def _send_message_endpoint(self) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"

    def send_text(self, text: str) -> bool:
        """Returns True on confirmed delivery, False on failure (logged, not raised -
        a single alert failing to send should not crash the whole scan)."""
        try:
            response = post_with_retry(
                self._send_message_endpoint(),
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout,
            )
        except DataProviderError as e:
            logger.error("Failed to send Telegram message: %s", e)
            return False

        payload = response.json()
        if not payload.get("ok"):
            logger.error("Telegram API rejected the message: %s", payload)
            return False
        return True

    def send_alert(self, zone: ZoneRecord) -> bool:
        return self.send_text(format_alert_message(zone))

    def get_updates(self, offset: Optional[int] = None, timeout: int = 0) -> List[dict]:
        """
        Polls Telegram for new incoming messages since `offset` (the
        update_id to start after - pass last_update_id + 1). Returns the
        raw list of Telegram "update" objects, or [] on any failure
        (logged, not raised - a polling hiccup shouldn't crash the run).
        """
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset

        try:
            response = post_with_retry(
                f"{TELEGRAM_API_BASE}/bot{self.bot_token}/getUpdates",
                json=params,
                timeout=self.timeout + timeout,
            )
        except DataProviderError as e:
            logger.error("Failed to poll Telegram for updates: %s", e)
            return []

        payload = response.json()
        if not payload.get("ok"):
            logger.error("Telegram getUpdates rejected: %s", payload)
            return []
        return payload.get("result", [])
