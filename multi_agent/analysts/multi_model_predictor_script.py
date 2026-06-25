"""多模型融合预测子进程脚本"""
import sys, json, warnings, numpy as np
warnings.filterwarnings("ignore")

sys.path.insert(0, "MULTI_AGENT_DIR_PLACEHOLDER")
from core.data_layer import get_stock_data, calc_technical_indicators

ticker = "TICKER_PLACEHOLDER"
name = "NAME_PLACEHOLDER"
days_list = DAYS_PLACEHOLDER

df, _ = get_stock_data(ticker, calibrate=False)
if len(df) > 500:
    df = df.iloc[-500:]
df = calc_technical_indicators(df)
close = df["close"].values.astype(float)
total_len = len(close)
if total_len < 100:
    print(json.dumps({"error": "数据不足"})); sys.exit(0)

lookback = 20
results = {}
all_votes = []

for day in days_list:
    feats, labels = [], []
    for i in range(lookback, total_len - day):
        w = close[i-lookback:i]
        f = list(w) + list(np.diff(w)) + [float(np.mean(w)), float(np.std(w)), float(close[i-1]/np.mean(w)-1)]
        feats.append(f)
        labels.append(1 if close[i+day] > close[i] else 0)
    if len(feats) < 30:
        continue
    X = np.array(feats)
    y = np.array(labels)
    split = int(len(X) * 0.8)
    X_tr, X_te = X[:split], X[split:]
    y_tr, y_te = y[:split], y[split:]
    last_X = X[-1:]
    mr = {}

    try:
        import xgboost as xgb
        m = xgb.XGBClassifier(n_estimators=50, max_depth=3, random_state=42, verbosity=0)
        m.fit(X_tr, y_tr); p = int(m.predict(last_X)[0])
        mr["xgb"] = {"pred": p, "dir": "涨" if p else "跌", "test_acc": round(m.score(X_te, y_te)*100, 1)}
    except: pass

    try:
        import lightgbm as lgb
        m = lgb.LGBMClassifier(n_estimators=50, max_depth=3, random_state=42, verbose=-1)
        m.fit(X_tr, y_tr); p = int(m.predict(last_X)[0])
        mr["lgb"] = {"pred": p, "dir": "涨" if p else "跌", "test_acc": round(m.score(X_te, y_te)*100, 1)}
    except: pass

    try:
        from sklearn.ensemble import RandomForestClassifier
        m = RandomForestClassifier(n_estimators=50, max_depth=3, random_state=42)
        m.fit(X_tr, y_tr); p = int(m.predict(last_X)[0])
        mr["rf"] = {"pred": p, "dir": "涨" if p else "跌", "test_acc": round(m.score(X_te, y_te)*100, 1)}
    except: pass

    try:
        import tensorflow as tf
        X_lstm = X.reshape(X.shape[0], X.shape[1], 1)
        m = tf.keras.Sequential([
            tf.keras.layers.LSTM(32, input_shape=(X.shape[1], 1), return_sequences=False),
            tf.keras.layers.Dense(16, activation="relu"),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(1, activation="sigmoid"),
        ])
        m.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
        m.fit(X_lstm[:split], y_tr, epochs=15, batch_size=16, verbose=0, validation_split=0.1)
        _, te_acc = m.evaluate(X_lstm[split:], y_te, verbose=0)
        prob = float(m.predict(last_X.reshape(1, X.shape[1], 1), verbose=0)[0][0])
        p = 1 if prob > 0.5 else 0
        mr["lstm"] = {"pred": p, "dir": "涨" if p else "跌", "prob": round(prob, 3), "test_acc": round(te_acc*100, 1)}
    except: pass

    if mr:
        results[f"day_{day}"] = mr
        votes = [m["pred"] for m in mr.values()]
        all_votes.append(1 if sum(votes) > len(votes)/2 else 0)

out = {
    "ticker": ticker, "name": name,
    "current_price": round(float(close[-1]), 2),
    "data_days": total_len,
    "results": results,
    "ensemble": {
        "direction": "看涨" if (sum(all_votes)/max(len(all_votes),1)) > 0.5 else "看跌",
        "consensus": round(sum(all_votes)/max(len(all_votes),1)*100, 1),
    }
}
print(json.dumps(out, ensure_ascii=False))
