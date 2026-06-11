"""KIS MCP shared-token recovery regression test."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.kis_token_cache import SharedTokenCache

SERVER_PATH = Path(__file__).parent.parent / "kis-mcp" / "server.py"


class _Response:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


class KisMcpTokenRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("kis_mcp_server_test", SERVER_PATH)
        cls.server = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.server
        spec.loader.exec_module(cls.server)

    def test_get_retries_with_new_shared_token(self) -> None:
        cache_path = Path(tempfile.mkdtemp()) / "token.json"
        issued = iter([{"access_token": "stale", "expires_in": 86400},
                       {"access_token": "fresh", "expires_in": 86400}])
        manager = SharedTokenCache("key", self.server.BASE_URL, lambda: next(issued),
                                   cache_path, min_issue_interval=0)
        self.server._token = None
        self.server._expires = None
        self.server._token_cache = manager
        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(headers["authorization"])
            if headers["authorization"] == "Bearer stale":
                return _Response({"msg_cd": "EGW00123"}, 500)
            return _Response({"output": {"stck_prpr": "1000"}})

        with patch.object(self.server.requests, "get", side_effect=fake_get):
            result = self.server.get_stock_price("005930")

        self.assertEqual(result["close"], 1000)
        self.assertEqual(calls, ["Bearer stale", "Bearer fresh"])


if __name__ == "__main__":
    unittest.main()
