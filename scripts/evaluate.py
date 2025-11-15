#!/usr/bin/env python3
import os, json, time, urllib.request, urllib.error

BASES = ["BTC","ETH","XRP","BNB","SOL","DOGE","ADA","LTC","SHIB","PUMP"]
SYMBOLS = [b + "USDT" for b in BASES]

API_URL = "https://min-api.cryptocompare.com/data/v2/histominute"

def fetch_last_close(symbol: str):
    """جلب آخر سعر إغلاق 1 دقيقة من CryptoCompare"""
    base = symbol.replace("USDT", "")
    url = f"{API_URL}?fsym={base}&tsym=USD&limit=2&aggregate=1"
    print(f"[evaluate] Fetching last close for {symbol} from {url}")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"[evaluate] ERROR {symbol}: {e}")
        return None

    rows = data.get("Data", {}).get("Data", [])
    if not rows:
        print(f"[evaluate] No rows for {symbol}")
        return None

    last_row = rows[-1]
    close = last_row.get("close")
    if close is None:
        return None
    return float(close)

def evaluate_file(data_root: str, symbol: str, horizon: int):
    """
    يفتح ملف 15m.jsonl أو 60m.jsonl
    يمر على التوقعات ذات outcome == 'Pending'
    إذا مرّ وقت كافٍ، يجلب السعر الحقيقي ويحدّد Correct / Wrong
    """
    path = os.path.join(data_root, symbol, f"{horizon}m.jsonl")
    if not os.path.exists(path):
        print(f"[evaluate] File not found: {path}")
        return False

    now_ms = int(time.time() * 1000)
    horizon_ms = horizon * 60 * 1000
    changed = False
    lines = []

    # نقرأ كل التوقعات
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue

            # لو ما فيها outcome أو ليست Pending، نخليها كما هي
            outcome = row.get("outcome")
            if outcome is None or outcome != "Pending":
                lines.append(json.dumps(row))
                continue

            t = row.get("t")
            base = row.get("base")
            direction = row.get("dir")
            if t is None or base is None or direction is None:
                # بيانات ناقصة، نخليها Pending كما هي
                lines.append(json.dumps(row))
                continue

            # هل مرّت الفترة المطلوبة؟
            if now_ms - int(t) < horizon_ms - 5000:
                # لم يحن وقت التقييم بعد
                lines.append(json.dumps(row))
                continue

            # الآن نحاول تقييم التوقع
            last_close = fetch_last_close(symbol)
            if last_close is None:
                # ما قدرنا نجيب السعر، نخليها Pending لمحاولة لاحقة
                lines.append(json.dumps(row))
                continue

            delta = (last_close / float(base)) - 1.0
            up = delta > 0
            if (up and direction == "Up") or ((not up) and direction == "Down"):
                row["outcome"] = "Correct"
            else:
                row["outcome"] = "Wrong"

            changed = True
            lines.append(json.dumps(row))

    if changed:
        print(f"[evaluate] Writing updated file: {path}")
        with open(path, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    else:
        print(f"[evaluate] No changes for {symbol} {horizon}m")

    return changed

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_root = os.path.join(root, "data")
    os.makedirs(data_root, exist_ok=True)

    any_changed = False

    for sym in SYMBOLS:
        for horizon in (15, 60):
            ok = evaluate_file(data_root, sym, horizon)
            any_changed = any_changed or ok

    if any_changed:
        print("[evaluate] Some predictions were updated.")
    else:
        print("[evaluate] Nothing to update.")

if __name__ == "__main__":
    main()
