# SPDX-License-Identifier: AGPL-3.0-only
"""Finds connector subpackages and their FastAPI routers.

A connector package (app/connectors/<id>/__init__.py) exports:

    POLICY = ConnectorPolicy(...)   # also added to policy.POLICIES
    router = APIRouter(prefix="/py/connectors/<id>")

main.py includes every router found here, so a new connector needs no edit
to main.py. Packages whose name starts with "_" are skipped.
"""
from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter


def connector_routers() -> list[APIRouter]:
    import app.connectors as package

    routers: list[APIRouter] = []
    for info in pkgutil.iter_modules(package.__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"app.connectors.{info.name}")
        router = getattr(module, "router", None)
        if isinstance(router, APIRouter):
            routers.append(router)
    return routers
