"""Refresh Gemini S/R levels for the universe, through the Batch API.

Two phases, because a batch job is asynchronous:

    refresh_levels.py --timeframe daily --submit
    refresh_levels.py --timeframe daily --collect

Cron runs submit on Saturday and collect a few hours later. The job name is kept
in the meta table, so collect needs no argument beyond the timeframe.

Bars come from the Phase 1 store; this job never touches the price network.
"""
import argparse
from datetime import date
from pathlib import Path

import level_batch
import pivots
import sr_major
import sr_parse
import store
import timeframes
from monitor_config import MONITOR

_JOB_KEY = "levels_job_{timeframe}"
_DONE_STATES = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"}
_DEAD_STATES = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}


def submit(conn, timeframe, tickers, image_dir, limit=None):
    """Render every chart, submit one batch, record the job name."""
    if limit is not None:
        tickers = tickers[:limit]

    requests, included, skipped = [], [], []
    for ticker in tickers:
        daily = store.read_bars(conn, ticker)
        req = level_batch.build_request(ticker, daily, timeframe, image_dir)
        if req is None:
            skipped.append(ticker)
            continue
        requests.append(req)
        included.append(ticker)

    if not requests:
        raise ValueError("no ticker had enough history to build a request")

    cfg = timeframes.config_for(timeframe)
    name = level_batch.submit(
        requests, cfg.gemini_model,
        f"sr-levels-{timeframe}-{date.today().isoformat()}")
    store.set_meta(conn, _JOB_KEY.format(timeframe=timeframe), name)
    return name, included, skipped


def rows_for(levels, extras):
    """Level objects plus sr_major extras -> store rows."""
    return [{
        "price": lv.price, "kind": lv.kind, "strength": lv.strength,
        "touches": lv.touches, "episodes": extras[i].get("episodes", 0),
        "score": extras[i].get("major_score", 0.0),
        "is_major": extras[i].get("computed_major", False),
        "is_primary": extras[i].get("computed_primary", False),
        "nearest_major_support": extras[i].get("nearest_major_support", False),
        "reasoning": extras[i].get("reasoning", ""),
    } for i, lv in enumerate(levels)]


def collect(conn, timeframe, job_name=None):
    """Parse a finished job into the levels table. Returns a status dict."""
    key = _JOB_KEY.format(timeframe=timeframe)
    job_name = job_name or store.get_meta(conn, key)
    if not job_name:
        raise ValueError(f"no recorded job for {timeframe}; run --submit first")

    job = level_batch.poll(job_name)
    state = str(getattr(job.state, "name", job.state))
    if state in _DEAD_STATES:
        raise RuntimeError(f"batch {job_name} ended {state}: {job.error}")
    if state not in _DONE_STATES:
        return {"state": state, "written": 0, "tickers": 0, "empty": []}

    texts = level_batch.responses_by_ticker(
        getattr(job.dest, "inlined_responses", None))

    today = date.today().isoformat()
    written, empty = 0, []
    for ticker, raw in texts.items():
        daily = store.read_bars(conn, ticker)
        df = level_batch.frame_for(daily, timeframe)
        cfg = timeframes.config_for(timeframe, df)
        parsed = sr_parse.parse_sr_response(raw, df, cfg)
        levels, extras = sr_parse.build_levels(
            parsed, df, pivots.zigzag_pivots(df, cfg), cfg)
        levels, extras, _, _ = sr_major.annotate(df, levels, extras, cfg)

        n = store.replace_levels(conn, ticker, timeframe,
                                 rows_for(levels, extras), today)
        written += n
        if n == 0:
            empty.append(ticker)

    store.set_meta(conn, f"levels_collected_{timeframe}", today)
    return {"state": state, "written": written, "tickers": len(texts), "empty": empty}


def main():
    ap = argparse.ArgumentParser(description="Refresh Gemini S/R levels by batch.")
    ap.add_argument("--timeframe", choices=sorted(timeframes.TIMEFRAMES), required=True)
    ap.add_argument("--db", default=str(MONITOR.db_path))
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--job", default=None, help="Collect a specific job name")
    ap.add_argument("--tickers", nargs="+", default=None,
                    help="Named tickers instead of the stored universe")
    ap.add_argument("--limit", type=int, default=None, help="First N tickers only")
    ap.add_argument("--image-dir", default=None,
                    help="Where chart PNGs go (default: output_charts/<timeframe>)")
    args = ap.parse_args()

    if args.submit == args.collect:
        ap.error("choose exactly one of --submit or --collect")

    conn = store.init_db(store.connect(args.db))

    if args.submit:
        # Everything this project writes stays inside the project. A temp
        # directory would scatter run artifacts across /tmp and lose the charts
        # that were actually sent to the model.
        image_dir = Path(args.image_dir) if args.image_dir else Path(
            "output_charts") / args.timeframe
        image_dir.mkdir(parents=True, exist_ok=True)
        tickers = args.tickers or store.active_universe(conn)
        name, included, skipped = submit(
            conn, args.timeframe, tickers, image_dir, args.limit)
        print(f"submitted {len(included)} request(s) as {name}")
        print(f"charts in {image_dir}")
        if skipped:
            print(f"skipped {len(skipped)} with too little history: "
                  f"{', '.join(skipped[:10])}")
    else:
        result = collect(conn, args.timeframe, args.job)
        if result["state"] not in _DONE_STATES:
            print(f"job still {result['state']} — try again later")
            return
        print(f"{result['state']}: {result['written']} level(s) "
              f"across {result['tickers']} ticker(s)")
        if result["empty"]:
            print(f"parsed to nothing, previous levels kept: "
                  f"{', '.join(result['empty'])}")


if __name__ == "__main__":
    main()
