# train.py content from earlier cell
import sys, json, math, time
import os
from datetime import datetime, timedelta, timezone
try:
    import requests
except ImportError:
    os.system("python -m pip install requests -q")
    import requests

DAYS = int(os.environ.get("TRAIN_DAYS", "30"))
SYMBOLS = os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,ADAUSDT,LTCUSDT,SHIBUSDT,PUMPUSDT").split(",")
BINANCE = "https://api.binance.com"

def fetch_klines_1m(symbol, start_ts_ms, end_ts_ms):
    out = []
    limit = 1000
    cur_end = end_ts_ms
    while True:
        params = {"symbol": symbol, "interval": "1m", "endTime": cur_end, "limit": limit}
        r = requests.get(BINANCE + "/api/v3/klines", params=params, timeout=15)
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        chunk = [[int(k[0]), float(k[2]), float(k[3]), float(k[4])] for k in data]  # ts, high, low, close
        out = chunk + out
        oldest_ts = data[0][0]
        if oldest_ts <= start_ts_ms or len(data) < limit:
            break
        cur_end = oldest_ts - 1
        time.sleep(0.1)
    out = [row for row in out if start_ts_ms <= row[0] <= end_ts_ms]
    return out

def ema(series, p):
    k = 2.0/(p+1.0)
    prev = series[0]
    out = [prev]
    for i in range(1, len(series)):
        prev = series[i]*k + prev*(1.0-k)
        out.append(prev)
    return out

def sma(series, p):
    out = []
    s = 0.0
    for i in range(len(series)):
        s += series[i]
        if i>=p: s -= series[i-p]
        out.append(s / float(min(i+1, p)))
    return out

def stddev(window):
    m = sum(window)/len(window)
    v = sum((x-m)*(x-m) for x in window)/len(window)
    return math.sqrt(v)

def sigma_of_returns(closes, i, w=30):
    if i<1: return 0.0
    rets = []
    start = max(1, i-w+1)
    for j in range(start, i+1):
        rets.append( (closes[j]/closes[j-1]) - 1.0 )
    if not rets:
        return 0.0
    return stddev(rets)

def atr14(high, low, close):
    tr = [ (high[i]-low[i]) for i in range(len(close)) ]
    return sma(tr, 14)

def bb_pctb(closes, p=20):
    sm = sma(closes, p)
    sd = []
    for i in range(len(closes)):
        start = max(0, i-p+1)
        w = closes[start:i+1]
        sd.append( stddev(w) if len(w)>1 else 0.0 )
    out = []
    for i in range(len(closes)):
        if sd[i] == 0.0:
            out.append(0.0)
        else:
            out.append( (closes[i] - sm[i])/(2.0*sd[i]) )
    return out

def rsi14(closes):
    gains, losses = [0.0], [0.0]
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avgG = sma(gains, 14)
    avgL = sma(losses, 14)
    out = []
    for i in range(len(closes)):
        g = avgG[i]
        l = avgL[i] if avgL[i]!=0 else 1e-6
        rs = g/l
        out.append( 100.0 - (100.0/(1.0+rs)) )
    return out

def build_dataset(rows, horizon):
    ts = [r[0] for r in rows]
    high = [r[1] for r in rows]
    low  = [r[2] for r in rows]
    close= [r[3] for r in rows]

    ema5 = ema(close,5); ema15 = ema(close,15)
    sma5 = sma(close,5); sma15 = sma(close,15)
    rsi  = rsi14(close)

    feats = []
    labels = []
    for i in range(len(close)-horizon-1):
        if i<15: 
            continue
        s5 = ema5[i] - ema5[i-1]
        s15= ema15[i] - ema15[i-1]
        momentum = (close[i] / max(1e-9, close[max(0,i-15)])) - 1.0
        lastRet = (close[i] / close[i-1]) - 1.0
        sigma = sigma_of_returns(close, i, 30)
        x = [ rsi[i], s5, s15, momentum, lastRet, sigma ]
        feats.append(x)

        future = close[i+horizon]
        move = (future/close[i]) - 1.0
        dead = 0.001  # ±0.10% dead-zone
        if move > dead:
            labels.append(1)
        elif move < -dead:
            labels.append(0)
        else:
            feats.pop()
            continue
    return feats, labels

def standardize(X):
    import numpy as np
    X = np.array(X, dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd==0] = 1.0
    Xn = (X - mu)/sd
    return Xn, mu.tolist(), sd.tolist()

def train_logreg_sgd(Xn, y, lr=0.05, epochs=50, l2=0.001):
    import numpy as np
    Xn = np.array(Xn, dtype=float)
    y = np.array(y, dtype=float)
    n, d = Xn.shape
    W = np.zeros(d)
    b = 0.0
    def sigmoid(z): return 1.0/(1.0+np.exp(-z))
    for ep in range(epochs):
        z = Xn.dot(W) + b
        p = sigmoid(z)
        gradW = Xn.T.dot(p - y)/n + l2*W
        gradb = (p - y).mean()
        W -= lr*gradW
        b -= lr*gradb
    return W.tolist(), float(b)

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def run_symbol(symbol, days):
    print(f"[TRAIN] {symbol} days={days}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = fetch_klines_1m(symbol, int(start.timestamp()*1000), int(end.timestamp()*1000))
    if len(rows) < 2000:
        print(f"[WARN] not enough data for {symbol}: {len(rows)} rows")
    out_models = {}
    for horizon in (15, 60):
        X, y = build_dataset(rows, horizon)
        if len(y) < 200:
            print(f"[WARN] few samples {symbol} H{horizon}: {len(y)}")
            continue
        Xn, mu, sd = standardize(X)
        W, b = train_logreg_sgd(Xn, y, lr=0.05, epochs=60, l2=0.001)
        model = {
            "features": ["rsi","ema5_slope","ema15_slope","momentum","lastRet","sigma"],
            "W": W, "b": b, "scaler": {"mu": mu, "sd": sd},
            "meta": {"trained_at": datetime.now(timezone.utc).isoformat(), "symbol":symbol, "horizon":horizon, "n_samples": len(y)}
        }
        out_models[horizon] = model
        folder = os.path.join("data","models",symbol)
        ensure_dir(folder)
        with open(os.path.join(folder, f"{horizon}m.json"), "w", encoding="utf-8") as f:
            json.dump(model, f, indent=2)
        print(f"[OK] {symbol} H{horizon} -> data/models/{symbol}/{horizon}m.json (n={len(y)})")
    return out_models

def main():
    ensure_dir(os.path.join("data","models"))
    for sym in SYMBOLS:
        sym = sym.strip()
        if not sym: continue
        try:
            run_symbol(sym, DAYS)
        except Exception as e:
            print(f"[ERR] {sym}: {e}")

if __name__ == "__main__":
    main()
