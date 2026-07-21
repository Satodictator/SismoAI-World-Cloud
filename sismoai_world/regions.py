from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class WorldRegion:
    id: str
    name: str
    group: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    min_magnitude: float = 2.5
    event_magnitude: float = 5.0
    gnss: bool = True
    goes: bool = False
    insar_catalog: bool = True
    max_gnss_stations: int = 18

    @property
    def bbox(self) -> list[float]:
        return [self.min_lat, self.max_lat, self.min_lon, self.max_lon]


def load_regions(path: Path) -> tuple[dict, list[WorldRegion]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    regions = [WorldRegion(**r) for r in raw.get("regions", [])]
    validate_regions(regions)
    return raw, regions


def validate_regions(regions: Iterable[WorldRegion]) -> None:
    seen: set[str] = set()
    count = 0
    for r in regions:
        count += 1
        if not r.id or r.id in seen:
            raise ValueError(f"ID regional inválido o duplicado: {r.id!r}")
        seen.add(r.id)
        if not (-90 <= r.min_lat < r.max_lat <= 90):
            raise ValueError(f"Latitudes inválidas en {r.id}")
        if not (-180 <= r.min_lon < r.max_lon <= 180):
            raise ValueError(f"Longitudes inválidas en {r.id}")
        if not (0 <= r.min_magnitude <= 10 and 0 <= r.event_magnitude <= 10):
            raise ValueError(f"Magnitudes inválidas en {r.id}")
        if r.max_gnss_stations < 0 or r.max_gnss_stations > 100:
            raise ValueError(f"max_gnss_stations inválido en {r.id}")
    if count < 10:
        raise ValueError("El catálogo mundial debe contener al menos 10 regiones")
