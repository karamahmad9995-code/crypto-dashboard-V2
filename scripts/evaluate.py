#!/usr/bin/env python3
"""
Robust evaluator for 15m / 60m predictions.

- لا يرمي استثناءات غير معالجة (حتى لا يفشل الـ Action).
- يتجاهل الرموز / السطور التي يحصل فيها خطأ API.
- يمر على كل Pending أقدم من (horizon + 2 دقائق) ويحوّلها إلى Correct / Wrong إذا أمكن.
"""

import os
import json
import time
import math
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

# نفس العملات التي تستخدمها في باقي السكربتات
BASES = ["BTC", "ETH", "XRP", "BNB", "SOL", "DOGE", "ADA", "LTC", "SHIB", "PUMP"]
SYMBOLS = [b + "USDT" for b in BASES]

# نستخدم CryptoCompare كمصدر رئيسي، لأنه لا يحتاج API key للاستخدام البسيط
CC_MINUTE_URL = "https://min-api.cryptocompare.com/data/v2/histominute"


# ---------- أدوات مساعدة آمنة لجلب JSON ----------

def safe_get_json(url: str, retries: int = 3, timeout: int = 20, sleep_sec: int = 2) -> Optional[Dict[str, Any]]:
    """
    يحاول جلب JSON من URL عدة مرات.
    لا يرمي استثناء للخارج؛ يرجع None في حال الفشل.
    """
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            print(f"[evaluate] WARN safe_get_json attempt {attempt} failed for {url}: {e}")
            if attempt < retries:
                time.sleep(sleep_sec)
    print(f"[evaluate] ERROR safe_get_json giving up on {url}")
    return None


def fetch_last_close(symbol: str) -> Optional[float]:
    """
    يجلب آخر سعر إغلاق 1 دقيقة من CryptoCompare لرمز مثل 'BTCUSDT' عبر fsym=BTC, tsym=USD.
    يرجع float أو None في حال الفشل.
    """
    base = symbol.replace("USDT", "")
    url = f"{CC_MINUTE_URL}?fsym={base}&tsym=USD&limit=2&aggregate=1"
    print(f"[evaluate] Fetching last close for {symbol} from {url}")
    data = safe_get_json(url)
    if not data:
        return None

    rows = data.get("Data", {}).get("Data", [])
    if not rows:
        print(f"[evaluate] WARN no rows for {symbol}")
        return None

    last_row = rows[-1]
    close = last_row.get("close")
    if close is None:
        print(f"[evaluate] WARN last row has no close for {symbol}")
        return None

    try:
        return float(close)
    except (TypeError, ValueError):
        return None


# ---------- قراءة وكتابة JSONL ----------

def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
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
                # نتجاهل السطور التالفة
                continue
    return rows


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------- منطق التقييم ----------

def evaluate_file(data_root: str, symbol: str, horizon: int) -> bool:
    """
    يفتح data/<symbol>/<horizon>m.jsonl
    يمر على الأسطر ذات outcome == "Pending" التي مرّ عليها وقت كافٍ
    يحاول جلب السعر الحالي وتحويل outcome إلى Correct / Wrong
    يرجع True إذا تم تعديل أي سطر.
    """
    rel = f"{symbol}/{horizon}m.jsonl"
    path = os.path.join(data_root, rel)

    rows = read_jsonl(path)
    if not rows:
        print(f"[evaluate] INFO no rows for {path}")
        return False

    now_ms = int(time.time() * 1000)
    horizon_ms = horizon * 60 * 1000
    margin_ms = 2 * 60 * 1000  # هامش أمان دقيقتين
    changed = False

    # نحاول جلب السعر مرة واحدة لكل ملف (لتقليل الضغط على API)
    last_close: Optional[float] = None

    new_rows: List[Dict[str, Any]] = []

    for row in rows:
        outcome = row.get("outcome")
        t = row.get("t")
        base = row.get("base")
        direction = row.get("dir")

        # لو ما فيها outcome أو ليست Pending، نخليها كما هي
        if outcome is None or outcome != "Pending" or t is None or base is None or direction is None:
            new_rows.append(row)
            continue

        # هل مر الوقت الكافي للتقييم؟
        age = now_ms - int(t)
        if age < (horizon_ms + margin_ms):
            # ما زال مبكرًا، نخليها Pending
            new_rows.append(row)
            continue

        # هنا يجب أن نقيّمها: نحاول جلب السعر إذا لم نجلبه بعد
        if last_close is None:
            last_close = fetch_last_close(symbol)

        if last_close is None:
            # فشل جلب السعر -> نترك الصف Pending لمحاولة لاحقة
            new_rows.append(row)
            continue

        try:
            base_price = float(base)
        except (TypeError, ValueError):
            new_rows.append(row)
            continue

        delta = (last_close / base_price) - 1.0
        up = delta > 0

        if (up and direction == "Up") or (not up and direction == "Down"):
            row["outcome"] = "Correct"
        else:
            row["outcome"] = "Wrong"

        changed = True
        new_rows.append(row)

    if changed:
        print(f"[evaluate] INFO updated file: {path}")
        write_jsonl(path, new_rows)
    else:
        print(f"[evaluate] INFO no changes for {path}")

    return changed


def main() -> None:
    """
    الدالة الرئيسية: تمر على كل الرموز وكل الأفقين 15m/60m.
    لا ترمي استثناءات للخارج حتى لو حصلت مشاكل.
    """
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_root = os.path.join(root, "data")
        os.makedirs(data_root, exist_ok=True)

        any_changed = False

        for sym in SYMBOLS:
            for horizon in (15, 60):
                try:
                    ok = evaluate_file(data_root, sym, horizon)
                    any_changed = any_changed or ok
                except Exception as e:
                    # لا نسمح لعمل رمز واحد أن يسقط السكربت كله
                    print(f"[evaluate] ERROR evaluating {sym} {horizon}m: {e}")

        if any_changed:
            print("[evaluate] DONE: some predictions were updated.")
        else:
            print("[evaluate] DONE: nothing to update.")
    except Exception as e:
        # حماية أخيرة حتى لا يخرج السكربت بكود خطأ
        print(f"[evaluate] FATAL error in main: {e}")


if __name__ == "__main__":
    main()
