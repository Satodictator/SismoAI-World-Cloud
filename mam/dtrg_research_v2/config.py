from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ResearchConfig:
    region_id: str = "venezuela"
    min_lat: float = 0.0
    max_lat: float = 15.0
    min_lon: float = -76.0
    max_lon: float = -57.0
    min_magnitude: float = 2.5
    baseline_days: int = 180
    score_window_days: int = 14
    history_years: int = 5
    gnss_history_years: int = 5
    max_gnss_stations: int = 25
    goes_recent_hours: int = 12
    goes_sampling_minutes: int = 10
    insar_catalog_days: int = 180
    insar_auto_download_max: int = 3
    backtest_horizon_days: int = 7
    backtest_event_magnitude: float = 5.0
    public_min_confidence: float = 0.75
    public_min_families: int = 3
    public_min_backtest_positives: int = 10
    public_min_auc: float = 0.58
    public_max_brier_ratio: float = 0.98
    family_weights: dict[str, float] = field(default_factory=lambda: {
        "seismic": 0.50,
        "gnss": 0.22,
        "insar": 0.18,
        "goes_lightning_control": 0.10,
    })
    licsar_frames: list[str] = field(default_factory=list)
    earthdata_token: str = ""

    @classmethod
    def load(cls, root: Path) -> "ResearchConfig":
        path = Path(root) / "config" / "dtrg_research.json"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            obj = cls()
            path.write_text(json.dumps(asdict(obj), ensure_ascii=False, indent=2), encoding="utf-8")
            return obj
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def bbox(self):
        return self.min_lon, self.min_lat, self.max_lon, self.max_lat
