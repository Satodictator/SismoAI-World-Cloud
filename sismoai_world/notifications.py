from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCIENTIFIC_NOTICE = (
    "Aviso privado experimental de SismoAI. No constituye una predicción sísmica, "
    "una alerta oficial ni una orden de evacuación."
)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def latest_event(region: dict[str, Any]) -> dict[str, Any]:
    event = region.get("latest_event") or {}
    return {
        "event_time": event.get("event_time"),
        "magnitude": as_float(event.get("magnitude"), 0.0),
        "place": event.get("place") or event.get("title") or "",
    }


def snapshot(world: dict[str, Any]) -> dict[str, Any]:
    regions: dict[str, Any] = {}
    for item in world.get("ranking") or []:
        rid = str(item.get("region_id") or "")
        if not rid:
            continue
        event = latest_event(item)
        regions[rid] = {
            "region_name": item.get("region_name") or rid,
            "state": item.get("state") or "NO_DATA",
            "iedc": as_float(item.get("iedc_provisional"), 0.0),
            "confidence": as_float(item.get("confidence"), 0.0),
            "coverage": as_float(item.get("coverage"), 0.0),
            "data_quality": as_float(item.get("data_quality"), 0.0),
            "event_time": event.get("event_time"),
            "event_magnitude": event.get("magnitude"),
            "event_place": event.get("place"),
        }
    return regions


def candidate_message(candidate: dict[str, Any]) -> str:
    kind = candidate["kind"]
    lines = [
        "SISMOAI — AVISO EXPERIMENTAL PRIVADO",
        "",
        f"Tipo: {candidate.get('label', kind)}",
        f"Región: {candidate.get('region_name', candidate.get('region_id', '—'))}",
    ]
    if kind == "STATE_CHANGE":
        lines += [
            f"Estado experimental: {candidate.get('state')}",
            f"IEDC provisional: {candidate.get('iedc'):.1f}",
            f"Confianza: {candidate.get('confidence') * 100:.1f} %",
            f"Cobertura: {candidate.get('coverage') * 100:.1f} %",
            f"Calidad: {candidate.get('data_quality') * 100:.1f} %",
            f"Motivo: {candidate.get('reason')}",
        ]
    elif kind == "OBSERVED_EVENT":
        lines += [
            f"Actividad observada: M{candidate.get('magnitude'):.1f}",
            f"Fecha registrada: {candidate.get('event_time') or '—'}",
            f"Lugar informado: {candidate.get('place') or '—'}",
            "Este evento ya fue observado; no es una predicción futura.",
        ]
    elif kind == "SHADOW_WINDOW":
        lines += [
            f"Ventana: {candidate.get('window_start') or '—'} a {candidate.get('window_end') or '—'}",
            f"Objetivo: {candidate.get('target') or '—'}",
            f"Posibilidad experimental: {candidate.get('probability', 0) * 100:.1f} %",
            f"Referencia regional: {candidate.get('baseline_probability', 0) * 100:.1f} %",
            f"Confianza: {candidate.get('confidence', 0) * 100:.1f} %",
            "Estado: modo sombra.",
        ]
    lines += [
        "",
        "NO ES UNA PREDICCIÓN NI UNA ALERTA OFICIAL.",
        "Consulta USGS y las autoridades de Protección Civil.",
        "https://satodictator.github.io/SismoAI-World-Cloud/",
    ]
    return "\n".join(lines)


