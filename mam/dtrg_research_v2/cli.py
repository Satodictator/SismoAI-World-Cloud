from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .dashboard import create_app
from .engine import ResearchEngine


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(description="SismoAI DTRG Research Mode")
    p.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    sub = p.add_subparsers(dest="command", required=True)
    u = sub.add_parser("update")
    u.add_argument("--history-years", type=int)
    u.add_argument("--quick", action="store_true")
    c = sub.add_parser("calculate")
    c.add_argument("--days", type=int, default=365)
    g = sub.add_parser("goes-history")
    g.add_argument("--days", type=int, default=7)
    g.add_argument("--sampling-minutes", type=int, default=10)
    i = sub.add_parser("insar-update")
    i.add_argument("--days", type=int, default=180)
    b = sub.add_parser("backtest")
    b.add_argument("--years", type=int, default=3)
    b.add_argument("--threshold", type=float, default=50.0)
    sub.add_parser("status")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5088)
    sub.add_parser("version")
    a = p.parse_args(argv)
    root = Path(a.project_root)
    if a.command == "version":
        emit({"version": __version__})
        return 0
    eng = ResearchEngine(root)
    if a.command == "update":
        emit(eng.update(history_years=a.history_years, quick=a.quick))
    elif a.command == "calculate":
        emit(eng.calculate(a.days))
    elif a.command == "goes-history":
        emit(eng.goes_history(a.days, a.sampling_minutes))
    elif a.command == "insar-update":
        emit(eng.insar_update(a.days))
    elif a.command == "backtest":
        emit(eng.backtest(a.years, a.threshold))
    elif a.command == "status":
        emit(eng.status())
    elif a.command == "serve":
        create_app(root).run(host=a.host, port=a.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
