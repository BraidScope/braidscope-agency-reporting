# SPDX-License-Identifier: AGPL-3.0-only
import logging
import base64
import hashlib
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("braidscope.ai")
logger.setLevel(logging.INFO)

# debug_log() prints to stdout, which is what containers and the deploy logs
# read. A mirror file is useful on a developer machine to read a trace back
# after the fact. It is opt-in: set BRAIDSCOPE_DEBUG_LOG_FILE to a path.
# Default off, so a container never writes log files into the app folder.
_DEBUG_LOG_PATH = os.getenv("BRAIDSCOPE_DEBUG_LOG_FILE", "").strip()
if _DEBUG_LOG_PATH:
    try:
        _file_handler = logging.FileHandler(_DEBUG_LOG_PATH, encoding="utf-8")
        _file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(_file_handler)
    except OSError:
        pass

KLAVIYO_MCP_URL = "https://mcp.klaviyo.com/mcp?read-only=true&disable-tools-with-user-generated-content=true"
KLAVIYO_OAUTH_ISSUER = "https://mcp.klaviyo.com"
HUBSPOT_MCP_URL = "https://mcp.hubspot.com"
HUBSPOT_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v3/token"
GOOGLE_ADS_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_ADS_TOKEN_URL = "https://oauth2.googleapis.com/token"
GA4_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GA4_TOKEN_URL = "https://oauth2.googleapis.com/token"


def debug_log(message: str, *args: Any) -> None:
    text = message % args if args else message
    logger.info(text)
    print(f"[braidscope.ai] {text}", flush=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def urlsafe_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
