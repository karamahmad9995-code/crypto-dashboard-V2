#!/usr/bin/env python3
import os, json, time

BASES = ["BTC","ETH","XRP","BNB","SOL","DOGE","ADA","LTC","SHIB","PUMP"]
SYMBOLS = [b + "USDT" for b in BASES]

HOURS_WINDOW = 24  # نافذة الملخص: آخر 24 ساعة

def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return rows

def compute_hit_rate(rows, horizon_ms):
    """
    rows: قائمة توقعات (من 15m.jsonl أو 60m.jsonl)
    نرجع: (hit_pct, n_trades)
    نحسب فقط التوقعات:
      - outcome in ['Correct','Wrong']
      - t ضمن آخر 24 ساعة
    """
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - HOURS_WINDOW * 60 * 60 * 1000

    total = 0
    correct = 0

    for r in rows:
        t = r.get("t")
        outcome = r.get("outcome")
        if t is None or outcome not in ("Correct", "Wrong"):
            continue
        if int(t) < cutoff:
            continue

        total += 1
        if outcome == "Correct":
            correct += 1

    if total == 0:
        return None, 0

    hit_pct = round((correct / total) * 100)
    return hit_pct, total

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_root = os.path.join(root, "data")
    os.makedirs(data_root, exist_ok=True)

    global_summary = {}

    for sym in SYMBOLS:
        sym_dir = os.path.join(data_root, sym)
        os.makedirs(sym_dir, exist_ok=True)

        path_15 = os.path.join(sym_dir, "15m.jsonl")
        path_60 = os.path.join(sym_dir, "60m.jsonl")

        rows_15 = read_jsonl(path_15)
        rows_60 = read_jsonl(path_60)

        hit15, n15 = compute_hit_rate(rows_15, 15 * 60 * 1000)
        hit60, n60 = compute_hit_rate(rows_60, 60 * 60 * 1000)

        sym_summary = {
            "symbol": sym,
        }
        h24 = {}

        if n15 > 0 and hit15 is not None:
            h24["hit15"] = hit15
            h24["n15"] = n15

        if n60 > 0 and hit60 is not None:
            h24["hit60"] = hit60
            h24["n60"] = n60

        # لو ما في أي صفقات في آخر 24 ساعة → لا نكتب h24
        if h24:
            sym_summary["h24"] = h24
            # هذا يُستخدم في الصفحة الرئيسية
            global_summary[sym] = {
                "h24": {
                    "hit15": h24.get("hit15"),
                    "hit60": h24.get("hit60"),
                }
            }

        out_path = os.path.join(sym_dir, "summary.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(sym_summary, f, ensure_ascii=False, indent=2)

        print(f"[summarize] {sym}: h24={h24 if h24 else 'no trades in last 24h'}")

    # الملف العمومي للصفحة الرئيسية
    summary_path = os.path.join(data_root, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(global_summary, f, ensure_ascii=False, indent=2)

    print(f"[summarize] Wrote global summary to {summary_path}")

if __name__ == "__main__":
    main()
