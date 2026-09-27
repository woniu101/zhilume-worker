import hashlib
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from zhilume_worker.gateway import create_app, identity


class GatewayTests(unittest.TestCase):
    def test_inbound_auth_input_integrity_result_and_owner_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(state=root, name="test", delay=0, enable_image_execution=False, upload_limit=1024)
            app = create_app(args)
            credentials = identity(Path(root))
            headers = {"Authorization": "Bearer " + credentials["credential"], "X-Zhilume-Server-Id": str(uuid.uuid4())}
            job = {"jobId": str(uuid.uuid4()), "attemptId": str(uuid.uuid4()), "leaseId": str(uuid.uuid4())}
            asset_id = str(uuid.uuid4())
            raw = b"fixture bytes"
            digest = hashlib.sha256(raw).hexdigest()
            def message(kind, payload=None):
                return {"protocolVersion": "3.0", "messageId": str(uuid.uuid4()), "type": kind, "payload": payload or {}, **job}
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/v1/system").status_code, 401)
                self.assertEqual(client.get("/api/v1/system", headers=headers).json()["workerId"], credentials["workerId"])
                with client.websocket_connect("/api/v1/connect", headers=headers) as socket:
                    self.assertEqual(socket.receive_json()["type"], "hello")
                    socket.send_json(message("task.assign", {"leaseSeconds": 30, "operation": "mock.media.copy.v1", "input": {"asset": {"id": asset_id, "filename": "test.png", "size": len(raw), "sha256": digest}}}))
                    while socket.receive_json()["type"] != "task.accepted":
                        pass
                    endpoint = f'/api/v1/attempts/{job["attemptId"]}/inputs/{asset_id}'
                    transfer_headers = {**headers, "X-Lease-Id": job["leaseId"]}
                    self.assertEqual(client.put(endpoint, headers=headers, content=raw).status_code, 409)
                    self.assertEqual(client.put(endpoint, headers=transfer_headers, content=b"bad").status_code, 422)
                    self.assertEqual(client.put(endpoint, headers=transfer_headers, content=raw + b"too large").status_code, 413)
                    self.assertEqual(client.put(endpoint, headers=transfer_headers, content=raw).status_code, 200)
                    self.assertEqual(client.put(endpoint, headers=transfer_headers, content=raw).status_code, 200)
                    socket.send_json(message("task.inputs_ready"))
                    for _ in range(20):
                        event = socket.receive_json()
                        if event["type"] == "task.result_ready":
                            break
                    self.assertEqual(event["payload"]["sha256"], digest)
                    result = client.get(f'/api/v1/attempts/{job["attemptId"]}/output', headers=transfer_headers)
                    self.assertEqual(result.content, raw)
                    with self.assertRaises(WebSocketDisconnect):
                        with client.websocket_connect("/api/v1/connect", headers={**headers, "X-Zhilume-Server-Id": str(uuid.uuid4())}):
                            pass
                    socket.send_json(message("task.commit_ack"))
                    for _ in range(100):
                        if not (Path(root) / "attempts" / job["attemptId"]).exists():
                            break
                        time.sleep(.01)
                    self.assertFalse((Path(root) / "attempts" / job["attemptId"]).exists())
