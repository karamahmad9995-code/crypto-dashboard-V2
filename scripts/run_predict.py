#!/usr/bin/env python3
import os, json, time, math, urllib.request, urllib.error

BASES = ["BTC","ETH","XRP","BNB","SOL","DOGE","ADA","LTC","SHIB","PUMP"]
SYMBOLS = [b + "USDT" for b in BASES]

API_URL = "https://min-api.cryptocompare.com/data/v2/histominute"

# كم شمعة نحتاج لبناء المزايا
HIST_15 = 16   # تقريباً 15 دقيقة (نفس منطق المتصفح)
HIST_60 = 61   # تقريباً 60 دقيقة

def fetch_last_closes(symbol: str, need: int):
    """جلب آخر need شمعة 1m من CryptoCompare"""
    base = symbol.replace("USDT", "")
    url = f"{API_URL}?fsym={base}&tsym=USD&limit={need}&aggregate=1"
    print(f"[predict] Fetching {symbol} ({need}m) from {url}")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[predict] ERROR {symbol}: {e}")
        return []

    rows = data.get("Data", {}).get("Data", [])
    closes = []
    for p in rows:
        c = p.get("close")
        if c is None:
            continue
        closes.append(float(c))
    return closes

def ema(arr, p):
    k = 2.0 / (p + 1.0)
    prev = arr[0]
    out = [prev]
    for i in range(1, len(arr)):
        prev = arr[i] * k + prev * (1.0 - k)
        out.append(prev)
    return out

def stddev(a):
    if not a:
        return 0.0
    m = sum(a) / len(a)
    v = sum((x - m) ** 2 for x in a) / len(a)
    return math.sqrt(v)

def rsi14(cl):
    if len(cl) < 15:
        return 50.0
    g = 0.0
    l = 0.0
    for i in range(1, 15):
        d = cl[i] - cl[i - 1]
        if d >= 0:
            g += d
        else:
            l -= d
    avgG = g / 14.0
    avgL = (l / 14.0) if l > 0 else 1e-6
    rs = avgG / avgL
    return 100.0 - (100.0 / (1.0 + rs))

def build_features(closes):
    ema5 = ema(closes, 5)
    ema15 = ema(closes, 15)
    momentum = (closes[-1] / closes[0]) - 1.0
    rets = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
    sigma = stddev(rets)
    rsi = rsi14(closes)
    s5 = ema5[-1] - ema5[-2]
    s15 = ema15[-1] - ema15[-2]
    return {
        "rsi": rsi,
        "s5": s5,
        "s15": s15,
        "momentum": momentum,
        "lastRet": rets[-1] if rets else 0.0,
        "sigma": sigma,
    }

def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))

def predict_simple(feat):
    # نفس البارامترات من الجافاسكربت
    mu  = [50, 0, 0, 0, 0, 0.003]
    sd  = [12, 0.5, 0.3, 0.01, 0.005, 0.002]
    W   = [0.35, 0.45, 0.25, 0.80, 0.30, -0.15]
    b   = 0.0

    x_vec = [
        feat["rsi"],
        feat["s5"],
        feat["s15"],
        feat["momentum"],
        feat["lastRet"],
        feat["sigma"],
    ]
    # تطبيع
    nx = []
    for i, v in enumerate(x_vec):
        s = sd[i] if sd[i] != 0 else 1.0
        nx.append((v - mu[i]) / s)

    z = sum(w * v for w, v in zip(W, nx)) + b
    p_up = sigmoid(z)
    direction = "Up" if p_up >= 0.5 else "Down"
    conf_raw = max(p_up, 1.0 - p_up)
    confidence = max(0.55, min(0.95, conf_raw))

    # مدى الحركة المتوقعة (بالمئة)
    rng = 0.8 * (feat["sigma"] * 100.0) + 0.6 * (abs(feat["momentum"]) * 100.0)
    rng = max(0.2, min(2.0, rng))
    lo = max(0.10, rng * 0.55)
    hi = rng

    return {
        "direction": direction,
        "confidence": confidence,
        "rangePct": [lo, hi],
    }

def make_prediction(symbol: str, horizon: int):
    need = HIST_15 if horizon == 15 else HIST_60
    closes = fetch_last_closes(symbol, need)
    if len(closes) < need:
        print(f"[predict] Not enough data for {symbol} horizon {horizon}m")
        return None

    base = closes[-1]
    feat = build_features(closes)
    res = predict_simple(feat)

    # نفس منطق noise في الواجهة
    noise = (res["confidence"] < 0.62) or (res["rangePct"][1] < 0.30)

    lo_pct, hi_pct = res["rangePct"]
    if res["direction"] == "Up":
        price_lo = base * (1.0 + lo_pct / 100.0)
        price_hi = base * (1.0 + hi_pct / 100.0)
    else:
        price_lo = base * (1.0 - hi_pct / 100.0)
        price_hi = base * (1.0 - lo_pct / 100.0)

    ts = int(time.time() * 1000)
    row = {
        "id": f"{ts}-{horizon}",
        "t": ts,
        "src": "auto",
        "dir": res["direction"],
        "conf": res["confidence"],
        "range": res["rangePct"],
        "priceLo": price_lo,
        "priceHi": price_hi,
        "base": base,
        "horizon": horizon,
        "outcome": "No-Trade" if noise else "Pending",
    }
    return row

def upsert_jsonl(path: str, row: dict, max_len: int = 2000):
    """نضيف سطر جديد مع الحفاظ على حد أقصى لعدد السطور"""
    lines = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    lines.append(ln)
    lines.append(json.dumps(row))
    if len(lines) > max_len:
        lines = lines[-max_len:]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_root = os.path.join(root, "data")
    os.makedirs(data_root, exist_ok=True)

    for sym in SYMBOLS:
        for horizon in (15, 60):
            row = make_prediction(sym, horizon)
            if not row:
                continue
            out_path = os.path.join(data_root, sym, f"{horizon}m.jsonl")
            print(f"[predict] {sym} {horizon}m → {out_path}")
            upsert_jsonl(out_path, row, max_len=2000)
            # راحة بسيطة لتجنب الضغط على الـ API
            time.sleep(1)

if __name__ == "__main__":
    main()
