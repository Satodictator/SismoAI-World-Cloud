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
    "Aviso experimental de SismoAI. No constituye una predicción sísmica, "
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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


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
            "generated_at": item.get("generated_at"),
            "event_time": event.get("event_time"),
            "event_magnitude": event.get("magnitude"),
            "event_place": event.get("place"),
        }
    return regions


def candidate_message(candidate: dict[str, Any]) -> str:
    kind = candidate["kind"]
    lines = [
        "SISMOAI — AVISO EXPERIMENTAL",
        "",
        f"Tipo: {candidate.get('label', kind)}",
        f"Región: {candidate.get('region_name', candidate.get('region_id', '—'))}",
    ]
    if kind == "STATE_CHANGE":
        lines += [
            f"Evaluado: {candidate.get('evaluated_at') or '—'}",
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
    alert_states = set(
        policy.get("activation", {}).get(
            "states", ["WATCH", "ELEVATED", "HIGHLY_ATYPICAL"]
        )
    )
    min_event_mag = as_float(
        policy.get("activation", {}).get("observed_event_min_magnitude"), 5.0
    )
    increase = as_float(
        policy.get("activation", {}).get("iedc_increase_points"), 10.0
    )
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
            "evaluated_at": now.get("generated_at"),
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
            (suppressed if first_run and suppress_first else candidates).append(
                state_candidate
            )

        event_time = now.get("event_time")
        event_mag = as_float(now.get("event_magnitude"))
        previous_event_time = before.get("event_time")
        if (
            event_time
            and event_time != previous_event_time
            and event_mag >= min_event_mag
        ):
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
                (suppressed if first_run and suppress_first else candidates).append(
                    event_candidate
                )

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
                "baseline_probability": as_float(
                    window.get("baseline_probability")
                ),
                "confidence": as_float(window.get("confidence")),
            }
            (suppressed if first_run and suppress_first else candidates).append(
                candidate
            )

    # SISMOAI_NOTIFICATION_KIND_FILTER_BEGIN
    # Permite que la pol?tica determine exactamente qu? tipos de candidatos
    # pueden convertirse en avisos. La configuraci?n p?blica utiliza solamente
    # SHADOW_WINDOW: ventanas probabil?sticas futuras en modo sombra.
    allowed_kinds = {
        str(value)
        for value in policy.get("activation", {}).get(
            "allowed_candidate_kinds",
            ["STATE_CHANGE", "OBSERVED_EVENT", "SHADOW_WINDOW"],
        )
    }

    candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("kind")) in allowed_kinds
    ]

    suppressed = [
        candidate
        for candidate in suppressed
        if str(candidate.get("kind")) in allowed_kinds
    ]
    # SISMOAI_NOTIFICATION_KIND_FILTER_END

    new_state = {
        "schema_version": 2,
        "initialized": True,
        "updated_at": utcnow(),
        "regions": current,
        "sent_keys": list(sent_keys)[-2000:],
    }
    return candidates, suppressed, new_state


def telegram_request(
    token: str, method: str, payload: dict[str, Any]
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "SismoAI-World-Cloud",
        },
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
    return telegram_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": False,
            "protect_content": True,
        },
    )


def github_request(
    token: str,
    repository: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}{endpoint}"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "SismoAI-World-Cloud",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub no disponible: {exc}") from exc


def github_digest_title(candidates: list[dict[str, Any]]) -> str:
    if len(candidates) == 1:
        item = candidates[0]
        region = item.get("region_name") or item.get("region_id") or "región"
        if item["kind"] == "OBSERVED_EVENT":
            return (
                f"[SismoAI] Evento observado M{item.get('magnitude', 0):.1f} "
                f"— {region}"
            )[:240]
        if item["kind"] == "SHADOW_WINDOW":
            return f"[SismoAI] Ventana experimental — {region}"[:240]
        return (
            f"[SismoAI] {item.get('label', 'Cambio de actividad')} "
            f"— {region}"
        )[:240]
    return f"[SismoAI] {len(candidates)} novedades de actividad — {utcnow()}"[:240]


