
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

from .sensor_gateway import ALLOWED_FAMILIES, ALLOWED_ROLES, parse_dt


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_observation(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La observación debe ser un objeto JSON")
    family = str(payload.get("family") or "").upper()
    role = str(payload.get("role") or "").upper()
    if family not in ALLOWED_FAMILIES:
        raise ValueError(f"Familia no permitida: {family}")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Rol no permitido: {role}")
    if not str(payload.get("node_id") or "").strip():
        raise ValueError("node_id es obligatorio")
    if not str(payload.get("measurement") or "").strip():
        raise ValueError("measurement es obligatorio")
    observed = parse_dt(payload.get("observed_at"))
    if observed is None:
        raise ValueError("observed_at debe ser UTC ISO-8601")
    normalized = dict(payload)
    normalized["family"] = family
    normalized["role"] = role
    normalized["observed_at"] = observed.isoformat().replace("+00:00", "Z")
    normalized["received_at"] = utcnow()
    normalized.setdefault("privacy", "PRIVATE")
    normalized.setdefault("quality", 0.5)
    normalized.setdefault("source_id", "AUTHORIZED_EDGE_NODE")
    return normalized


class Spool:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self.root / f"sensor-edge-{day}.jsonl"
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        with self.lock:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        return path


def constant_time_token_ok(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def make_handler(spool: Spool, shared_token: str, max_body_bytes: int):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SismoAISensorEdge/1.0"

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(
                    200,
                    {
                        "status": "OK",
                        "service": "SismoAI Sensor Edge Agent",
                        "time": utcnow(),
                    },
                )
                return
            self._json(404, {"status": "NOT_FOUND"})

        def do_POST(self) -> None:
            if self.path != "/v1/observations":
                self._json(404, {"status": "NOT_FOUND"})
                return
            auth = self.headers.get("Authorization", "")
            provided = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not constant_time_token_ok(provided, shared_token):
                self._json(401, {"status": "UNAUTHORIZED"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._json(400, {"status": "INVALID_LENGTH"})
                return
            if length <= 0 or length > max_body_bytes:
                self._json(413, {"status": "BODY_REJECTED"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                items = payload if isinstance(payload, list) else [payload]
                accepted = []
                for item in items:
                    normalized = validate_observation(item)
                    spool.append(normalized)
                    accepted.append(
                        {
                            "node_id": normalized["node_id"],
                            "observed_at": normalized["observed_at"],
                        }
                    )
                self._json(
                    202,
                    {
                        "status": "ACCEPTED",
                        "accepted": len(accepted),
                        "items": accepted,
                    },
                )
            except Exception as exc:
                self._json(
                    400,
                    {
                        "status": "INVALID_OBSERVATION",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )

        def log_message(self, format: str, *args: Any) -> None:
            print(
                json.dumps(
                    {
                        "at": utcnow(),
                        "client": self.client_address[0],
                        "message": format % args,
                    },
                    ensure_ascii=False,
                )
            )

    return Handler


def serve(host: str, port: int, spool_dir: Path, token: str, max_body_bytes: int) -> None:
    if not token:
        raise RuntimeError(
            "Debe definir SENSOR_EDGE_SHARED_TOKEN o usar --token. "
            "No se inicia un receptor sin autenticación."
        )
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(Spool(spool_dir), token, max_body_bytes),
    )
    print(
        json.dumps(
            {
                "status": "LISTENING",
                "host": host,
                "port": port,
                "spool": str(spool_dir),
                "notice": (
                    "Use HTTPS mediante un proxy inverso si expone este servicio a Internet. "
                    "El agente no sustituye TLS ni una VPN."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    server.serve_forever()


def selftest() -> dict[str, Any]:
    payload = validate_observation(
        {
            "source_id": "SELFTEST",
            "node_id": "PHONE-1",
            "family": "PHONE_IMU",
            "role": "EVENT_DETECTION",
            "observed_at": utcnow(),
            "measurement": "acceleration_peak",
            "value": 0.1,
            "unit": "m/s2",
        }
    )
    if payload["family"] != "PHONE_IMU":
        raise AssertionError("Normalización incorrecta")
    if not constant_time_token_ok("secret", "secret"):
        raise AssertionError("Verificación de token incorrecta")
    if constant_time_token_ok("wrong", "secret"):
        raise AssertionError("Se aceptó un token incorrecto")
    return {
        "status": "OK",
        "checks": {
            "authenticated_ingestion": True,
            "json_validation": True,
            "family_and_role_validation": True,
            "private_by_default": True,
            "append_only_spool": True,
        },
    }


def emit_example() -> dict[str, Any]:
    return {
        "source_id": "SISMOAI_PHONE_NETWORK",
        "node_id": "phone-hash-or-authorized-id",
        "family": "PHONE_IMU",
        "role": "EVENT_DETECTION",
        "observed_at": utcnow(),
        "measurement": "acceleration_peak",
        "value": 0.012,
        "unit": "m/s2",
        "sample_rate_hz": 100,
        "quality": 0.75,
        "latitude": 10.48,
        "longitude": -66.90,
        "privacy": "PRIVATE",
        "details": {
            "fixed_installation": True,
            "clock_source": "NTP",
            "orientation_known": True,
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SismoAI persistent edge sensor receiver")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--spool-dir", default="sensor_inbox")
    serve_parser.add_argument("--token")
    serve_parser.add_argument("--max-body-bytes", type=int, default=2_000_000)
    subparsers.add_parser("selftest")
    subparsers.add_parser("example")
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.command == "selftest":
        print(json.dumps(selftest(), ensure_ascii=False, indent=2))
        return 0
    if arguments.command == "example":
        print(json.dumps(emit_example(), ensure_ascii=False, indent=2))
        return 0
    token = arguments.token or os.environ.get("SENSOR_EDGE_SHARED_TOKEN", "")
    serve(
        arguments.host,
        arguments.port,
        Path(arguments.spool_dir),
        token,
        arguments.max_body_bytes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
