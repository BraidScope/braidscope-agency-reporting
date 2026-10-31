# SPDX-License-Identifier: AGPL-3.0-only
import os
from typing import Any

import httpx
import jwt
from fastapi import Header, HTTPException, status

from .schemas import AuthUser

def session_id_from_validated_token(token: str) -> str | None:
    """Read session metadata only after Supabase has validated the bearer token."""
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
        )
        return payload.get("session_id") or payload.get("jti")
    except jwt.InvalidTokenError:
        return None


async def verify_bearer_token(authorization: str | None = Header(default=None)) -> AuthUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    token = authorization.removeprefix("Bearer ").strip()
    secrets = [
        os.getenv("SUPABASE_JWT_SECRET"),
        os.getenv("SHARED_JWT_SECRET"),
    ]

    for secret in [s for s in secrets if s]:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience="authenticated",
                options={"verify_aud": False},
            )
            sub = payload.get("sub")
            if not sub:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token missing subject",
                )
            return AuthUser(
                sub=sub,
                email=payload.get("email"),
                role=payload.get("role"),
                access_token=token,
                session_id=payload.get("session_id"),
            )
        except jwt.InvalidTokenError:
            continue

    supabase_url = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY")
    if supabase_url and supabase_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{supabase_url.rstrip('/')}/auth/v1/user",
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {token}",
                    },
                )
            if response.is_success:
                user = response.json()
                user_id = user.get("id")
                if user_id:
                    return AuthUser(
                        sub=user_id,
                        email=user.get("email"),
                        role=user.get("role"),
                        access_token=token,
                        session_id=session_id_from_validated_token(token),
                    )
        except (httpx.HTTPError, ValueError):
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid bearer token",
    )
