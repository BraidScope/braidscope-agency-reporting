# SPDX-License-Identifier: AGPL-3.0-only
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import rate_limit


def make_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(rate_limit.rate_limit_middleware)

    @app.post("/py/chat")
    def chat():
        return {"ok": True}

    @app.post("/py/invitations/accept")
    def accept():
        return {"ok": True}

    @app.get("/py/health")
    def health():
        return {"ok": True}

    return app


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        rate_limit._memory.clear()
        self.env = patch.dict(os.environ, {"REDIS_URL": "", "RATE_LIMIT_DISABLED": ""})
        self.env.start()
        self.client = TestClient(make_app())

    def tearDown(self):
        self.env.stop()

    def test_groups(self):
        self.assertEqual(rate_limit.group_for("POST", "/py/chat"), "chat")
        self.assertEqual(rate_limit.group_for("POST", "/py/connectors/hubspot/oauth/start"), "oauth")
        self.assertEqual(rate_limit.group_for("POST", "/py/clients/abc/invitations"), "invitations")
        self.assertEqual(rate_limit.group_for("POST", "/py/settings/team/invite"), "invitations")
        self.assertIsNone(rate_limit.group_for("GET", "/py/chat"))
        self.assertIsNone(rate_limit.group_for("POST", "/py/dashboards/shopify"))

    def test_blocks_after_limit_and_sets_retry_after(self):
        limit = rate_limit.LIMITS["invitations"]
        for _ in range(limit):
            self.assertEqual(self.client.post("/py/invitations/accept").status_code, 200)
        blocked = self.client.post("/py/invitations/accept")
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_limits_are_per_ip(self):
        limit = rate_limit.LIMITS["invitations"]
        for _ in range(limit + 1):
            self.client.post("/py/invitations/accept", headers={"x-forwarded-for": "10.0.0.1"})
        other = self.client.post("/py/invitations/accept", headers={"x-forwarded-for": "10.0.0.2"})
        self.assertEqual(other.status_code, 200)

    def test_unlimited_endpoints_untouched(self):
        for _ in range(50):
            self.assertEqual(self.client.get("/py/health").status_code, 200)

    def test_disable_switch(self):
        with patch.dict(os.environ, {"RATE_LIMIT_DISABLED": "1"}):
            for _ in range(rate_limit.LIMITS["chat"] + 5):
                self.assertEqual(self.client.post("/py/chat").status_code, 200)


if __name__ == "__main__":
    unittest.main()
