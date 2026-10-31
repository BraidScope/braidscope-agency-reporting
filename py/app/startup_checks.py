# SPDX-License-Identifier: AGPL-3.0-only
"""Refuse to start with missing or invalid secrets.

A container that boots with an empty encryption key or no database credentials
fails later, on the first request, with a confusing error. Checking once at
startup turns that into one clear message naming the variable.

APP_ENV=development or APP_ENV=test downgrades the failure to a warning so a
developer can run a partial stack. Anything else (the default) is strict.
"""
import logging
import os

from cryptography.fernet import Fernet

logger = logging.getLogger("braidscope.startup")

REQUIRED = (
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "CONNECTOR_TOKEN_ENCRYPTION_KEY",
)
# One of these must be set so bearer tokens can be verified locally.
JWT_SECRETS = ("SUPABASE_JWT_SECRET", "SHARED_JWT_SECRET")


def find_problems(env: dict[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    problems: list[str] = []
    for name in REQUIRED:
        if not (env.get(name) or "").strip():
            problems.append(f"{name} is not set")
    if not any((env.get(name) or "").strip() for name in JWT_SECRETS):
        problems.append("set SUPABASE_JWT_SECRET or SHARED_JWT_SECRET")
    key = (env.get("CONNECTOR_TOKEN_ENCRYPTION_KEY") or "").strip()
    if key:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, TypeError):
            problems.append(
                "CONNECTOR_TOKEN_ENCRYPTION_KEY is not a valid Fernet key "
                "(generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
            )
    return problems


def is_strict(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get("APP_ENV") or "").strip().lower() not in {"development", "dev", "test"}


def run_startup_checks() -> None:
    problems = find_problems()
    if not problems:
        return
    message = "Configuration problems: " + "; ".join(problems)
    if is_strict():
        raise RuntimeError(message + ". Set APP_ENV=development to start anyway.")
    logger.warning("%s (APP_ENV is not production, continuing)", message)