def evaluate_candidates(
    world: dict[str, Any],
    previous: dict[str, Any],
    policy: dict[str, Any],
    shadow: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    current = snapshot(world)
    prior_regions = previous.get("regions") or {}
    sent_keys = set(previous.get("sent_keys") or [])
    alert_states = set(policy.get("activation", {}).get(
        "states", ["WATCH", "ELEVATED", "HIGHLY_ATYPICAL"]
    ))
    min_event_mag = as_float(policy.get("activation", {}).get("observed_event_min_magnitude"), 5.0)
    increase = as_float(policy.get("activation", {}).get("iedc_increase_points"), 10.0)
    first_run = not bool(previous.get("initialized"))
    suppress_first = bool(policy.get("suppress_first_run", True))

    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for rid, now in current.items():
        before = prior_regions.get(rid) or {}
        state = str(now.get("state") or "NO_DATA")
        previous_state = str(before.get("state") or "NO_DATA")
        iedc = as_float(now.get("iedc"))
        previous_iedc = as_float(before.get("iedc"))
        common = {
            "region_id": rid,
            "region_name": now.get("region_name") or rid,
            "state": state,
            "iedc": iedc,
            "confidence": as_float(now.get("confidence")),
            "coverage": as_float(now.get("coverage")),
            "data_quality": as_float(now.get("data_quality")),
        }

        state_candidate = None
        if state in alert_states and previous_state != state:
            state_candidate = {
                **common,
                "kind": "STATE_CHANGE",
                "label": "Cambio de actividad regional",
                "reason": f"La región cambió de {previous_state} a {state}.",
                "key": f"state:{rid}:{state}",
            }
        elif state in alert_states and iedc - previous_iedc >= increase:
            bucket = int(iedc // max(1.0, increase))
            state_candidate = {
                **common,
                "kind": "STATE_CHANGE",
                "label": "Aumento importante del IEDC",
                "reason": f"El IEDC aumentó {iedc - previous_iedc:.1f} puntos.",
                "key": f"iedc:{rid}:{state}:{bucket}",
            }

        if state_candidate and state_candidate["key"] not in sent_keys:
            (suppressed if first_run and suppress_first else candidates).append(state_candidate)

        event_time = now.get("event_time")
        event_mag = as_float(now.get("event_magnitude"))
        previous_event_time = before.get("event_time")
        if event_time and event_time != previous_event_time and event_mag >= min_event_mag:
            event_candidate = {
                **common,
                "kind": "OBSERVED_EVENT",
                "label": "Actividad sísmica observada",
                "event_time": event_time,
                "magnitude": event_mag,
                "place": now.get("event_place") or "",
                "key": f"event:{rid}:{event_time}:{event_mag:.1f}",
            }
            if event_candidate["key"] not in sent_keys:
                (suppressed if first_run and suppress_first else candidates).append(event_candidate)

    if shadow:
        for window in shadow.get("windows") or []:
            if not window.get("notification_eligible"):
                continue
            forecast_id = str(window.get("forecast_id") or "")
            if not forecast_id:
                continue
            key = f"shadow:{forecast_id}"
            if key in sent_keys:
                continue
            candidate = {
                "kind": "SHADOW_WINDOW",
                "label": "Ventana probabilística experimental",
                "key": key,
                "region_id": window.get("region_id"),
                "region_name": window.get("region_name") or window.get("region_id"),
                "window_start": window.get("window_start"),
                "window_end": window.get("window_end"),
                "target": window.get("target"),
                "probability": as_float(window.get("probability")),
                "baseline_probability": as_float(window.get("baseline_probability")),
                "confidence": as_float(window.get("confidence")),
            }
            (suppressed if first_run and suppress_first else candidates).append(candidate)

    new_state = {
        "schema_version": 1,
        "initialized": True,
        "updated_at": utcnow(),
        "regions": current,
        "sent_keys": list(sent_keys)[-1000:],
    }
    return candidates, suppressed, new_state


def telegram_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "SismoAI-World-Cloud"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram no disponible: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rechazó la solicitud: {result}")
    return result


def send_telegram(token: str, chat_id: str, text: str) -> dict[str, Any]:
    return telegram_request(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": False,
        "protect_content": True,
    })


def update_manifest(world_path: Path, manifest_path: Path | None, manifest_sha_path: Path | None) -> None:
    if manifest_path is None or not manifest_path.exists():
        return
    manifest = read_json(manifest_path, {"files": []})
    files = manifest.setdefault("files", [])
    found = False
    for item in files:
        if item.get("path") == "data/world.json":
            item["sha256"] = sha256_file(world_path)
            found = True
            break
    if not found:
        files.append({"path": "data/world.json", "sha256": sha256_file(world_path)})
    files.sort(key=lambda item: str(item.get("path") or ""))
    write_json(manifest_path, manifest)
    if manifest_sha_path is not None:
        manifest_sha_path.write_text(
            f"{sha256_file(manifest_path)}  manifest.json\n", encoding="utf-8"
        )


def run_notifications(args: argparse.Namespace) -> int:
    world_path = Path(args.world)
    policy_path = Path(args.policy)
    previous_path = Path(args.previous_state) if args.previous_state else None
    output_state_path = Path(args.output_state)
    shadow_path = Path(args.shadow) if args.shadow else None
    manifest_path = Path(args.manifest) if args.manifest else None
    manifest_sha_path = Path(args.manifest_sha) if args.manifest_sha else None

    world = read_json(world_path, {})
    if not world.get("ranking"):
        raise SystemExit("world.json no contiene ranking regional.")
    policy = read_json(policy_path, {})
    previous = read_json(previous_path, {}) if previous_path else {}
    shadow = read_json(shadow_path, None) if shadow_path and shadow_path.exists() else None

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [
        value.strip()
        for value in os.environ.get("TELEGRAM_CHAT_IDS", "").replace(";", ",").split(",")
        if value.strip()
    ]
    configured = bool(token and chat_ids)
    requested_enabled = bool(policy.get("enabled"))
    active = bool(requested_enabled and configured)

    candidates, suppressed, next_state = evaluate_candidates(world, previous, policy, shadow)
    sent: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    if active:
        for candidate in candidates:
            delivered_to = 0
            for chat_id in chat_ids:
                try:
                    send_telegram(token, chat_id, candidate_message(candidate))
                    delivered_to += 1
                except Exception as exc:
                    errors.append({
                        "candidate_key": candidate["key"],
                        "recipient": "configured_private_chat",
                        "error": str(exc)[:500],
                    })
            if delivered_to:
                next_state["sent_keys"].append(candidate["key"])
                sent.append({
                    "key": candidate["key"],
                    "kind": candidate["kind"],
                    "region_id": candidate.get("region_id"),
                    "delivered_to": delivered_to,
                })

    next_state["sent_keys"] = list(dict.fromkeys(next_state["sent_keys"]))[-1000:]
    write_json(output_state_path, next_state)

    if not requested_enabled:
        status = "PREPARED_DISABLED"
    elif not configured:
        status = "WAITING_FOR_TELEGRAM_CONFIGURATION"
    elif errors and not sent:
        status = "DEGRADED"
    else:
        status = "ACTIVE"

    public = {
        "schema_version": 1,
        "generated_at": utcnow(),
        "status": status,
        "owner_label": policy.get("owner_label", "Propietario privado"),
        "privacy": "Los números, tokens y chat_id no se publican en la página ni en el repositorio.",
        "channels": {
            "telegram": {
                "available_without_payment": True,
                "configured": configured,
                "enabled": active,
                "recipients_count": len(chat_ids) if configured else 0,
                "schedule": "24 horas",
                "status": "ACTIVE" if active else (
                    "WAITING_FOR_BOT" if not configured else "DISABLED_BY_POLICY"
                ),
            },
            "whatsapp": {
                "available_without_payment": False,
                "configured": False,
                "enabled": False,
                "status": "NOT_AVAILABLE_FREE_OFFICIALLY",
            },
            "voice_call": {
                "available_without_payment": False,
                "configured": False,
                "enabled": False,
                "schedule": "Horario de oficina de Caracas si en el futuro se contrata un proveedor",
                "status": "NOT_AVAILABLE_FREE_OFFICIALLY",
            },
        },
        "activation_policy": {
            "states": policy.get("activation", {}).get("states", []),
            "iedc_increase_points": policy.get("activation", {}).get("iedc_increase_points"),
            "observed_event_min_magnitude": policy.get("activation", {}).get(
                "observed_event_min_magnitude"
            ),
            "shadow_windows": "Solo cuando notification_eligible=true",
            "deduplication": True,
            "first_run_is_silent": bool(policy.get("suppress_first_run", True)),
        },
        "last_run": {
            "candidates": len(candidates),
            "suppressed": len(suppressed),
            "sent": len(sent),
            "errors": len(errors),
        },
        "scientific_notice": SCIENTIFIC_NOTICE,
    }

    world["private_notifications"] = public
    write_json(world_path, world)
    update_manifest(world_path, manifest_path, manifest_sha_path)

    print(json.dumps({
        "status": "OK",
        "notification_status": status,
        "configured": configured,
        "enabled": active,
        "recipients": len(chat_ids) if configured else 0,
        "candidates": len(candidates),
        "suppressed": len(suppressed),
        "sent": len(sent),
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0


def test_message(_: argparse.Namespace) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [
        value.strip()
        for value in os.environ.get("TELEGRAM_CHAT_IDS", "").replace(";", ",").split(",")
        if value.strip()
    ]
    if not token or not chat_ids:
        raise SystemExit("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_IDS.")
    text = (
        "SISMOAI — PRUEBA PRIVADA\n\n"
        "El canal gratuito de Telegram quedó conectado correctamente.\n"
        "Los avisos estarán disponibles las 24 horas y solo se enviarán ante "
        "cambios definidos por la política, sin repetir continuamente.\n\n"
        "NO ES UNA PREDICCIÓN NI UNA ALERTA OFICIAL."
    )
    delivered = 0
    for chat_id in chat_ids:
        send_telegram(token, chat_id, text)
        delivered += 1
    print(json.dumps({"status": "OK", "delivered": delivered}, indent=2))
    return 0


def selftest(_: argparse.Namespace) -> int:
    mock_world = {
        "ranking": [{
            "region_id": "central_america",
            "region_name": "Centroamérica",
            "state": "ELEVATED",
            "iedc_provisional": 51.2,
            "confidence": 0.76,
            "coverage": 0.8,
            "data_quality": 0.9,
            "latest_event": {
                "magnitude": 5.1,
                "event_time": "2026-07-25T00:00:00Z",
                "place": "Prueba",
            },
        }]
    }
    policy = {
        "suppress_first_run": True,
        "activation": {
            "states": ["WATCH", "ELEVATED", "HIGHLY_ATYPICAL"],
            "iedc_increase_points": 10,
            "observed_event_min_magnitude": 5.0,
        },
    }
    candidates, suppressed, seeded = evaluate_candidates(mock_world, {}, policy, None)
    if candidates or len(suppressed) != 2 or not seeded.get("initialized"):
        raise SystemExit("Falló la supresión segura de la primera ejecución.")
    previous = {
        "initialized": True,
        "regions": {
            "central_america": {
                "state": "NORMAL",
                "iedc": 0,
                "event_time": "2026-07-24T00:00:00Z",
            }
        },
        "sent_keys": [],
    }
    candidates, suppressed, _ = evaluate_candidates(mock_world, previous, policy, None)
    kinds = {item["kind"] for item in candidates}
    if suppressed or kinds != {"STATE_CHANGE", "OBSERVED_EVENT"}:
        raise SystemExit("Falló la detección de candidatos.")
    print(json.dumps({
        "status": "OK",
        "first_run_suppressed": 2,
        "candidate_kinds": sorted(kinds),
        "network_calls": 0,
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Notificaciones privadas de SismoAI")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--world", required=True)
    run.add_argument("--policy", required=True)
    run.add_argument("--previous-state")
    run.add_argument("--output-state", required=True)
    run.add_argument("--shadow")
    run.add_argument("--manifest")
    run.add_argument("--manifest-sha")
    run.set_defaults(func=run_notifications)

    test = sub.add_parser("test")
    test.set_defaults(func=test_message)

    check = sub.add_parser("selftest")
    check.set_defaults(func=selftest)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
