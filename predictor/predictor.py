"""Prévoit la CPU et la RAM réelles de charge-demo-app à partir de cAdvisor."""
import os
import time
import logging

import numpy as np
import requests
from prometheus_client import Gauge, start_http_server
from sklearn.ensemble import RandomForestRegressor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
PROM_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))
FORECAST_SECONDS = int(os.getenv("FORECAST_SECONDS", "60"))
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "charge-demo-app")
STEP = 15
LAG_POINTS = 8
cpu_forecast = Gauge("predicted_cpu_percent", "CPU réel du conteneur prévu à l'horizon configuré")
memory_forecast = Gauge("predicted_memory_mb", "RAM réelle du conteneur prévue à l'horizon configuré")
forecast_ok = Gauge("prediction_model_ready", "1 si le modèle ML a pu être utilisé")
forecast_horizon = Gauge("prediction_horizon_seconds", "Horizon de prévision en secondes")
forecast_error = Gauge("prediction_last_error", "1 si la dernière collecte/prédiction a échoué")
history_points = Gauge("prediction_history_points", "Nombre de points cAdvisor utilisés pour chaque modèle")

CPU_QUERY = (
    'rate(container_cpu_usage_seconds_total{job="cadvisor",'
    f'name="{CONTAINER_NAME}",cpu="total"}}[1m]) * 100'
)
MEMORY_QUERY = (
    'container_memory_working_set_bytes{job="cadvisor",'
    f'name="{CONTAINER_NAME}"}} / 1024 / 1024'
)


def query_range(metric: str) -> list[tuple[float, float]]:
    end = int(time.time())
    start = end - 30 * 60
    response = requests.get(
        f"{PROM_URL}/api/v1/query_range",
        params={"query": metric, "start": start, "end": end, "step": STEP},
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("data", {}).get("result", [])
    if not results:
        return []
    # Le sélecteur name doit produire exactement la série du conteneur cible.
    series = next(
        (item for item in results if item.get("metric", {}).get("name") == CONTAINER_NAME),
        None,
    )
    if series is None:
        return []
    return [
        (float(point[0]), float(point[1]))
        for point in series.get("values", [])
        if point[1] not in ("NaN", "+Inf", "-Inf")
    ]


def forecast(samples: list[tuple[float, float]], steps: int) -> tuple[float, bool]:
    values = [value for _, value in samples]
    if len(values) < LAG_POINTS + 8:
        return (values[-1], False) if values else (0.0, False)
    series = np.asarray(values, dtype=float)
    x, y = [], []
    for index in range(LAG_POINTS, len(series)):
        x.append(series[index - LAG_POINTS:index])
        y.append(series[index])
    model = RandomForestRegressor(n_estimators=80, max_depth=8, random_state=42, n_jobs=1)
    model.fit(np.asarray(x), np.asarray(y))
    window = series[-LAG_POINTS:].copy()
    for _ in range(max(1, steps)):
        next_value = float(model.predict(window.reshape(1, -1))[0])
        window = np.append(window[1:], next_value)
    return float(window[-1]), True


def main():
    start_http_server(9100)
    forecast_horizon.set(FORECAST_SECONDS)
    logging.info("Exporter prédictif démarré; Prometheus=%s", PROM_URL)
    while True:
        try:
            cpu = query_range(CPU_QUERY)
            memory = query_range(MEMORY_QUERY)
            steps = max(1, round(FORECAST_SECONDS / STEP))
            cpu_value, cpu_ml = forecast(cpu, steps)
            memory_value, memory_ml = forecast(memory, steps)
            cpu_forecast.set(cpu_value)
            memory_forecast.set(memory_value)
            forecast_ok.set(1 if cpu_ml and memory_ml else 0)
            forecast_error.set(0)
            history_points.set(min(len(cpu), len(memory)))
            logging.info("Prévision +%ss: CPU %.1f%%, RAM %.1f MiB (n=%d)", FORECAST_SECONDS, cpu_value, memory_value, min(len(cpu), len(memory)))
        except Exception:
            forecast_error.set(1)
            logging.exception("Échec de la collecte ou de la prédiction")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
