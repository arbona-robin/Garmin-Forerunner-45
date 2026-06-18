#!/usr/bin/env python3
"""Convert a Garmin .fit activity file into a CSV and per-metric charts.

Usage:
    python fit_to_csv.py ACTIVITY.fit [--outdir output] [--no-plots]

Produces:
    <outdir>/<name>.csv      one row per record (GPS sample)
    <outdir>/<name>_*.png    one chart per available metric
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from fitparse import FitFile

# Garmin stores lat/long as semicircles; this converts them to degrees.
SEMICIRCLES_TO_DEGREES = 180.0 / 2**31

# Gaps longer than this (seconds) are treated as a pause / clock glitch
# when computing moving time, and collapsed to the typical sampling interval.
GAP_CLAMP_S = 60

# Columns we try to extract from each "record" message, in display order.
RECORD_FIELDS = [
    "timestamp",
    "position_lat",
    "position_long",
    "distance",
    "enhanced_speed",
    "speed",
    "enhanced_altitude",
    "altitude",
    "heart_rate",
    "cadence",
    "fractional_cadence",
    "temperature",
    "power",
]


def read_records(fit_path: Path) -> pd.DataFrame:
    """Read all 'record' messages from a FIT file into a DataFrame."""
    fit = FitFile(str(fit_path))
    rows = []
    for record in fit.get_messages("record"):
        values = {d.name: d.value for d in record}
        rows.append({field: values.get(field) for field in RECORD_FIELDS})

    if not rows:
        raise ValueError(f"No 'record' data found in {fit_path}")

    df = pd.DataFrame(rows)
    return enrich(df)


def read_session(fit_path: Path) -> dict:
    """Read the device's own activity summary (authoritative totals)."""
    fit = FitFile(str(fit_path))
    for session in fit.get_messages("session"):
        return {d.name: d.value for d in session}
    return {}


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Add human-friendly derived columns and drop empty ones."""
    # Convert GPS semicircles to decimal degrees.
    if "position_lat" in df:
        df["latitude"] = df["position_lat"] * SEMICIRCLES_TO_DEGREES
    if "position_long" in df:
        df["longitude"] = df["position_long"] * SEMICIRCLES_TO_DEGREES
    df = df.drop(columns=["position_lat", "position_long"], errors="ignore")

    # Prefer the "enhanced" variants when present.
    if "enhanced_speed" in df:
        df["speed"] = df["enhanced_speed"].combine_first(df.get("speed"))
    if "enhanced_altitude" in df:
        df["altitude"] = df["enhanced_altitude"].combine_first(df.get("altitude"))
    df = df.drop(columns=["enhanced_speed", "enhanced_altitude"], errors="ignore")

    # Garmin stores cadence as an integer plus a separate fractional part;
    # combine them for sub-rpm precision (e.g. 86 + 0.5 -> 86.5).
    if "cadence" in df and "fractional_cadence" in df:
        df["cadence"] = df["cadence"].fillna(0) + df["fractional_cadence"].fillna(0)
    df = df.drop(columns=["fractional_cadence"], errors="ignore")

    # Speed in km/h and pace in min/km are easier to read than m/s.
    if "speed" in df:
        df["speed_kmh"] = df["speed"] * 3.6
        df["pace_min_km"] = df["speed"].apply(
            lambda s: (1000.0 / s) / 60.0 if s and s > 0 else None
        )

    # Seconds elapsed since the start of the activity.
    if "timestamp" in df:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["elapsed_s"] = (df["timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds()
        # "Moving time": same clock, but any gap larger than GAP_CLAMP_S
        # (a pause, or a device clock jump) is collapsed to the typical
        # sampling interval. This keeps charts readable and the duration
        # close to the device's own timer, while the raw timestamp/elapsed_s
        # columns stay untouched.
        dt = df["timestamp"].diff().dt.total_seconds().fillna(0)
        step = dt[(dt > 0) & (dt <= GAP_CLAMP_S)].median()
        step = step if pd.notna(step) else 1.0
        df["moving_time_s"] = dt.where(dt <= GAP_CLAMP_S, step).cumsum()

    # Drop columns that are entirely empty (e.g. no power meter / cadence).
    df = df.dropna(axis=1, how="all")
    return df


def print_summary(df: pd.DataFrame, session: dict | None = None) -> None:
    """Print a short text summary of the activity."""
    session = session or {}
    sport = session.get("sport")
    print(f"Records: {len(df)}" + (f" | Sport: {sport}" if sport else ""))

    # Prefer the device's own timer/distance; fall back to record-derived.
    timer = session.get("total_timer_time")
    if timer:
        print(f"Duration: {timer / 60:.1f} min (device timer)")
    else:
        duration_col = "moving_time_s" if "moving_time_s" in df else "elapsed_s"
        if duration_col in df:
            print(f"Duration: {df[duration_col].iloc[-1] / 60:.1f} min")
    distance = session.get("total_distance")
    if distance:
        print(f"Distance: {distance / 1000:.2f} km")
    elif "distance" in df:
        print(f"Distance: {df['distance'].iloc[-1] / 1000:.2f} km")
    if "speed_kmh" in df:
        print(f"Avg speed: {df['speed_kmh'].mean():.1f} km/h "
              f"(max {df['speed_kmh'].max():.1f})")
    if "heart_rate" in df:
        print(f"Heart rate: avg {df['heart_rate'].mean():.0f} "
              f"max {df['heart_rate'].max():.0f} bpm")
    if "altitude" in df:
        print(f"Altitude: {df['altitude'].min():.0f}-{df['altitude'].max():.0f} m")


# Metrics we know how to chart, with axis labels.
PLOTTABLE = {
    "heart_rate": "Heart rate (bpm)",
    "speed_kmh": "Speed (km/h)",
    "altitude": "Altitude (m)",
    "cadence": "Cadence (rpm)",
    "power": "Power (W)",
    "distance": "Distance (m)",
}


def make_plots(df: pd.DataFrame, outdir: Path, stem: str) -> list[Path]:
    """Generate one PNG chart per available metric over elapsed time."""
    import matplotlib
    matplotlib.use("Agg")  # headless backend, no display needed
    import matplotlib.pyplot as plt

    time_col = "moving_time_s" if "moving_time_s" in df else "elapsed_s"
    if time_col not in df:
        print("No timestamp available; skipping plots.", file=sys.stderr)
        return []

    x = df[time_col] / 60.0  # minutes
    written = []
    for col, label in PLOTTABLE.items():
        if col not in df or df[col].dropna().empty:
            continue
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(x, df[col], linewidth=1.2)
        ax.set_xlabel("Time (min)")
        ax.set_ylabel(label)
        ax.set_title(label)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = outdir / f"{stem}_{col}.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)
    return written


def make_route_plot(df: pd.DataFrame, outdir: Path, stem: str) -> Path | None:
    """Draw the GPS route as a static PNG, coloured by speed when available."""
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import numpy as np

    if "latitude" not in df or "longitude" not in df:
        print("No GPS data; skipping route plot.", file=sys.stderr)
        return None

    gps = df.dropna(subset=["latitude", "longitude"])
    if len(gps) < 2:
        print("Not enough GPS points; skipping route plot.", file=sys.stderr)
        return None

    lat = gps["latitude"].to_numpy()
    lon = gps["longitude"].to_numpy()

    fig, ax = plt.subplots(figsize=(8, 8))

    if "speed_kmh" in gps and not gps["speed_kmh"].dropna().empty:
        # Build coloured segments so the line shows speed along the route.
        points = np.array([lon, lat]).T.reshape(-1, 1, 2)
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        speed = gps["speed_kmh"].to_numpy()
        lc = LineCollection(segments, cmap="viridis", linewidth=2)
        lc.set_array(speed[:-1])
        ax.add_collection(lc)
        fig.colorbar(lc, ax=ax, label="Speed (km/h)", shrink=0.7)
    else:
        ax.plot(lon, lat, color="tab:blue", linewidth=2)

    # Start (green) and finish (red) markers.
    ax.plot(lon[0], lat[0], "o", color="green", markersize=9, label="Start")
    ax.plot(lon[-1], lat[-1], "o", color="red", markersize=9, label="Finish")

    # Correct the aspect ratio so the track is not horizontally squashed:
    # one degree of longitude is shorter than one of latitude away from equator.
    ax.set_aspect(1.0 / math.cos(math.radians(float(np.mean(lat)))))
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Route")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    ax.margins(0.05)
    fig.tight_layout()

    path = outdir / f"{stem}_route.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fit_files", type=Path, nargs="+",
                        help="One or more .fit files")
    parser.add_argument("--outdir", type=Path, default=Path("output"),
                        help="Output directory (default: output)")
    parser.add_argument("--no-plots", action="store_true",
                        help="Only write the CSV, skip charts")
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    failures = 0

    for fit_file in args.fit_files:
        if not fit_file.exists():
            print(f"File not found: {fit_file}", file=sys.stderr)
            failures += 1
            continue

        stem = fit_file.stem
        print(f"\n=== {fit_file.name} ===")
        try:
            df = read_records(fit_file)
        except ValueError as exc:
            print(f"Skipped: {exc}", file=sys.stderr)
            failures += 1
            continue

        csv_path = args.outdir / f"{stem}.csv"
        df.to_csv(csv_path, index=False)
        print(f"Wrote {csv_path}")

        print_summary(df, read_session(fit_file))

        if not args.no_plots:
            for path in make_plots(df, args.outdir, stem):
                print(f"Wrote {path}")
            route = make_route_plot(df, args.outdir, stem)
            if route:
                print(f"Wrote {route}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
