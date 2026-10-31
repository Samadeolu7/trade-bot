"""Runs hypothesis #7 (see notes/research_log.md) on BTC/USDT 15-minute
candles and prints the walk-forward results. The same research also runs
on the server as an intraday research job.

    python scripts/intraday_research.py                 # both batches
    python scripts/intraday_research.py --batch A       # #2-#5 under the 24/7 session
    python scripts/intraday_research.py --batch B       # existing strategies, 3 treatments

The 15m data (2020-01-01 to 2026-02-28, never the holdout) is downloaded
from Binance's public data API on first run and cached in data/btc15m.pkl.
Batch B takes 20-40 minutes."""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.research import intraday_report as R  # noqa: E402

DATA = ROOT / "data" / "btc15m.pkl"


def load() -> pd.DataFrame:
    if DATA.exists():
        return pd.read_pickle(DATA)
    print("downloading BTC/USDT 15m candles (one-off, a few minutes)...", flush=True)
    start = int(pd.Timestamp("2020-01-01", tz="UTC").timestamp() * 1000)
    end = int(R.HOLDOUT.timestamp() * 1000)
    rows, since = [], start
    while since < end:
        url = ("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=15m"
               f"&startTime={since}&endTime={end - 1}&limit=1000")
        for attempt in range(5):
            try:
                batch = json.load(urllib.request.urlopen(url, timeout=30))
                break
            except Exception:
                time.sleep(2 ** attempt)
        else:
            sys.exit(f"download failed at {pd.Timestamp(since, unit='ms')}")
        if not batch:
            break
        rows += [r[:6] for r in batch if r[0] < end]
        since = batch[-1][0] + 15 * 60 * 1000
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).astype(float)
    df.index = pd.to_datetime(df.pop("t").astype("int64"), unit="ms", utc=True)
    df = df[~df.index.duplicated()]
    DATA.parent.mkdir(exist_ok=True)
    df.to_pickle(DATA)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", choices=["A", "B", "all"], default="all")
    args = ap.parse_args()
    df = load()
    days, folds = R.setup(df)
    print(f"{len(df)} bars, {df.index.min():%Y-%m-%d} to {df.index.max():%Y-%m-%d}; {len(folds)} folds", flush=True)

    def log(msg):
        print(msg, flush=True)

    if args.batch in ("A", "all"):
        print("\n" + R.table(R.TITLES["A"], R.score(R.batch_a(df, days, log), folds)), flush=True)
    if args.batch in ("B", "all"):
        base = yaml.safe_load(open(ROOT / "config" / "config.yaml", encoding="utf-8"))["strategy"]
        print("\n" + R.table(R.TITLES["B"], R.score(R.batch_b(df, base, days, log), folds)), flush=True)


if __name__ == "__main__":
    main()
