"""
Data pipeline tests using mocked HTTP responses via unittest.mock. This
sandbox has no network egress, so these tests validate parsing, filtering,
retry/backoff, and error-handling logic against realistic fixture
payloads rather than live API calls. When you have a real CMC key, a
quick live smoke test (see scripts/verify_cmc_key.py) confirms the actual
connection separately from this logic.

Run with: pytest tests/test_data_pipeline.py -v
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.binance import BinanceClient
from data.coingecko import CoinGeckoClient
from data.coinmarketcap import CoinMarketCapClient
from data.exceptions import (
    AuthenticationError,
    DataProviderError,
    RateLimitError,
    SymbolNotFoundError,
    TransientProviderError,
)
from data.universe_service import UniverseService


def _cmc_entry(symbol, cmc_id, rank, market_cap, volume, pct_change_24h, tags=None):
    return {
        "id": cmc_id,
        "name": f"{symbol} Coin",
        "symbol": symbol,
        "cmc_rank": rank,
        "tags": tags or [],
        "quote": {
            "USD": {
                "market_cap": market_cap,
                "volume_24h": volume,
                "percent_change_24h": pct_change_24h,
            }
        },
    }


def _mock_response(json_data, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.headers = headers or {}
    resp.raise_for_status.side_effect = None
    return resp


class TestCoinMarketCapClient:
    def test_requires_api_key(self):
        try:
            CoinMarketCapClient(api_key="")
            assert False, "expected error for empty API key"
        except DataProviderError:
            pass

    @patch("data.coinmarketcap.get_with_retry")
    def test_parses_listings_response(self, mock_get):
        mock_get.return_value = _mock_response({
            "data": [
                _cmc_entry("BTC", 1, 1, 1_200_000_000_000, 30_000_000_000, 2.5),
                _cmc_entry("ETH", 1027, 2, 400_000_000_000, 15_000_000_000, -1.2),
            ]
        })
        client = CoinMarketCapClient(api_key="fake-key")
        coins = client.get_top_n(100)

        assert len(coins) == 2
        assert coins[0].symbol == "BTC"
        assert coins[0].market_cap_rank == 1
        assert coins[0].market_cap_usd == 1_200_000_000_000
        assert coins[1].symbol == "ETH"

    @patch("data.coinmarketcap.get_with_retry")
    def test_sends_api_key_in_header(self, mock_get):
        mock_get.return_value = _mock_response({"data": []})
        client = CoinMarketCapClient(api_key="my-secret-key")
        client.get_top_n(100)

        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["X-CMC_PRO_API_KEY"] == "my-secret-key"

    @patch("data.coinmarketcap.get_with_retry")
    def test_excludes_low_volatility_stablecoins(self, mock_get):
        mock_get.return_value = _mock_response({
            "data": [
                _cmc_entry("USDT", 825, 3, 100_000_000_000, 50_000_000_000, 0.02, tags=["stablecoin"]),
                _cmc_entry("BTC", 1, 1, 1_200_000_000_000, 30_000_000_000, 2.5),
            ]
        })
        client = CoinMarketCapClient(api_key="fake-key")
        coins = client.get_top_n(100, stablecoin_volatility_threshold_pct=1.0)

        symbols = {c.symbol for c in coins}
        assert "USDT" not in symbols, "near-zero-move stablecoin should be filtered out"
        assert "BTC" in symbols

    @patch("data.coinmarketcap.get_with_retry")
    def test_keeps_volatile_stablecoin(self, mock_get):
        # a de-pegging event should NOT be filtered - that's exactly the
        # "meaningful price opportunity" case the spec says to keep
        mock_get.return_value = _mock_response({
            "data": [
                _cmc_entry("USDC", 3408, 5, 30_000_000_000, 4_000_000_000, -4.5, tags=["stablecoin"]),
            ]
        })
        client = CoinMarketCapClient(api_key="fake-key")
        coins = client.get_top_n(100, stablecoin_volatility_threshold_pct=1.0)
        assert len(coins) == 1
        assert coins[0].symbol == "USDC"

    @patch("data.coinmarketcap.get_with_retry")
    def test_skips_malformed_entry_without_crashing(self, mock_get):
        malformed = {"symbol": "BROKEN", "id": 999}  # missing 'quote' entirely
        good = _cmc_entry("BTC", 1, 1, 1_200_000_000_000, 30_000_000_000, 2.5)
        mock_get.return_value = _mock_response({"data": [malformed, good]})

        client = CoinMarketCapClient(api_key="fake-key")
        coins = client.get_top_n(100)
        assert len(coins) == 1
        assert coins[0].symbol == "BTC"

    @patch("data.coinmarketcap.get_with_retry")
    def test_missing_data_key_raises(self, mock_get):
        mock_get.return_value = _mock_response({"status": "ok"})  # no 'data' key
        client = CoinMarketCapClient(api_key="fake-key")
        try:
            client.get_top_n(100)
            assert False, "expected DataProviderError"
        except DataProviderError:
            pass


class TestBinanceClient:
    @patch("data.binance.get_with_retry")
    def test_parses_klines_into_expected_dataframe_shape(self, mock_get):
        raw_klines = [
            [1700000000000, "100.0", "105.0", "99.0", "103.0", "1500.5", 1700003600000, "0", 0, "0", "0", "0"],
            [1700003600000, "103.0", "108.0", "102.0", "107.0", "2000.0", 1700007200000, "0", 0, "0", "0", "0"],
        ]
        mock_get.return_value = _mock_response(raw_klines)

        client = BinanceClient()
        df = client.get_klines("BTC", "4H", limit=2)

        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert len(df) == 2
        assert df["open"].iloc[0] == 100.0
        assert df["close"].iloc[1] == 107.0
        assert df["timestamp"].is_monotonic_increasing

    @patch("data.binance.get_with_retry")
    def test_rejects_unsupported_timeframe(self, mock_get):
        client = BinanceClient()
        try:
            client.get_klines("BTC", "1W")
            assert False, "expected DataProviderError for unsupported timeframe"
        except DataProviderError:
            pass
        mock_get.assert_not_called()

    @patch("data.binance.get_with_retry")
    def test_invalid_symbol_raises_symbol_not_found(self, mock_get):
        mock_get.return_value = _mock_response({"code": -1121, "msg": "Invalid symbol."})
        client = BinanceClient()
        try:
            client.get_klines("NOTREAL", "1D")
            assert False, "expected SymbolNotFoundError"
        except SymbolNotFoundError:
            pass

    @patch("data.binance.get_with_retry")
    def test_empty_response_raises(self, mock_get):
        mock_get.return_value = _mock_response([])
        client = BinanceClient()
        try:
            client.get_klines("BTC", "1D")
            assert False, "expected DataProviderError for empty candle list"
        except DataProviderError:
            pass

    @patch("data.binance.get_with_retry")
    def test_tradable_symbols_cached_across_calls(self, mock_get):
        mock_get.return_value = _mock_response({
            "symbols": [
                {"baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
                {"baseAsset": "ETH", "quoteAsset": "USDT", "status": "TRADING", "isSpotTradingAllowed": True},
                {"baseAsset": "DELISTED", "quoteAsset": "USDT", "status": "BREAK", "isSpotTradingAllowed": True},
            ]
        })
        client = BinanceClient()
        first = client.get_tradable_spot_symbols()
        second = client.get_tradable_spot_symbols()

        assert first == {"BTC", "ETH"}
        assert second == first
        assert mock_get.call_count == 1, "second call should hit the cache, not re-fetch"

    @patch("data.binance.get_with_retry")
    def test_universe_batch_skips_failures_without_aborting(self, mock_get):
        good_klines = [
            [1700000000000, "1.0", "1.1", "0.9", "1.05", "500", 1700003600000, "0", 0, "0", "0", "0"],
        ]

        def side_effect(url, params=None, timeout=None, **kwargs):
            if params["symbol"] == "GOODUSDT":
                return _mock_response(good_klines)
            return _mock_response({"code": -1121, "msg": "Invalid symbol."})

        mock_get.side_effect = side_effect
        client = BinanceClient()
        results = client.get_klines_for_universe(["GOOD", "BAD"], "1D")

        assert "GOOD" in results
        assert "BAD" not in results
        assert len(results) == 1


class TestHttpClientRetryBehavior:
    @patch("data.http_client.requests.get")
    @patch("data.http_client.time.sleep", return_value=None)  # skip real waiting in tests
    def test_retries_on_500_then_succeeds(self, mock_sleep, mock_requests_get):
        from data.http_client import get_with_retry

        fail_resp = MagicMock(status_code=500)
        ok_resp = _mock_response({"ok": True})
        mock_requests_get.side_effect = [fail_resp, fail_resp, ok_resp]

        response = get_with_retry("https://example.com/api", max_attempts=4)
        assert response.json() == {"ok": True}
        assert mock_requests_get.call_count == 3

    @patch("data.http_client.requests.get")
    @patch("data.http_client.time.sleep", return_value=None)
    def test_exhausts_retries_and_raises_transient_error(self, mock_sleep, mock_requests_get):
        from data.http_client import get_with_retry

        mock_requests_get.return_value = MagicMock(status_code=503)
        try:
            get_with_retry("https://example.com/api", max_attempts=3)
            assert False, "expected TransientProviderError"
        except TransientProviderError:
            pass
        assert mock_requests_get.call_count == 3

    @patch("data.http_client.requests.get")
    def test_401_raises_authentication_error_without_retry(self, mock_requests_get):
        from data.http_client import get_with_retry

        mock_requests_get.return_value = MagicMock(status_code=401)
        try:
            get_with_retry("https://example.com/api", max_attempts=4)
            assert False, "expected AuthenticationError"
        except AuthenticationError:
            pass
        assert mock_requests_get.call_count == 1, "auth errors should not be retried"

    @patch("data.http_client.requests.get")
    @patch("data.http_client.time.sleep", return_value=None)
    def test_429_respects_retry_after_header(self, mock_sleep, mock_requests_get):
        from data.http_client import get_with_retry

        rate_limited = MagicMock(status_code=429, headers={"Retry-After": "2.5"})
        ok_resp = _mock_response({"ok": True})
        mock_requests_get.side_effect = [rate_limited, ok_resp]

        response = get_with_retry("https://example.com/api", max_attempts=3, base_backoff=0.01)
        assert response.json() == {"ok": True}
        # the sleep floor should have been at least the Retry-After value
        slept_for = mock_sleep.call_args[0][0]
        assert slept_for >= 2.5

    @patch("data.http_client.requests.get")
    @patch("data.http_client.time.sleep", return_value=None)
    def test_network_exception_retried_then_raises(self, mock_sleep, mock_requests_get):
        import requests as requests_module
        from data.http_client import get_with_retry

        mock_requests_get.side_effect = requests_module.exceptions.ConnectionError("boom")
        try:
            get_with_retry("https://example.com/api", max_attempts=2)
            assert False, "expected TransientProviderError"
        except TransientProviderError:
            pass
        assert mock_requests_get.call_count == 2


def _gecko_entry(symbol, coin_id, rank, market_cap, volume, pct_change_24h):
    return {
        "id": coin_id,
        "symbol": symbol.lower(),
        "name": f"{symbol} Coin",
        "market_cap_rank": rank,
        "market_cap": market_cap,
        "total_volume": volume,
        "price_change_percentage_24h": pct_change_24h,
    }


class TestCoinGeckoClient:
    @patch("data.coingecko.get_with_retry")
    def test_parses_markets_response(self, mock_get):
        mock_get.side_effect = [
            _mock_response([
                _gecko_entry("BTC", "bitcoin", 1, 1_200_000_000_000, 30_000_000_000, 2.5),
                _gecko_entry("ETH", "ethereum", 2, 400_000_000_000, 15_000_000_000, -1.2),
            ]),
            _mock_response([]),  # stablecoins category page
        ]
        client = CoinGeckoClient()
        coins = client.get_top_n(100)

        assert len(coins) == 2
        assert coins[0].symbol == "BTC"
        assert coins[0].provider_id == "bitcoin"
        assert coins[0].market_cap_rank == 1

    @patch("data.coingecko.get_with_retry")
    def test_no_api_key_required(self, mock_get):
        # constructing the client must not require any credential argument
        mock_get.side_effect = [_mock_response([]), _mock_response([])]
        client = CoinGeckoClient()
        coins = client.get_top_n(10)
        assert coins == []

    @patch("data.coingecko.get_with_retry")
    def test_excludes_low_volatility_stablecoins_via_category_lookup(self, mock_get):
        mock_get.side_effect = [
            _mock_response([
                _gecko_entry("USDT", "tether", 3, 100_000_000_000, 50_000_000_000, 0.02),
                _gecko_entry("BTC", "bitcoin", 1, 1_200_000_000_000, 30_000_000_000, 2.5),
            ]),
            _mock_response([_gecko_entry("USDT", "tether", 3, 100_000_000_000, 50_000_000_000, 0.02)]),
        ]
        client = CoinGeckoClient()
        coins = client.get_top_n(100, stablecoin_volatility_threshold_pct=1.0)

        symbols = {c.symbol for c in coins}
        assert "USDT" not in symbols
        assert "BTC" in symbols

    @patch("data.coingecko.get_with_retry")
    def test_keeps_volatile_stablecoin(self, mock_get):
        mock_get.side_effect = [
            _mock_response([_gecko_entry("USDC", "usd-coin", 5, 30_000_000_000, 4_000_000_000, -4.5)]),
            _mock_response([_gecko_entry("USDC", "usd-coin", 5, 30_000_000_000, 4_000_000_000, -4.5)]),
        ]
        client = CoinGeckoClient()
        coins = client.get_top_n(100, stablecoin_volatility_threshold_pct=1.0)
        assert len(coins) == 1
        assert coins[0].symbol == "USDC"

    @patch("data.coingecko.get_with_retry")
    def test_skips_malformed_entry_without_crashing(self, mock_get):
        malformed = {"symbol": "broken"}  # missing market_cap, id, etc.
        good = _gecko_entry("BTC", "bitcoin", 1, 1_200_000_000_000, 30_000_000_000, 2.5)
        mock_get.side_effect = [_mock_response([malformed, good]), _mock_response([])]

        client = CoinGeckoClient()
        coins = client.get_top_n(100)
        assert len(coins) == 1
        assert coins[0].symbol == "BTC"

    @patch("data.coingecko.get_with_retry")
    def test_unexpected_response_shape_raises(self, mock_get):
        mock_get.return_value = _mock_response({"error": "not a list"})
        client = CoinGeckoClient()
        try:
            client.get_top_n(100)
            assert False, "expected DataProviderError"
        except DataProviderError:
            pass


class TestUniverseService:
    def test_build_universe_maps_binance_symbols_and_drops_unmapped(self):
        cmc = MagicMock()
        cmc.get_top_n.return_value = [
            _make_coin("BTC", 1),
            _make_coin("ETH", 2),
            _make_coin("SOMEOBSCURECOIN", 3),
        ]
        binance = MagicMock()
        binance.get_tradable_spot_symbols.return_value = {"BTC", "ETH"}

        service = UniverseService(cmc, binance)
        universe = service.build_universe(top_n=100)

        symbols = {c.symbol for c in universe}
        assert symbols == {"BTC", "ETH"}
        btc = next(c for c in universe if c.symbol == "BTC")
        assert btc.binance_symbol == "BTCUSDT"

    def test_diff_detects_entries_and_exits(self):
        cmc = MagicMock()
        binance = MagicMock()
        service = UniverseService(cmc, binance)

        new_universe = [_make_coin("BTC", 1), _make_coin("SOL", 2), _make_coin("NEWCOIN", 3)]
        previous_symbols = ["BTC", "SOL", "OLDCOIN"]

        diff = service.diff_against_previous(new_universe, previous_symbols)
        assert diff.added == ["NEWCOIN"]
        assert diff.removed == ["OLDCOIN"]
        assert diff.changed is True

    def test_diff_reports_no_change_when_identical(self):
        cmc = MagicMock()
        binance = MagicMock()
        service = UniverseService(cmc, binance)

        new_universe = [_make_coin("BTC", 1), _make_coin("ETH", 2)]
        diff = service.diff_against_previous(new_universe, ["BTC", "ETH"])
        assert diff.changed is False


def _make_coin(symbol, rank):
    from data.models import UniverseCoin
    return UniverseCoin(
        symbol=symbol, name=symbol, provider_id=str(rank), market_cap_rank=rank,
        market_cap_usd=1e9, volume_24h_usd=1e8, percent_change_24h=1.0,
        is_stablecoin=False,
    )


if __name__ == "__main__":
    try:
        import pytest
        sys.exit(pytest.main([__file__, "-v"]))
    except ModuleNotFoundError:
        print("pytest not installed; run via a plain test runner instead.")