def github_digest_body(
    candidates: list[dict[str, Any]],
    assignee: str,
    run_url: str,
) -> str:
    lines = [
        f"@{assignee}",
        "",
        "## Aviso automático experimental de SismoAI",
        "",
        (
            f"Se detectaron **{len(candidates)}** novedades incluidas en la "
            "política de notificación."
        ),
        "",
    ]
    for index, candidate in enumerate(candidates, 1):
        lines += [
            f"### {index}. {candidate.get('label', candidate.get('kind'))}",
            "",
            "```text",
            candidate_message(candidate),
            "```",
            "",
            f"Identificador deduplicado: `{candidate.get('key')}`",
            "",
        ]
    lines += [
        "## Alcance y limitaciones",
        "",
        (
            "Este Issue sirve como aviso automático de investigación. "
            "**No confirma que ocurrirá un terremoto futuro**, no reemplaza a "
            "USGS, Protección Civil ni a ninguna autoridad competente."
        ),
        "",
        f"Ejecución de origen: {run_url or 'no disponible'}",
        "",
        "Panel mundial: https://satodictator.github.io/SismoAI-World-Cloud/",
        "",
        "<!-- SISMOAI-AUTOMATIC-NOTICE -->",
    ]
    return "\n".join(lines)


def create_github_issue(
    token: str,
    repository: str,
    candidates: list[dict[str, Any]],
    assignee: str,
    labels: list[str],
    run_url: str,
) -> dict[str, Any]:
    return github_request(
        token,
        repository,
        "POST",
        "/issues",
        {
            "title": github_digest_title(candidates),
            "body": github_digest_body(candidates, assignee, run_url),
            "assignees": [assignee] if assignee else [],
            "labels": labels,
        },
    )


