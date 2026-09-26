import asyncio
import unittest

import httpx

from zhilume_worker.deployment import check_services


class DeploymentTests(unittest.TestCase):
    def test_read_only_probe_never_posts_or_follows_redirects(self):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            if request.url.path == "/api/v1/system":
                return httpx.Response(200, json={"name": "Zhilume Server", "protocolVersion": "1.0"})
            return httpx.Response(200, json={})
        transport = httpx.MockTransport(handler)
        result = asyncio.run(check_services("https://server.invalid", {"exclusive": True, "url": "http://127.0.0.1:8188", "profiles": []}, transport))
        self.assertTrue(result["serverReachable"])
        self.assertFalse(result["comfyEnvironmentComplete"])
        self.assertFalse(result["inferenceVerified"])
        self.assertTrue(result["errors"])
        self.assertEqual(calls, [("GET", "/api/v1/system"), ("GET", "/object_info")])


if __name__ == "__main__":
    unittest.main()
