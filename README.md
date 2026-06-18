# Garmin FIT → CSV + charts

Converts Garmin `.fit` activity files (Garmin Forerunner 45, etc.) into CSV and
generates one chart per metric (heart rate, speed, altitude, cadence,
distance), plus a static map of the GPS route.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# One file
python fit_to_csv.py 2026-06-18-09-23-01.fit

# Several files at once
python fit_to_csv.py *.fit

# CSV only, no charts
python fit_to_csv.py *.fit --no-plots

# Choose the output directory (default: output/)
python fit_to_csv.py my_activity.fit --outdir results
```

## Outputs (in `output/`)

- `<name>.csv` — one row per GPS sample, with derived columns: `latitude`,
  `longitude`, `speed_kmh`, `pace_min_km`, `elapsed_s`, `moving_time_s`. The
  `cadence` column merges Garmin's integer and `fractional_cadence` parts for
  sub-rpm precision (e.g. `86.5`).
- `<name>_<metric>.png` — one chart per available metric over time.
- `<name>_route.png` — static map of the GPS track, coloured by speed, with
  green start / red finish markers. Skipped for activities without GPS.

The text summary (duration, distance) comes from the `session` record written
by the watch, which is authoritative.

## A note on timestamps

Some files may contain a clock jump (the watch losing GPS time sync): the raw
`timestamp` values are then discontinuous. The CSV keeps these values as-is;
the `moving_time_s` column neutralises jumps and pauses so the charts stay
readable and the duration matches the watch timer.

## References / Links

Python libraries used:

- [fitparse](https://github.com/dtcooper/python-fitparse) — reads the Garmin
  `.fit` binary format
- [pandas](https://pandas.pydata.org/) — data wrangling and CSV export
- [matplotlib](https://matplotlib.org/) — charts and route map

Watch documentation (French):

- [Manuel d'utilisation Forerunner 45/45 Plus — version web](https://www8.garmin.com/manuals/webhelp/forerunner45/FR-FR/index.html)
- [Manuel d'utilisation Forerunner 45/45 Plus — PDF](https://www8.garmin.com/manuals/webhelp/forerunner45/FR-FR/Forerunner_45_45_Plus_OM_FR-FR.pdf)
# Garmin-Forerunner-45
