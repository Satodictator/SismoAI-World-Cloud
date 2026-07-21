from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


def copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--previous", required=True)
    p.add_argument("--collected", required=True)
    p.add_argument("--world", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--retention-days", type=int, default=180)
    a = p.parse_args()
    previous, collected, output = Path(a.previous), Path(a.collected), Path(a.output)
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    copy_tree(previous, output)
    copy_tree(collected, output)
    world = json.loads(Path(a.world).read_text(encoding="utf-8"))
    stamp = str(world.get("generated_at") or datetime.now(timezone.utc).isoformat()).replace(":", "-")
    hist = output / "history" / "world" / f"{stamp}.json"
    hist.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.world, hist)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(7, a.retention_days))
    for path in (output / "history" / "world").glob("*.json"):
        try:
            when = datetime.fromisoformat(path.stem.replace("Z", "+00:00").replace("-00-", ":00:"))
        except Exception:
            when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if when < cutoff:
            path.unlink(missing_ok=True)
    print(json.dumps({"status": "OK", "output": str(output), "history": str(hist)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
