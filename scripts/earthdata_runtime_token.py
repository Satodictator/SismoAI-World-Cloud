from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

TOKEN_URL = "https://urs.earthdata.nasa.gov/api/users/find_or_create_token"


def _append_github_env(name: str, value: str) -> None:
    env_path = os.environ.get("GITHUB_ENV", "").strip()
    if not env_path:
        raise RuntimeError("GITHUB_ENV no está disponible")
    with Path(env_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _set_status(status: str, expiration: str = "") -> None:
    _append_github_env("EARTHDATA_AUTH_STATUS", status)
    _append_github_env("EARTHDATA_TOKEN_EXPIRATION", expiration)


def _clean_username(value: str) -> str:
    return value.lstrip("\ufeff\u200b\u2060").strip()


def _clean_password(value: str) -> str:
    return value.lstrip("\ufeff\u200b\u2060").rstrip("\r\n")


def _strict() -> bool:
    return os.environ.get("EARTHDATA_STRICT", "").strip().lower() in {"1", "true", "yes"}


def main() -> int:
    mode = os.environ.get("SISMOAI_MODE", "").strip().lower()
    strict = _strict()

    if mode == "fast":
        _set_status("SKIPPED_FAST")
        print("Earthdata: omitido en modo fast; InSAR se ejecuta en daily/weekly/bootstrap.")
        return 0

    username = _clean_username(os.environ.get("EARTHDATA_USERNAME", ""))
    password = _clean_password(os.environ.get("EARTHDATA_PASSWORD", ""))

    if not username or not password:
        _set_status("NOT_CONFIGURED")
        message = "Earthdata no configurado; faltan EARTHDATA_USERNAME o EARTHDATA_PASSWORD."
        print(f"::{'error' if strict else 'warning'}::{message}")
        return 2 if strict else 0

    try:
        shard = max(0, int(os.environ.get("EARTHDATA_SHARD_INDEX", "0") or 0))
    except ValueError:
        shard = 0

    if not strict:
        time.sleep(min(20, shard * 3))

    try:
        response = requests.post(
            TOKEN_URL,
            auth=(username, password),
            headers={"Accept": "application/json", "User-Agent": "SismoAI-World-Cloud/1.0"},
            timeout=(20, 60),
        )

        if response.status_code == 401:
            _set_status("INVALID_CREDENTIALS")
            print("::error::Earthdata rechazó el usuario o la contraseña (HTTP 401 invalid_credentials).")
            return 3 if strict else 0

        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        expiration = str(payload.get("expiration_date") or "").strip()

        if not token:
            raise RuntimeError("Earthdata respondió sin access_token")

        print(f"::add-mask::{token}")
        _append_github_env("EARTHDATA_TOKEN", token)
        _set_status("ACTIVE", expiration)
        print(json.dumps({
            "earthdata_status": "ACTIVE",
            "expiration_date": expiration or None,
            "mode": mode or None,
            "shard": shard,
        }, ensure_ascii=False))
        return 0

    except requests.RequestException as exc:
        _set_status("DEGRADED")
        level = "error" if strict else "warning"
        print(f"::{level}::Earthdata temporalmente no disponible: {type(exc).__name__}: {exc}")
        if not strict:
            print("Las fuentes USGS, GNSS, GOES y el catálogo ASF continuarán.")
        return 4 if strict else 0
    except Exception as exc:
        _set_status("DEGRADED")
        level = "error" if strict else "warning"
        print(f"::{level}::Error procesando la autenticación Earthdata: {type(exc).__name__}: {exc}")
        return 5 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
