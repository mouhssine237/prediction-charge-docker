"""Application de démonstration qui expose une charge contrôlable et des métriques."""
import asyncio
import math
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

load_level = float(os.getenv("LOAD_LEVEL", "0.35"))
cpu_metric = Gauge("app_cpu_percent", "CPU simulé de l'application (pourcent)")
memory_metric = Gauge("app_memory_mb", "Mémoire simulée de l'application (MiB)")
load_metric = Gauge("app_load_level", "Niveau de charge demandé, de 0 à 1")
requests_metric = Gauge("app_requests_per_second", "Débit simulé de requêtes par seconde")
started_at = time.time()


async def update_metrics():
    tick = 0
    while True:
        # Le signal combine le niveau de charge choisi, une tendance et un bruit léger.
        wave = 0.12 * math.sin(tick / 5) + 0.07 * math.sin(tick / 13)
        cpu = min(98.0, max(2.0, 8 + 78 * load_level + 100 * wave + random.uniform(-3, 3)))
        memory = max(64.0, 110 + 420 * load_level + 25 * math.sin(tick / 18) + random.uniform(-4, 4))
        cpu_metric.set(cpu)
        memory_metric.set(memory)
        load_metric.set(load_level)
        requests_metric.set(5 + 95 * load_level + random.uniform(-2, 2))
        tick += 1
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(update_metrics())
    yield
    task.cancel()


app = FastAPI(title="Charge Demo", version="1.0.0", lifespan=lifespan)


class LoadRequest(BaseModel):
    level: float = Field(ge=0, le=1, description="Charge simulée entre 0 (faible) et 1 (forte)")


@app.get("/")
def home():
    return {
        "service": "application de démonstration",
        "load_level": load_level,
        "endpoints": {"health": "/health", "metrics": "/metrics", "set_load": "POST /load"},
        "usage": "POST /load avec le JSON {\"level\": 0.8} pour simuler une hausse de charge",
    }


@app.get("/health")
def health():
    return {"status": "ok", "uptime_seconds": round(time.time() - started_at, 1)}


@app.post("/load")
def set_load(request: LoadRequest):
    global load_level
    load_level = request.level
    return {"load_level": load_level, "message": "Niveau de charge mis à jour"}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
