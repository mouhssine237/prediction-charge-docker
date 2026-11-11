"""Fusionne les CSV de collecte ayant les colonnes attendues, sans doublons."""
import argparse
import csv
from pathlib import Path

FIELDS = ["timestamp_utc", "container", "cpu_percent", "ram_mb", "scenario", "machine_id"]
LEGACY_FIELDS = FIELDS[:-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    merged = {}
    for path in args.inputs:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames not in (FIELDS, LEGACY_FIELDS):
                parser.error(f"Colonnes inattendues dans {path}; attendu: {', '.join(FIELDS)}")
            for row in reader:
                row.setdefault("machine_id", "legacy_unknown")
                key = (row["timestamp_utc"], row["container"], row["scenario"], row["machine_id"])
                merged[key] = row
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(merged.values(), key=lambda r: (r["timestamp_utc"], r["container"])))
    print(f"{len(merged)} lignes fusionnées dans {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
