"""
预测数据管理层

核心职责:
1. 新建独立 SQLite 数据库, 保存每次回测和预测使用的原始数据、特征
2. 写入前做数据可用性检查、错误校验、 NaN/重复/异常值处理
3. 数据自动清理, 最多保留 1 年
4. 为预测验证器、模型对比、自适应预测提供数据读写接口
"""
import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.expanduser(
    '~/github/daily_tracker_analytics/multi_agent/data/prediction_data.db'
)

DATA_RETENTION_DAYS = 365


class DataValidationError(Exception):
    """数据校验失败异常"""


class PredictionDataStore:
    """预测数据存储"""

    REQUIRED_OHLCV_COLS = ['open', 'high', 'low', 'close', 'volume']
    OPTIONAL_FEATURES = ['ma5', 'ma10', 'ma20', 'ma60',
                         'rsi6', 'rsi14', 'rsi24', 'macd', 'macd_signal', 'macd_hist',
                         'boll_upper', 'boll_lower', 'boll_mid', 'atr14', 'vol_ratio',
                         'kdj_k', 'kdj_d', 'kdj_j', 'vol_ma5', 'vol_ma20',
                         'annual_vol_20d', 'annual_vol_60d']

    FEATURE_NAME_MAP = {
        'rsi_14': 'rsi14',
        'rsi_6': 'rsi6',
        'rsi_24': 'rsi24',
        'macd_hist': 'macd_hist',
        'macd_signal': 'macd_signal',
        'boll_up': 'boll_upper',
        'boll_down': 'boll_lower',
        'boll_mid': 'boll_mid',
        'atr_14': 'atr14',
        'vol_ratio': 'vol_ratio',
        'vol_ma5': 'vol_ma5',
        'vol_ma20': 'vol_ma20',
        'annual_vol_20d': 'annual_vol_20d',
        'annual_vol_60d': 'annual_vol_60d',
        'kdj_j': 'kdj_j',
        'kdj_k': 'kdj_k',
        'kdj_d': 'kdj_d',
    }

    def __init__(self, db_path: Optional[str] = None,
                 retention_days: int = DATA_RETENTION_DAYS):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.retention_days = retention_days
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    # ---------- 数据库初始化 ----------

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS raw_market_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL NOT NULL,
                    volume REAL,
                    source TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, trade_date)
                );
                CREATE INDEX IF NOT EXISTS idx_raw_ticker_date
                    ON raw_market_data(ticker, trade_date);

                CREATE TABLE IF NOT EXISTS features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    feature_name TEXT NOT NULL,
                    feature_value REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, trade_date, feature_name)
                );
                CREATE INDEX IF NOT EXISTS idx_features_ticker_date
                    ON features(ticker, trade_date);

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE,
                    run_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    model TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    test_days INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS backtest_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    forecast_date TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    current_price REAL,
                    pred_price REAL,
                    pred_return REAL,
                    pred_direction TEXT,
                    actual_price REAL,
                    actual_return REAL,
                    actual_direction TEXT,
                    direction_correct INTEGER,
                    return_error REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bt_ticker_date
                    ON backtest_records(ticker, forecast_date);

                CREATE TABLE IF NOT EXISTS forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    forecast_date TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    current_price REAL,
                    pred_price REAL,
                    pred_return REAL,
                    pred_direction TEXT,
                    features_snapshot TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, forecast_date, horizon, model)
                );
                CREATE INDEX IF NOT EXISTS idx_fc_ticker_date
                    ON forecasts(ticker, forecast_date);
            ''')

    # ---------- 数据校验 ----------

    def validate_ohlcv(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """
        校验 OHLCV 数据可用性
        返回清洗后的 DataFrame, 失败抛出 DataValidationError
        """
        if df is None or df.empty:
            raise DataValidationError(f"{ticker}: 数据为空")

        df = df.copy()
        # 统一列名小写
        df.columns = [str(c).lower() for c in df.columns]

        # 必须有 close
        if 'close' not in df.columns:
            raise DataValidationError(f"{ticker}: 缺少 close 列")

        # 检查必须有列
        missing = [c for c in self.REQUIRED_OHLCV_COLS if c not in df.columns]
        if missing:
            logger.warning(f"{ticker}: 缺失列 {missing}, 尝试填充")
            for c in missing:
                if c == 'volume':
                    df[c] = 0
                else:
                    df[c] = df['close']

        # 检查 close 有效值数量
        valid_close = df['close'].notna().sum()
        if valid_close < 30:
            raise DataValidationError(f"{ticker}: 有效 close 数据仅 {valid_close} 条, 不足 30")

        # 检查价格异常值
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                bad = df[col].notna() & (df[col] <= 0)
                if bad.any():
                    bad_dates = df.index[bad].tolist()
                    raise DataValidationError(f"{ticker}: {col} 存在非正值 at {bad_dates[:5]}")

        # high >= low, high >= close, low <= close
        if all(c in df.columns for c in ['high', 'low', 'close']):
            invalid = df[df['high'] < df['low']]
            if not invalid.empty:
                raise DataValidationError(f"{ticker}: 存在 high<low 异常 at {invalid.index.tolist()[:5]}")

        # 检查重复日期
        dup_dates = df.index[df.index.duplicated(keep=False)].unique()
        if len(dup_dates) > 0:
            df = df[~df.index.duplicated(keep='first')]
            logger.warning(f"{ticker}: 删除重复日期 {dup_dates.tolist()[:5]}")

        # 检查连续缺失
        if df['close'].isna().sum() > valid_close * 0.1:
            raise DataValidationError(f"{ticker}: close 缺失率超过 10%")

        # 删除未来日期的数据
        today = pd.Timestamp.now().normalize()
        df = df[df.index <= today]

        # 按日期排序
        df = df.sort_index()

        return df

    # ---------- 原始行情数据 ----------

    def save_market_data(self, ticker: str, df: pd.DataFrame,
                         source: str = 'sina',
                         validate: bool = True) -> int:
        """
        保存原始行情数据, 返回写入条数
        """
        if validate:
            df = self.validate_ohlcv(df, ticker)

        records = []
        for idx, row in df.iterrows():
            records.append((
                ticker,
                idx.strftime('%Y-%m-%d'),
                float(row.get('open', row['close'])) if pd.notna(row.get('open', row['close'])) else None,
                float(row.get('high', row['close'])) if pd.notna(row.get('high', row['close'])) else None,
                float(row.get('low', row['close'])) if pd.notna(row.get('low', row['close'])) else None,
                float(row['close']) if pd.notna(row['close']) else None,
                float(row['volume']) if pd.notna(row.get('volume')) else None,
                source,
                datetime.now().isoformat(),
            ))

        with sqlite3.connect(self.db_path) as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO raw_market_data
                (ticker, trade_date, open, high, low, close, volume, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', records)
            conn.commit()

        logger.info(f"{ticker}: 保存 {len(records)} 条行情数据")
        self._cleanup_old_data()
        return len(records)

    def load_market_data(self, ticker: str, days: int = 500,
                         end_date: Optional[str] = None) -> pd.DataFrame:
        """从数据库读取行情数据, 不足时返回空"""
        end = pd.Timestamp(end_date) if end_date else pd.Timestamp.now().normalize()
        start = end - timedelta(days=days + 60)

        with sqlite3.connect(self.db_path) as conn:
            sql = '''
                SELECT trade_date, open, high, low, close, volume
                FROM raw_market_data
                WHERE ticker = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date ASC
            '''
            df = pd.read_sql_query(sql, conn,
                                   params=(ticker, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')),
                                   parse_dates=['trade_date'])

        if df.empty:
            return df

        df.set_index('trade_date', inplace=True)
        df.index.name = 'date'
        return df

    # ---------- 特征数据 ----------

    def save_features(self, ticker: str, df: pd.DataFrame):
        """保存计算后的特征, 自动映射列名"""
        # 列名标准化
        col_map = {c.lower(): c for c in df.columns}
        records = []
        for idx, row in df.iterrows():
            for feat in self.OPTIONAL_FEATURES:
                src = self.FEATURE_NAME_MAP.get(feat, feat)
                # 兼容原始列名
                if src not in df.columns:
                    src = src.replace('_', '')
                if src in df.columns and pd.notna(row[src]):
                    records.append((
                        ticker, idx.strftime('%Y-%m-%d'), feat,
                        float(row[src]), datetime.now().isoformat()
                    ))
        if not records:
            return 0
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany('''
                INSERT OR REPLACE INTO features
                (ticker, trade_date, feature_name, feature_value, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', records)
            conn.commit()
        return len(records)

    def load_features(self, ticker: str, end_date: Optional[str] = None,
                      name_map: bool = True) -> pd.DataFrame:
        """读取特征数据并 pivot 成 DataFrame"""
        end = pd.Timestamp(end_date) if end_date else pd.Timestamp.now().normalize()
        start = end - timedelta(days=500)
        with sqlite3.connect(self.db_path) as conn:
            sql = '''
                SELECT trade_date, feature_name, feature_value
                FROM features
                WHERE ticker = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date ASC
            '''
            df = pd.read_sql_query(sql, conn,
                                   params=(ticker, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')),
                                   parse_dates=['trade_date'])
        if df.empty:
            return df
        df = df.pivot(index='trade_date', columns='feature_name', values='feature_value')
        df.index.name = 'date'
        if name_map:
            # 转回 improved_predictor 需要的下划线列名
            reverse_map = {v: k for k, v in self.FEATURE_NAME_MAP.items()}
            df = df.rename(columns=reverse_map)
        return df

    # ---------- 回测记录 ----------

    def save_backtest_records(self, run_id: str, ticker: str, model: str,
                              horizon: int, test_days: int,
                              records: List[Dict]):
        """保存滚动回测结果"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO backtest_runs
                (run_id, run_date, ticker, model, horizon, test_days, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (run_id, datetime.now().strftime('%Y-%m-%d'), ticker, model,
                  horizon, test_days, datetime.now().isoformat()))

            for r in records:
                conn.execute('''
                    INSERT OR REPLACE INTO backtest_records
                    (run_id, ticker, forecast_date, horizon, model,
                     current_price, pred_price, pred_return, pred_direction,
                     actual_price, actual_return, actual_direction,
                     direction_correct, return_error, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    run_id, r['ticker'], r['forecast_date'], r['horizon'], r['model'],
                    r.get('current_price'), r.get('pred_price'), r.get('pred_return'),
                    r.get('pred_direction'), r.get('actual_price'), r.get('actual_return'),
                    r.get('actual_direction'), r.get('direction_correct'), r.get('return_error'),
                    datetime.now().isoformat()
                ))
            conn.commit()

    # ---------- 预测记录 ----------

    def save_forecasts(self, records: List[Dict]):
        """保存预测"""
        if not records:
            return
        with sqlite3.connect(self.db_path) as conn:
            for r in records:
                conn.execute('''
                    INSERT OR REPLACE INTO forecasts
                    (ticker, name, forecast_date, horizon, model, current_price,
                     pred_price, pred_return, pred_direction, features_snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    r.get('ticker'), r.get('name'), r.get('forecast_date'),
                    r.get('horizon'), r.get('model', ''), r.get('current_price'),
                    r.get('pred_price'), r.get('pred_return'), r.get('pred_direction'),
                    json.dumps(r.get('features', {}), ensure_ascii=False, default=str),
                    datetime.now().isoformat()
                ))
            conn.commit()

    def get_forecasts(self, ticker: Optional[str] = None,
                      forecast_date: Optional[str] = None,
                      verified: Optional[bool] = None) -> pd.DataFrame:
        """读取预测记录"""
        where = []
        params = []
        if ticker:
            where.append('ticker = ?')
            params.append(ticker)
        if forecast_date:
            where.append('forecast_date = ?')
            params.append(forecast_date)

        sql = 'SELECT * FROM forecasts'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY forecast_date DESC, horizon ASC'

        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql_query(sql, conn, params=params)
        return df

    # ---------- 数据清理 ----------

    def _cleanup_old_data(self):
        """删除超过保留期的数据"""
        cutoff = (datetime.now() - timedelta(days=self.retention_days)).strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            for table, date_col in [
                ('raw_market_data', 'trade_date'),
                ('features', 'trade_date'),
                ('backtest_records', 'forecast_date'),
                ('forecasts', 'forecast_date'),
            ]:
                conn.execute(f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,))
            conn.execute(
                "DELETE FROM backtest_runs WHERE run_date < ?",
                (cutoff,)
            )
            conn.commit()
        logger.info(f"清理 {self.retention_days} 天前数据完成 (cutoff={cutoff})")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.data_layer import get_stock_data, calc_technical_indicators

    ds = PredictionDataStore()
    ticker = '516150'
    df, _ = get_stock_data(ticker, calibrate=False)
    df = calc_technical_indicators(df)
    print(f"校验并保存 {ticker} ...")
    n = ds.save_market_data(ticker, df, source='sina')
    ds.save_features(ticker, df)
    print(f"已保存 {n} 条行情数据")
    loaded = ds.load_market_data(ticker)
    print(f"库中可查询到 {len(loaded)} 条")
