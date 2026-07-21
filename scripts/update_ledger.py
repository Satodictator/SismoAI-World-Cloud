from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--world", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--ledger", default="audit/public_ledger.jsonl")
    p.add_argument("--mode", required=True)
    p.add_argument("--run-url", required=True)
    p.add_argument("--source-commit", required=True)
    a = p.parse_args()
    world_path, manifest_path = Path(a.world), Path(a.manifest)
    world = json.loads(world_path.read_text(encoding="utf-8"))
    record = {
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generated_at": world.get("generated_at"),
        "mode": a.mode,
        "model_version": world.get("model_version"),
        "regions_configured": world.get("regions_configured"),
        "regions_operational": world.get("regions_operational"),
        "regions_public_valid": world.get("regions_public_valid"),
        "world_sha256": sha256(world_path),
        "manifest_sha256": sha256(manifest_path),
        "source_commit": a.source_commit,
        "workflow_run": a.run_url,
    }
    ledger = Path(a.ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    previous = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    if line not in previous.splitlines():
        with ledger.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
