"""Collecte les métriques CPU/RAM cAdvisor et étiquette les scénarios contrôlés."""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ["timestamp_utc", "container", "cpu_percent", "ram_mb", "scenario", "machine_id"]
SCENARIOS = [
    ("charge_faible", 0.15), ("charge_moyenne", 0.45), ("charge_forte", 0.85),
    ("montee_progressive", 0.30), ("montee_progressive", 0.60), ("montee_progressive", 0.90),
    ("descente_progressive", 0.90), ("descente_progressive", 0.60), ("descente_progressive", 0.30),
    ("pics", 0.20), ("pics", 0.90), ("pics", 0.20),
    ("cycle_periodique", 0.20), ("cycle_periodique", 0.60), ("cycle_periodique", 0.90),
    ("cycle_periodique", 0.60), ("cycle_periodique", 0.20),
    ("charge_variable", 0.35), ("charge_variable", 0.75), ("charge_variable", 0.50),
]


def request_json(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Impossible de joindre {url}: {exc}") from exc


def prom_query_range(base_url, expression, start, end, step):
    params = urllib.parse.urlencode({"query": expression, "start": start, "end": end, "step": step})
    payload = request_json(f"{base_url.rstrip('/')}/api/v1/query_range?{params}")
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus a refusé la requête: {payload}")
    results = payload.get("data", {}).get("result", [])
    if not results:
        return {}
    series = next((item for item in results if item.get("metric", {}).get("name")), results[0])
    return {int(float(ts)): float(value) for ts, value in series.get("values", [])
            if value not in ("NaN", "+Inf", "-Inf")}


def merge_csv(output, rows):
    output.parent.mkdir(parents=True, exist_ok=True)
    items = {}
    if output.exists() and output.stat().st_size:
        with output.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != FIELDS:
                raise RuntimeError(f"Colonnes CSV inattendues; attendu: {', '.join(FIELDS)}")
            for row in reader:
                if "machine_id" not in row:
                    row["machine_id"] = "legacy_unknown"
                items[(row["timestamp_utc"], row["container"], row["scenario"], row["machine_id"])] = row
    before = len(items)
    for row in rows:
        items[(row["timestamp_utc"], row["container"], row["scenario"], row["machine_id"])] = row
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(items.values(), key=lambda r: (r["timestamp_utc"], r["container"])))
    return len(items) - before


def collect_window(args, start, end, scenario):
    name = json.dumps(args.container)
    cpu_expr = f'rate(container_cpu_usage_seconds_total{{job="cadvisor",name={name},cpu="total"}}[1m]) * 100'
    ram_expr = f'container_memory_working_set_bytes{{job="cadvisor",name={name}}} / 1024 / 1024'
    cpu = prom_query_range(args.prometheus, cpu_expr, start, end, args.step)
    ram = prom_query_range(args.prometheus, ram_expr, start, end, args.step)
    ts = sorted(set(cpu) & set(ram))
    rows = [{"timestamp_utc": datetime.fromtimestamp(t, timezone.utc).isoformat(),
             "container": args.container, "cpu_percent": f"{cpu[t]:.6f}",
             "ram_mb": f"{ram[t]:.6f}", "scenario": scenario,
             "machine_id": args.machine_id} for t in ts]
    if rows:
        merge_csv(args.output, rows)
    return rows


def run_scenarios(args):
    app = args.app_url.rstrip("/")
    print("Scénarios : faible, moyenne, forte, montée/descente, pics, cycle périodique, charge variable.")
    print("Le service charge-demo-app sera piloté via POST /load.")
    total = 0
    for cycle in range(1, args.repeats + 1):
      print(f"Cycle {cycle}/{args.repeats}")
      for index, (label, level) in enumerate(SCENARIOS, 1):
        request_json(f"{app}/load", json.dumps({"level": level}).encode())
        print(f"[{index}/{len(SCENARIOS)}] {label}: charge={level:.2f}, palier {args.stage_seconds}s")
        time.sleep(args.stage_seconds)
        end = int(datetime.now(timezone.utc).timestamp())
        rows = collect_window(args, end - args.stage_seconds, end, label)
        total += len(rows)
        print(f"  mesures synchronisées: {len(rows)} (CSV: {args.output})")
    request_json(f"{app}/load", json.dumps({"level": args.restore_level}).encode())
    print(f"Terminé : {total} mesures collectées. Charge restaurée à {args.restore_level:.2f}.")


def main():
    default_output = Path(__file__).resolve().parents[1] / "data" / "raw" / "container_metrics.csv"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prometheus", default=os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    p.add_argument("--container", default="charge-demo-app")
    p.add_argument("--machine-id", default=os.getenv("MACHINE_ID", "pc_local"), help="Identifiant unique de la machine pour distinguer ses données")
    p.add_argument("--hours", type=float, default=1)
    p.add_argument("--step", type=int, default=15)
    p.add_argument("--scenario", default="historique_non_etiquete")
    p.add_argument("--output", type=Path, default=default_output)
    p.add_argument("--scenarios", action="store_true", help="Appliquer et collecter la séquence de scénarios")
    p.add_argument("--app-url", default="http://localhost:8000")
    p.add_argument("--stage-seconds", type=int, default=180, help="Durée de chaque palier (minimum conseillé: 180s)")
    p.add_argument("--repeats", type=int, default=1, help="Nombre de répétitions de la séquence complète")
    p.add_argument("--restore-level", type=float, default=0.35)
    a = p.parse_args()
    if a.step < 1 or a.hours <= 0 or a.stage_seconds < 60 or a.repeats < 1 or not 0 <= a.restore_level <= 1:
        p.error("--step >=1, --hours >0, --stage-seconds >=60, --repeats >=1 et --restore-level entre 0 et 1")
    try:
        if a.scenarios:
            run_scenarios(a)
        else:
            end = int(datetime.now(timezone.utc).timestamp())
            rows = collect_window(a, end - int(a.hours * 3600), end, a.scenario)
            if not rows:
                raise RuntimeError(f"Aucune série CPU/RAM commune pour {a.container}; vérifiez cAdvisor et Prometheus.")
            print(f"Échantillons synchronisés: {len(rows)}; CSV: {a.output.resolve()}")
            print(f"Période UTC: {rows[0]['timestamp_utc']} -> {rows[-1]['timestamp_utc']}")
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
