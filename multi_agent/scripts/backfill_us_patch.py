import sys
from datetime import datetime
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
from core.warehouse import save_daily_bars
import akshare as ak

missing = ['AAPL','ABBV','ADBE','AMD','AMT','AMZN','BA','BABA','BAC','BIDU','CAT','COST','CRM','CVX','DIA','DIS','GE','GOOGL','GS','HD','IBM','INTC','IWM','JD','JNJ','JPM','KO','LIN','LLY','MA','MCD','META','MRK','MSFT','NFLX','NKE','NTES','NVDA','ORCL','PDD','PEP','PFE','SPY','TCEHY','TSLA','UBER','UNH','V','VTI','WFC','WMT','XOM']

start = '20250101'
end = '20260722'

for sym in missing:
    try:
        df = ak.stock_us_daily(symbol=sym, adjust='qfq')
        df = df[(df['date'] >= start) & (df['date'] <= end)]
        records = []
        for _, row in df.iterrows():
            records.append({
                'date': row['date'].strftime('%Y-%m-%d'),
                'ticker': sym,
                'category': 'US',
                'open': float(row['open']) if row['open'] else None,
                'high': float(row['high']) if row['high'] else None,
                'low': float(row['low']) if row['low'] else None,
                'close': float(row['close']) if row['close'] else None,
                'volume': float(row['volume']) if row['volume'] else None,
            })
        if records:
            save_daily_bars(records)
            print(f'{sym}: {len(records)} rows')
    except Exception as e:
        print(f'{sym}: {type(e).__name__}: {str(e)[:80]}')
