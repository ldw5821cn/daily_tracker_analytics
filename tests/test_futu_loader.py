"""FutuLoader 单元测试（无需 OpenD 在线）。

验证：
- 代码转换（A 股/美股/港股/期货）
- 模块可加载且不破坏 registry
- 无 OpenD 时 is_available 返回 False
- 注册 markets 正确
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "multi_agent"))

import pytest

import data_sources.futu_loader as futu_loader
from core.data_loader_registry import LOADER_REGISTRY, list_loaders


def test_futu_loader_registered():
    assert 'futu' in LOADER_REGISTRY
    meta = LOADER_REGISTRY['futu']
    assert sorted(meta.markets) == sorted(['a_share', 'index', 'hk_equity', 'us_equity', 'futures'])
    assert meta.requires_auth is True


def test_to_futu_symbol_a_share():
    assert futu_loader._to_futu_symbol('000001.SZ') == ('SZ', 'SZ.000001')
    assert futu_loader._to_futu_symbol('000001') == ('SZ', 'SZ.000001')
    assert futu_loader._to_futu_symbol('600000') == ('SH', 'SH.600000')
    assert futu_loader._to_futu_symbol('600000.SH') == ('SH', 'SH.600000')
    assert futu_loader._to_futu_symbol('688012') == ('SH', 'SH.688012')


def test_to_futu_symbol_index():
    # 沪深300 指数
    assert futu_loader._to_futu_symbol('000300.SH') == ('SH', 'SH.000300')


def test_to_futu_symbol_hk_us():
    assert futu_loader._to_futu_symbol('00700.HK') == ('HK', 'HK.00700')
    assert futu_loader._to_futu_symbol('AAPL') == ('US', 'US.AAPL')
    assert futu_loader._to_futu_symbol('AAPL.US') == ('US', 'US.AAPL')


def test_to_futu_symbol_futures():
    # 映射表中存在的品种
    assert futu_loader._to_futu_symbol('M0') == ('US', 'US.ZSX25')
    assert futu_loader._to_futu_symbol('CU0') == ('HK', 'HK.CAU24')
    # 映射表中未存在的品种兜底
    m, c = futu_loader._to_futu_symbol('RB0')
    assert m == 'SG'
    assert c.startswith('SG.RBmain')


def test_loader_not_available_without_opend():
    loader = futu_loader.FutuLoader()
    # 默认 127.0.0.1:11111 无 OpenD，应快速返回 False
    assert loader.is_available() is False


def test_to_futu_kl_type():
    assert futu_loader._to_futu_kl_type('1D') == 'K_DAY'
    assert futu_loader._to_futu_kl_type('30m') == 'K_30M'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