def update_manifest(
    world_path: Path,
    manifest_path: Path | None,
    manifest_sha_path: Path | None,
) -> None:
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
        files.append(
            {"path": "data/world.json", "sha256": sha256_file(world_path)}
        )
    files.sort(key=lambda item: str(item.get("path") or ""))
    write_json(manifest_path, manifest)
    if manifest_sha_path is not None:
        manifest_sha_path.write_text(
            f"{sha256_file(manifest_path)}  manifest.json\n",
            encoding="utf-8",
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
    shadow = (
        read_json(shadow_path, None)
        if shadow_path and shadow_path.exists()
        else None
    )

    delivery = policy.get("delivery") or {}
    requested_enabled = bool(policy.get("enabled"))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [
        value.strip()
        for value in os.environ.get("TELEGRAM_CHAT_IDS", "")
        .replace(";", ",")
        .split(",")
        if value.strip()
    ]
    telegram_requested = delivery.get("telegram") in {
        True,
        "enabled",
        "free_when_bot_is_configured",
    }
    telegram_configured = bool(token and chat_ids)
    telegram_active = bool(
        requested_enabled and telegram_requested and telegram_configured
    )

    github_cfg = delivery.get("github_issue") or {}
    github_requested = bool(github_cfg.get("enabled"))
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    github_configured = bool(github_token and github_repository)
    github_active = bool(
        requested_enabled and github_requested and github_configured
    )
    github_assignee = str(
        github_cfg.get("assignee") or os.environ.get("GITHUB_ACTOR") or ""
    ).strip()
    github_labels = [
        str(item).strip()
        for item in github_cfg.get(
            "labels", ["sismoai-aviso", "experimental"]
        )
        if str(item).strip()
    ]
    run_url = os.environ.get("SISMOAI_RUN_URL", "").strip()

    candidates, suppressed, next_state = evaluate_candidates(
        world, previous, policy, shadow
    )
    sent: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    issues_created = 0
    issue_urls: list[str] = []
    delivered_keys: set[str] = set()

    if github_active and candidates:
        try:
            issue = create_github_issue(
                github_token,
                github_repository,
                candidates,
                github_assignee,
                github_labels,
                run_url,
            )
            url = str(issue.get("html_url") or "")
            issues_created = 1
            if url:
                issue_urls.append(url)
            for candidate in candidates:
                delivered_keys.add(candidate["key"])
            sent.append(
                {
                    "kind": "GITHUB_DIGEST",
                    "delivered_to": "github_issue",
                    "candidate_count": len(candidates),
                    "issue_url": url,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "channel": "github_issue",
                    "error": str(exc)[:1000],
                }
            )

    if telegram_active:
        for candidate in candidates:
            delivered_to = 0
            for chat_id in chat_ids:
                try:
                    send_telegram(
                        token, chat_id, candidate_message(candidate)
                    )
                    delivered_to += 1
                except Exception as exc:
                    errors.append(
                        {
                            "candidate_key": candidate["key"],
                            "channel": "telegram",
                            "recipient": "configured_private_chat",
                            "error": str(exc)[:500],
                        }
                    )
            if delivered_to:
                delivered_keys.add(candidate["key"])
                sent.append(
                    {
                        "key": candidate["key"],
                        "kind": candidate["kind"],
                        "region_id": candidate.get("region_id"),
                        "channel": "telegram",
                        "delivered_to": delivered_to,
                    }
                )

    next_state["sent_keys"].extend(sorted(delivered_keys))
    next_state["sent_keys"] = list(
        dict.fromkeys(next_state["sent_keys"])
    )[-2000:]
    write_json(output_state_path, next_state)

    active_channels: list[str] = []
    if github_active:
        active_channels.append("github_issue")
    if telegram_active:
        active_channels.append("telegram")

    if not requested_enabled:
        status = "PREPARED_DISABLED"
    elif active_channels:
        status = "ACTIVE"
    elif github_requested and not github_configured:
        status = "WAITING_FOR_GITHUB_CONFIGURATION"
    elif telegram_requested and not telegram_configured:
        status = "WAITING_FOR_TELEGRAM_CONFIGURATION"
    else:
        status = "NO_ACTIVE_CHANNEL"

    public = {
        "schema_version": 2,
        "generated_at": utcnow(),
        "status": status,
        "owner_label": policy.get(
            "owner_label", "Propietario del repositorio"
        ),
        "privacy": (
            "Los teléfonos, tokens y chat_id no se publican. "
            "Los Issues de aviso sí son públicos porque el repositorio es público."
        ),
        "channels": {
            "github_issue": {
                "available_without_payment": True,
                "configured": github_configured,
                "enabled": github_active,
                "assignee": github_assignee,
                "public_visibility": True,
                "schedule": (
                    "En cada ejecución de SismoAI; normalmente cada 6 horas"
                ),
                "email_delivery": (
                    "Depende de la configuración de notificaciones de GitHub "
                    "del usuario asignado"
                ),
                "status": (
                    "ACTIVE"
                    if github_active
                    else (
                        "WAITING_FOR_WORKFLOW_PERMISSION"
                        if github_requested
                        else "DISABLED_BY_POLICY"
                    )
                ),
            },
            "telegram": {
                "available_without_payment": True,
                "configured": telegram_configured,
                "enabled": telegram_active,
                "recipients_count": (
                    len(chat_ids) if telegram_configured else 0
                ),
                "schedule": "24 horas cuando está configurado",
                "status": (
                    "ACTIVE"
                    if telegram_active
                    else (
                        "WAITING_FOR_BOT"
                        if telegram_requested
                        else "OPTIONAL_DISABLED"
                    )
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
                "schedule": (
                    "Horario de oficina de Caracas si en el futuro "
                    "se contrata un proveedor"
                ),
                "status": "NOT_AVAILABLE_FREE_OFFICIALLY",
            },
        },
        "activation_policy": {
            "states": policy.get("activation", {}).get("states", []),
            "iedc_increase_points": policy.get("activation", {}).get(
                "iedc_increase_points"
            ),
            "observed_event_min_magnitude": policy.get(
                "activation", {}
            ).get("observed_event_min_magnitude"),
            "shadow_windows": "Solo cuando notification_eligible=true",
            "deduplication": True,
            "first_run_is_silent": bool(
                policy.get("suppress_first_run", True)
            ),
            "github_digest": True,
        },
        "last_run": {
            "candidates": len(candidates),
            "suppressed": len(suppressed),
            "sent": len(sent),
            "issues_created": issues_created,
            "issue_urls": issue_urls,
            "errors": len(errors),
        },
        "scientific_notice": SCIENTIFIC_NOTICE,
    }

    world["private_notifications"] = public
    write_json(world_path, world)
    update_manifest(world_path, manifest_path, manifest_sha_path)

    print(
        json.dumps(
            {
                "status": "OK",
                "notification_status": status,
                "active_channels": active_channels,
                "candidates": len(candidates),
                "suppressed": len(suppressed),
                "sent": len(sent),
                "issues_created": issues_created,
                "issue_urls": issue_urls,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def test_message(_: argparse.Namespace) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = [
        value.strip()
        for value in os.environ.get("TELEGRAM_CHAT_IDS", "")
        .replace(";", ",")
        .split(",")
        if value.strip()
    ]
    if not token or not chat_ids:
        raise SystemExit(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_IDS."
        )
    text = (
        "SISMOAI — PRUEBA PRIVADA\n\n"
        "El canal gratuito de Telegram quedó conectado correctamente.\n"
        "NO ES UNA PREDICCIÓN NI UNA ALERTA OFICIAL."
    )
    delivered = 0
    for chat_id in chat_ids:
        send_telegram(token, chat_id, text)
        delivered += 1
    print(json.dumps({"status": "OK", "delivered": delivered}, indent=2))
    return 0


def test_github(args: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repository:
        raise SystemExit("Faltan GITHUB_TOKEN o GITHUB_REPOSITORY.")
    assignee = str(args.assignee or os.environ.get("GITHUB_ACTOR") or "").strip()
    candidate = {
        "kind": "STATE_CHANGE",
        "label": "PRUEBA DEL CANAL GRATUITO",
        "key": "test:github-channel:v1",
        "region_id": "test",
        "region_name": "Prueba controlada; no corresponde a una región real",
        "state": "TEST",
        "iedc": 0.0,
        "confidence": 0.0,
        "coverage": 0.0,
        "data_quality": 0.0,
        "evaluated_at": utcnow(),
        "reason": (
            "Comprobar que GitHub puede crear y asignar avisos automáticos."
        ),
    }
    issue = create_github_issue(
        token,
        repository,
        [candidate],
        assignee,
        ["sismoai-aviso", "experimental", "prueba"],
        os.environ.get("SISMOAI_RUN_URL", ""),
    )
    print(
        json.dumps(
            {
                "status": "OK",
                "issue_url": issue.get("html_url"),
                "issue_number": issue.get("number"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def selftest(_: argparse.Namespace) -> int:
    mock_world = {
        "ranking": [
            {
                "region_id": "central_america",
                "region_name": "Centroamérica",
                "state": "ELEVATED",
                "iedc_provisional": 51.2,
                "confidence": 0.76,
                "coverage": 0.8,
                "data_quality": 0.9,
                "generated_at": "2026-07-25T00:05:00Z",
                "latest_event": {
                    "magnitude": 5.1,
                    "event_time": "2026-07-25T00:00:00Z",
                    "place": "Prueba",
                },
            }
        ]
    }
    policy = {
        "suppress_first_run": True,
        "activation": {
            "states": ["WATCH", "ELEVATED", "HIGHLY_ATYPICAL"],
            "iedc_increase_points": 10,
            "observed_event_min_magnitude": 5.0,
        },
    }
    candidates, suppressed, seeded = evaluate_candidates(
        mock_world, {}, policy, None
    )
    if candidates or len(suppressed) != 2 or not seeded.get("initialized"):
        raise SystemExit(
            "Falló la supresión segura de la primera ejecución."
        )
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
    candidates, suppressed, _ = evaluate_candidates(
        mock_world, previous, policy, None
    )
    kinds = {item["kind"] for item in candidates}
    if suppressed or kinds != {"STATE_CHANGE", "OBSERVED_EVENT"}:
        raise SystemExit("Falló la detección de candidatos.")
    body = github_digest_body(
        candidates,
        "Satodictator",
        "https://github.com/example/actions/runs/1",
    )
    if "@Satodictator" not in body or "NO ES UNA PREDICCIÓN" not in body:
        raise SystemExit("Falló la construcción del Issue.")
    print(
        json.dumps(
            {
                "status": "OK",
                "first_run_suppressed": 2,
                "candidate_kinds": sorted(kinds),
                "github_digest": True,
                "network_calls": 0,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Notificaciones automáticas de SismoAI"
    )
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

    telegram_test = sub.add_parser("test")
    telegram_test.set_defaults(func=test_message)

    github_test = sub.add_parser("test-github")
    github_test.add_argument("--assignee", default="Satodictator")
    github_test.set_defaults(func=test_github)

    check = sub.add_parser("selftest")
    check.set_defaults(func=selftest)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
