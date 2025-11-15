#!/usr/bin/env python3
import os, json, time, urllib.request, urllib.error

BASES = ["BTC","ETH","XRP","BNB","SOL","DOGE","ADA","LTC","SHIB","PUMP"]
SYMBOLS = [b + "USDT" for b in BASES]

API_URL = "https://min-api.cryptocompare.com/data/v2/histominute"

# عدد النقاط لكل عملة (مثلاً 720 ≈ 12 ساعة، 1440 ≈ يوم كامل)
LIMIT = 720

def fetch_hist_minute(symbol: str):
    base = symbol.replace("USDT", "")
    url = f"{API_URL}?fsym={base}&tsym=USD&limit={LIMIT}&aggregate=1"
    print(f"[fetch_history] Fetching {symbol} from {url}")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[fetch_history] ERROR {symbol}: {e}")
        return []

    if not data.get("Data") or not data["Data"].get("Data"):
        print(f"[fetch_history] No data for {symbol}")
        return []

    out = []
    for p in data["Data"]["Data"]:
        # p: {time, high, low, open, close, volumefrom, volumeto}
        t = p.get("time")
        c = p.get("close")
        if t is None or c is None:
            continue
        out.append({
            "t": int(t) * 1000,   # ms timestamp مثل ما نستخدم في الواجهة
            "c": float(c)
        })
    return out

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_root = os.path.join(root, "data")
    os.makedirs(data_root, exist_ok=True)

    for sym in SYMBOLS:
        hist = fetch_hist_minute(sym)
        if not hist:
            continue

        sym_dir = os.path.join(data_root, sym)
        os.makedirs(sym_dir, exist_ok=True)

        out_path = os.path.join(sym_dir, "raw_1m.jsonl")
        print(f"[fetch_history] Writing {len(hist)} rows to {out_path}")

        # نكتب الملف بالكامل في كل مرة (بسيط وواضح الآن)
        with open(out_path, "w", encoding="utf-8") as f:
            for row in hist:
                f.write(json.dumps(row) + "\n")

        # نرتاح ثانية بين كل عملة (احتياط لمحدودية الـ API المجانية)
        time.sleep(1)

if __name__ == "__main__":
    main()
