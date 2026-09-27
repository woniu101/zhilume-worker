import asyncio
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from PIL import Image
from zhilume_worker.comfy import ComfyExecutor, normalize_image
from zhilume_worker.image_workflows import profiles, build_graph, validate_environment, MODELS

CONFIG = json.loads((Path(__file__).parent.parent / "config/comfy.example.json").read_text())


def inventory():
    info = {n: {} for m in MODELS.values() for n in m["requiredNodes"]}
    for node, key, field in [("UNETLoader", "diffusion", "unet_name"), ("CLIPLoader", "clip", "clip_name"), ("VAELoader", "vae", "vae_name")]:
        info[node] = {"input": {"required": {field: [[p["models"][key] for p in CONFIG["profiles"]]]}}}
    return info


def request(profile, refs=None, **overrides):
    p = profile["public"]
    return {**{k: p[k] for k in ("modelId", "profileId", "workflowRevision")}, "prompt": "测试", "negativePrompt": "",
            "seed": 3, "steps": 25, "width": 1024, "height": 768, "outputFormat": "png", "referenceAssetIds": refs or [], **overrides}


class ImagesTest(unittest.IsolatedAsyncioTestCase):
    def test_reference_cap_cannot_exceed_model_contract(self):
        config = json.loads(json.dumps(CONFIG))
        config["profiles"][1]["maxReferences"] = 10
        self.assertEqual(profiles(config)[1]["public"]["maxReferences"], 10)
        config["profiles"][1]["maxReferences"] = 11
        with self.assertRaises(ValueError):
            profiles(config)

    def test_graphs_have_distinct_encoders_and_preserve_reference_order(self):
        old, new = profiles(CONFIG)
        first = build_graph(old, "image.generate.v1", request(old), [], "test")
        self.assertEqual(first["sampling"]["inputs"]["shift"], 3.1)
        self.assertEqual(first["sample"]["inputs"]["cfg"], 4)
        second = build_graph(new, "image.reference.v1", request(new, ["b", "a"]), ["b.png", "a.png"], "test")
        self.assertEqual(second["encode"]["inputs"]["images.image_1"], ["reference_1", 0])
        self.assertEqual(second["reference_1"]["inputs"]["image"], "b.png")
        self.assertEqual(second["sample"]["inputs"]["latent_image"], ["encode", 2])
        self.assertNotIn("latent", second)
        text = build_graph(new, "image.generate.v1", request(new), [], "test")
        self.assertEqual(text["latent"]["class_type"], "EmptyLatentImage")
        for profile in (old, new):
            validate_environment(inventory(), profile)
        with self.assertRaises(ValueError):
            build_graph(old, "image.edit.v1", request(old, ["a"]), ["a.png"], "test")

    def test_rgba_is_preserved_and_rgb_is_not_misrepresented(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "image.png"
            Image.new("RGBA", (4, 4), (255, 0, 0, 0)).save(path)
            normalize_image(path, "rgba")
            with Image.open(path) as result:
                self.assertEqual(result.getpixel((0, 0)), (255, 0, 0, 0))
            normalize_image(path, "png")
            with Image.open(path) as result:
                self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
            with self.assertRaises(ValueError):
                normalize_image(path, "rgba")

    async def test_fake_comfy_submission_output_and_inventory_are_cpu_only(self):
        captured = []
        image = io.BytesIO(); Image.new("RGBA", (8, 4), (5, 10, 20, 100)).save(image, "PNG")
        def handle(req):
            if req.url.path == "/object_info": return httpx.Response(200, json=inventory())
            if req.url.path == "/queue": return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
            if req.url.path == "/prompt":
                data = json.loads(req.content); captured.append(data)
                return httpx.Response(200, json={"prompt_id": data["prompt_id"]})
            if req.url.path.startswith("/history/"):
                return httpx.Response(200, json={captured[0]["prompt_id"]: {"status": {"completed": True}, "outputs": {"output": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}}}})
            if req.url.path == "/view": return httpx.Response(200, content=image.getvalue())
            raise AssertionError(req.url)
        executor = ComfyExecutor(CONFIG, transport=httpx.MockTransport(handle))
        try:
            await executor.check()
            self.assertEqual(captured, [])
            with tempfile.TemporaryDirectory() as root:
                path, _ = await executor.process("image.generate.v1", request(executor.profiles[0]), [], Path(root), AsyncMock())
                with Image.open(path) as result: self.assertEqual(result.size, (8, 4))
            self.assertEqual(len(captured), 1)
        finally: await executor.http.aclose()

    async def test_lost_submit_response_never_reposts_and_cancels_only_own_id(self):
        calls = []; own = None; stopped = False
        def handle(req):
            nonlocal own, stopped
            data = json.loads(req.content) if req.method == "POST" else None
            calls.append((req.url.path, data))
            if req.url.path == "/object_info": return httpx.Response(200, json=inventory())
            if req.url.path == "/queue": return httpx.Response(200, json={"queue_running": [[0, own]] if own and not stopped else [], "queue_pending": []})
            if req.url.path == "/prompt":
                own = data["prompt_id"]
                raise httpx.ReadTimeout("response lost")
            if req.url.path == "/interrupt":
                self.assertEqual(data, {"prompt_id": own}); stopped = True
                return httpx.Response(200)
            raise AssertionError(req.url)
        executor = ComfyExecutor(CONFIG, transport=httpx.MockTransport(handle))
        try:
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(httpx.ReadTimeout):
                    await executor.process("image.generate.v1", request(executor.profiles[0]), [], Path(root), AsyncMock())
            self.assertEqual(sum(path == "/prompt" for path, _ in calls), 1)
            self.assertTrue(stopped)
            self.assertFalse(executor.poisoned)
        finally: await executor.http.aclose()

    async def test_unconfirmed_stop_disables_advertisement(self):
        executor = ComfyExecutor(CONFIG)
        executor.check = AsyncMock()
        executor.queue = AsyncMock(return_value={"queue_running": [], "queue_pending": []})
        executor.http.post = AsyncMock(side_effect=httpx.ReadTimeout("lost"))
        executor.stop = AsyncMock(side_effect=httpx.ConnectError("offline"))
        try:
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(ValueError, "执行已禁用"):
                    await executor.process("image.generate.v1", request(executor.profiles[0]), [], Path(root), AsyncMock())
            self.assertTrue(executor.poisoned)
            self.assertEqual(executor.public_profiles, [])
        finally: await executor.http.aclose()

    async def test_empty_queue_without_submission_ack_is_not_proof_of_stop(self):
        def handle(req):
            if req.url.path == "/queue": return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
            return httpx.Response(200, json={})
        executor = ComfyExecutor(CONFIG, transport=httpx.MockTransport(handle))
        try:
            with self.assertRaisesRegex(ValueError, "无法确定是否入队"):
                await executor.stop(str(uuid.uuid4()), require_evidence=True)
        finally: await executor.http.aclose()

    async def test_validation_rejection_is_not_reported_as_a_running_gpu_task(self):
        executor = ComfyExecutor(CONFIG)
        executor.check = AsyncMock()
        executor.queue = AsyncMock(return_value={"queue_running": [], "queue_pending": []})
        executor.http.post = AsyncMock(return_value=httpx.Response(400, json={"error": {"type": "prompt_outputs_failed_validation"}}))
        executor.stop = AsyncMock()
        try:
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(ValueError, "未开始推理"):
                    await executor.process("image.generate.v1", request(executor.profiles[0]), [], Path(root), AsyncMock())
                self.assertEqual(json.loads((Path(root) / "comfy-attempt.json").read_text())["state"], "rejected")
            self.assertFalse(executor.poisoned)
            executor.stop.assert_not_awaited()
        finally: await executor.http.aclose()

    async def test_restart_reconciles_persisted_attempt_before_new_work(self):
        executor = ComfyExecutor(CONFIG)
        executor.stop = AsyncMock()
        try:
            with tempfile.TemporaryDirectory() as root:
                attempt = str(uuid.uuid4())
                directory = Path(root) / "attempts" / attempt
                directory.mkdir(parents=True)
                path = directory / "comfy-attempt.json"
                executor.record(path, attempt, "submitted")
                await executor.recover(Path(root))
                executor.stop.assert_awaited_once_with(attempt, require_evidence=True)
                self.assertEqual(json.loads(path.read_text())["state"], "stopped")
                await executor.recover(Path(root))
                self.assertEqual(executor.stop.await_count, 1)
        finally: await executor.http.aclose()
