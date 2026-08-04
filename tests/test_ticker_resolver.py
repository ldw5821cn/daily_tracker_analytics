"""ticker_resolver 单元测试"""
import sys
from pathlib import Path

# 保证能从项目根导入 multi_agent/services
MULTI_AGENT = Path(__file__).resolve().parents[1] / "multi_agent"
sys.path.insert(0, str(MULTI_AGENT))

import pytest

from services.ticker_resolver import (
    detect_market,
    suffix_for_ticker,
    category_for_ticker,
    lookup_ticker,
)


class TestDetectMarket:
    def test_shanghai_stock(self):
        assert detect_market("601899") == "cn_sh"
        assert detect_market("688012") == "cn_sh"

    def test_shenzhen_stock(self):
        assert detect_market("000612") == "cn_sz"
        assert detect_market("300394") == "cn_sz"

    def test_index(self):
        assert detect_market("000300") == "index"
        assert detect_market("399006") == "index"
        assert detect_market("930050") == "index"

    def test_hk(self):
        assert detect_market("00700.HK") == "hk"
        assert detect_market("1234") == "hk"
        assert detect_market("01234") == "hk"

    def test_us(self):
        assert detect_market("AAPL") == "us"
        assert detect_market("JMO") == "us"

    def test_futures(self):
        assert detect_market("M00") == "futures"
        assert detect_market("FG0") == "futures"

    def test_chinese_name_unknown(self):
        assert detect_market("紫金矿业") == "unknown"


class TestSuffixForTicker:
    def test_shanghai(self):
        assert suffix_for_ticker("601899") == "601899.XSHG"

    def test_shenzhen(self):
        assert suffix_for_ticker("000612") == "000612.XSHE"
        assert suffix_for_ticker("300394") == "300394.XSHE"

    def test_index(self):
        assert suffix_for_ticker("000300") == "000300.XSHG"
        assert suffix_for_ticker("399006") == "399006.XSHE"

    def test_hk(self):
        assert suffix_for_ticker("00700.HK") == "00700.HK"


class TestCategoryForTicker:
    def test_stock(self):
        assert category_for_ticker("601899") == "个股"

    def test_etf(self):
        assert category_for_ticker("515880") == "ETF"
        assert category_for_ticker("516150") == "ETF"
        assert category_for_ticker("159819") == "ETF"

    def test_index(self):
        assert category_for_ticker("000300") == "指数"

    def test_futures(self):
        assert category_for_ticker("M00") == "期货"


class TestLookupTicker:
    def test_lookup_by_chinese_name(self):
        hits = lookup_ticker("紫金矿业", limit=1)
        assert len(hits) == 1
        assert hits[0]["ticker"] == "601899"

    def test_lookup_by_ticker(self):
        hits = lookup_ticker("688012", limit=1)
        assert len(hits) == 1
        assert hits[0]["name"] == "中微公司"

    def test_lookup_theme(self):
        hits = lookup_ticker("通信", limit=3)
        tickers = {h["ticker"] for h in hits}
        assert "515880" in tickers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
