#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Predict script for crypto-dashboard-V2

- يحاول يجلب بيانات 1m من أكثر من مصدر (Binance → CryptoCompare → CoinCap).
- لكل عملة ولكل أفق زمني (15m / 60m) يعمل توقع واحد فقط لكل فتحة زمنية.
- لو فشلت عملة لا يوقف السكربت بالكامل، فقط يطبع رسالة خطأ ويكمل الباقي.
- يكتب النتائج في: data/{SYMBOL}/{HORIZON}m.jsonl
  بنفس الشكل الذي تستخدمه الواجهة الأمامية.
"""

import os
import sys
import json
import time
import math
import random
from pathlib import Path

import requests

# ----------------- إعدادات عامة -----------------

SYMBOLS_DEFAULT = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "SOLUSDT",
    "DOGEUSDT", "ADAUSDT", "LTCUSDT", "SHIBUSDT", "PUMPUSDT",
]

HORIZONS_DEFAULT = [15, 60]  # بالدقائق

BINANCE_BASE = "https://api.binance.com"
CC_MINUTE_URL = "https://min-api.cryptocompare.com/data/v2/histominute"
CC_HOUR_URL = "https://min-api.cryptocompare.com/data/v2/histohour"
COINCAP_CANDLES_URL = "https://api.coincap.io/v2/candles"

COINCAP_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "XRP": "xrp",
    "BNB": "binance-coin",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "LTC": "litecoin",
    "SHIB": "shiba-inu",
    "PUMP": "pump",
}

SESSION = requests.Session()
SESSION.headers.update(
    {"User-Agent": "crypto-dashboard-v2-predict/1.0 (+github-actions)"}
)


# ----------------- دوال مساعدة عامة -----------------

def log(msg: str) -> None:
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]", time.gmtime())
    print(f"{ts} {msg}", flush=True)


def parse_symbols():
    env = os.getenv("SYMBOLS")
    if env:
        return [s.strip().upper() for s in env.split(",") if s.strip()]
    return SYMBOLS_DEFAULT[:]


def parse_horizons():
    # يمكن تمرير الأفق من:
    # - متغير بيئة HORIZON_MINUTES أو HORIZON
    # - أو كـ argument: python predict.py 15
    env_h = os.getenv("HORIZON_MINUTES") or os.getenv("HORIZON")
    arg_h = None
    if len(sys.argv) > 1:
        try:
            arg_h = int(sys.argv[1])
        except ValueError:
            arg_h = None

    h = None
    if arg_h is not None:
        h = arg_h
    elif env_h and env_h.isdigit():
        h = int(env_h)

    if h:
        return [h]
    return HORIZONS_DEFAULT[:]


def fetch_json(url, params=None, retries: int = 3, timeout: int = 10):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = 5 * attempt
                log(f"rate limited {resp.status_code} on {url}, sleep {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log(f"warn: fetch_json attempt {attempt} failed for {url}: {exc}")
            time.sleep(1 * attempt)
    raise last_exc or RuntimeError(f"fetch_json failed for {url}")


# ----------------- جلب بيانات 1m -----------------

def fetch_klines_1m(symbol: str, limit: int = 120):
    """
    يحاول يجلب بيانات 1m من:
    1) Binance
    2) CryptoCompare
    3) CoinCap
    لو فشل الكل → يرمي خطأ.
    """
    base = symbol.replace("USDT", "")

    # 1) Binance
    try:
        data = fetch_json(
            f"{BINANCE_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": "1m", "limit": limit},
        )
        if isinstance(data, list) and len(data) >= 2:
            return [{"t": int(k[0]), "c": float(k[4])} for k in data]
    except Exception as exc:  # noqa: BLE001
        log(f"info: Binance klines failed for {symbol}: {exc}")

    # 2) CryptoCompare (minute)
    try:
        j = fetch_json(
            CC_MINUTE_URL,
            params={"fsym": base, "tsym": "USD", "limit": limit},
        )
        dlist = j.get("Data", {}).get("Data") or []
        if dlist:
            return [
                {"t": int(p["time"]) * 1000, "c": float(p["close"])}
                for p in dlist
            ]
    except Exception as exc:  # noqa: BLE001
        log(f"info: CryptoCompare minute failed for {symbol}: {exc}")

    # 3) CoinCap candles (m1)
    try:
        asset_id = COINCAP_IDS.get(base)
        if asset_id:
            now_ms = int(time.time() * 1000)
            start = now_ms - limit * 60 * 1000
            params = {
                "exchange": "binance",
                "interval": "m1",
                "base": asset_id,
                "quote": "usd",
                "start": start,
                "end": now_ms,
            }
            j = fetch_json(COINCAP_CANDLES_URL, params=params)
            dlist = j.get("data") or []
            if dlist:
                out = []
                for item in dlist:
                    t = item.get("period") or item.get("time") or item.get("timestamp")
                    if t is None:
                        continue
                    if "close" in item:
                        c = float(item["close"])
                    elif "priceClose" in item:
                        c = float(item["priceClose"])
                    else:
                        continue
                    out.append({"t": int(t), "c": c})
                if out:
                    return out
    except Exception as exc:  # noqa: BLE001
        log(f"info: CoinCap candles failed for {symbol}: {exc}")

    raise RuntimeError(f"No klines available for {symbol}")


# ----------------- نموذج التوقع البسيط -----------------

def ema(series, span: int):
    k = 2.0 / (span + 1.0)
    ema_val = series[0]
    out = [ema_val]
    for x in series[1:]:
        ema_val = x * k + ema_val * (1.0 - k)
        out.append(ema_val)
    return out


def stddev(vals):
    if not vals:
        return 0.0
    m = sum(vals) / len(vals)
    var = sum((x - m) ** 2 for x in vals) / len(vals)
    return math.sqrt(var)


def rsi14(closes):
    if len(closes) < 15:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(1, 15):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_g = gains / 14.0
    avg_l = losses / 14.0 or 1e-6
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def build_features(closes):
    # نستخدم آخر 60 نقطة لعمل المؤشرات
    window = closes[-60:] if len(closes) >= 60 else closes[:]
    ema5 = ema(window, 5)
    ema15 = ema(window, 15)
    momentum = window[-1] / window[0] - 1.0
    rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
    sigma = stddev(rets) or 0.0005
    last_ret = rets[-1] if rets else 0.0
    rsi_val = rsi14(window[-15:]) if len(window) >= 15 else 50.0
    s5 = ema5[-1] - ema5[-2] if len(ema5) >= 2 else 0.0
    s15 = ema15[-1] - ema15[-2] if len(ema15) >= 2 else 0.0
    return {
        "rsi": rsi_val,
        "s5": s5,
        "s15": s15,
        "momentum": momentum,
        "lastRet": last_ret,
        "sigma": sigma,
    }


def sigmoid(z: float) -> float:
    if z < -40:
        return 0.0
    if z > 40:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def predict_simple(feat):
    # نفس الفكرة التقريبية الموجودة في الواجهة
    mu = [50.0, 0.0, 0.0, 0.0, 0.0, 0.003]
    sd = [12.0, 0.5, 0.3, 0.01, 0.005, 0.002]
    W = [0.35, 0.45, 0.25, 0.80, 0.30, -0.15]
    b0 = 0.0

    xvec = [
        feat["rsi"],
        feat["s5"],
        feat["s15"],
        feat["momentum"],
        feat["lastRet"],
        feat["sigma"],
    ]

    z = b0
    for w, xi, mu_i, sd_i in zip(W, xvec, mu, sd):
        z += w * ((xi - mu_i) / (sd_i or 1.0))

    p_up = sigmoid(z)
    # كسر التعادل الخفيف لو p=0.5
    if abs(p_up - 0.5) < 1e-3:
        p_up += (random.random() - 0.5) * 0.02

    direction = "Up" if p_up >= 0.5 else "Down"
    conf = max(0.55, min(0.95, max(p_up, 1.0 - p_up)))
    rng = max(
        0.2,
        min(
            2.0,
            0.8 * (feat["sigma"] * 100.0) + 0.6 * (abs(feat["momentum"]) * 100.0),
        ),
    )
    lo = max(0.10, rng * 0.55)
    hi = rng
    return {
        "direction": direction,
        "confidence": conf,
        "rangePct": [lo, hi],
    }


# ----------------- التعامل مع الملفات -----------------

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def read_last_record(path: Path):
    if not path.exists():
        return None
    try:
        last_line = None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return None
        return json.loads(last_line)
    except Exception as exc:  # noqa: BLE001
        log(f"warn: could not read last record from {path}: {exc}")
        return None


def same_slot(t1_ms: int, t2_ms: int, horizon_min: int) -> bool:
    slot1 = int(t1_ms) // (horizon_min * 60 * 1000)
    slot2 = int(t2_ms) // (horizon_min * 60 * 1000)
    return slot1 == slot2


def write_record(path: Path, obj: dict) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, sort_keys=True) + "\n")


# ----------------- منطق التوقع لكل عملة -----------------

def predict_for_symbol(symbol: str, horizon_min: int) -> None:
    try:
        # نحتاج على الأقل ~60 دقيقة سابقة لعمل المميزات
        limit = max(60, horizon_min + 20)
        candles = fetch_klines_1m(symbol, limit=limit)
        if len(candles) < 20:
            raise RuntimeError(f"too few klines for {symbol}: {len(candles)}")

        closes = [c["c"] for c in candles]
        base_price = closes[-1]
        feat = build_features(closes)

        pred = predict_simple(feat)
        now_ms = int(time.time() * 1000)

        data_dir = Path("data") / symbol
        out_path = data_dir / f"{horizon_min}m.jsonl"

        last_rec = read_last_record(out_path)
        if last_rec and "t" in last_rec and same_slot(last_rec["t"], now_ms, horizon_min):
            log(f"{symbol} {horizon_min}m: already have prediction for this slot, skip")
            return

        lo_pct, hi_pct = pred["rangePct"]
        direction = pred["direction"]
        conf = float(pred["confidence"])

        if direction == "Up":
            price_lo = base_price * (1.0 + lo_pct / 100.0)
            price_hi = base_price * (1.0 + hi_pct / 100.0)
        else:
            price_lo = base_price * (1.0 - hi_pct / 100.0)
            price_hi = base_price * (1.0 - lo_pct / 100.0)

        record = {
            "id": f"{symbol}-{now_ms}-{horizon_min}",
            "t": now_ms,
            "src": "auto",
            "dir": direction,
            "conf": conf,
            "range": [lo_pct, hi_pct],
            "priceLo": price_lo,
            "priceHi": price_hi,
            "base": base_price,
            "horizon": horizon_min,
            "outcome": "Pending",
        }

        write_record(out_path, record)
        log(
            f"{symbol} {horizon_min}m: wrote prediction "
            f"dir={direction} conf={conf:.2f} "
            f"range={lo_pct:.2f}-{hi_pct:.2f}%"
        )

    except Exception as exc:  # noqa: BLE001
        # مهم: لا نوقف باقي العملات، فقط نسجل خطأ
        log(f"ERROR: prediction failed for {symbol} {horizon_min}m: {exc}")


# ----------------- main -----------------

def main():
    symbols = parse_symbols()
    horizons = parse_horizons()
    log(f"starting predict for symbols={symbols} horizons={horizons}")
    for sym in symbols:
        for h in horizons:
            predict_for_symbol(sym, h)
    log("predict done")


if __name__ == "__main__":
    main()
