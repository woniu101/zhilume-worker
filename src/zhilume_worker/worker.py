import asyncio
import hashlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

import jsonschema

from .comfy import ComfyExecutor
from .speech import SpeechExecutor
from .video import VideoExecutor

LOG = logging.getLogger("zhilume.worker")
CAPABILITIES = ["mock.text.echo.v1", "mock.media.copy.v1"]
SCHEMA = json.loads((Path(__file__).parent / "contracts/worker-message.schema.json").read_text("utf-8"))
VALIDATOR = jsonschema.Draft7Validator(SCHEMA)


class Worker:
    def __init__(self, args):
        self.args = args
        self.comfy = ComfyExecutor.from_file(args.comfy_config) if getattr(args, "enable_image_execution", False) else None
        self.speech = SpeechExecutor.from_file(args.speech_config) if getattr(args, "enable_speech_execution", False) else None
        self.video = VideoExecutor.from_file(args.video_config) if getattr(args, "enable_video_execution", False) else None
        self.capabilities = list(CAPABILITIES)
        self.root = Path(args.state).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = {}
        self.socket = None
        self.send_lock = asyncio.Lock()


    async def send(self, kind, payload=None, job=None, **extras):
        message = {"protocolVersion": "2.0", "messageId": str(uuid.uuid4()), "type": kind, "payload": payload or {}, **extras}
        if job:
            message.update({key: job[key] for key in ("jobId", "attemptId", "leaseId")})
        VALIDATOR.validate(message)
        async with self.send_lock:
            if self.socket:
                try:
                    await self.socket.send_text(json.dumps(message))
                except Exception:
                    pass  # Reconnection and result outbox handle transport loss.

    async def execute(self, job):
        directory = self.root / "attempts" / job["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        state = self.active[job["attemptId"]]
        try:
            await self.send("task.accepted", job=job)
            await state["inputs_ready"].wait()
            source = job["payload"]["input"]
            operation = job["payload"]["operation"]
            if operation == CAPABILITIES[0]:
                filename = "文本回显.txt"
                output = directory / "output.txt"
                output.write_text(source["text"], "utf-8")
            elif operation == CAPABILITIES[1]:
                filename = source["asset"]["filename"]
                output = directory / ("output" + Path(filename).suffix)
                await self.download(job, source["asset"], output)
            elif (operation.startswith("image.") and self.comfy) or (operation == "audio.speech.v1" and self.speech) or (operation == "video.generate.v1" and self.video):
                references = source["referenceAssets"]
                if [a["id"] for a in references] != source["referenceAssetIds"]:
                    raise ValueError("参考素材顺序不一致")
                paths = []
                for i, asset in enumerate(references):
                    path = directory / (f"reference_{i}" + Path(asset["filename"]).suffix)
                    await self.download(job, asset, path)
                    paths.append(path)
                sequence = 0
                async def image_progress(stage):
                    nonlocal sequence
                    sequence += 1
                    await self.send("task.progress", {"progress": None, "stage": stage}, job, sequence=sequence)
                if operation == "video.generate.v1":
                    output, filename = await self.video.process(operation, source, paths, directory, image_progress, job["attemptId"])
                elif operation == "audio.speech.v1":
                    output, filename = await self.speech.process(operation, source, paths, directory, image_progress)
                else:
                    output, filename = await self.comfy.process(operation, source, paths, directory, image_progress, job["attemptId"])
            else:
                raise ValueError("不支持的能力")
            if operation in CAPABILITIES:
                for step in range(1, 6):
                    await asyncio.sleep(self.args.delay / 5)
                    await self.send("task.progress", {"progress": step / 6, "stage": "模拟执行（不调用模型）"}, job, sequence=step)

            def describe():
                digest = hashlib.sha256()
                with output.open("rb") as file:
                    for chunk in iter(lambda: file.read(256 * 1024), b""):
                        digest.update(chunk)
                return {"filename": filename, "size": output.stat().st_size, "sha256": digest.hexdigest()}
            state["output"] = output
            state["result"] = await asyncio.to_thread(describe)
            # Wait for durable Server acknowledgement before deleting local output.
            while not state["committed"].is_set():
                await self.send("task.result_ready", state["result"], job)
                try:
                    await asyncio.wait_for(state["committed"].wait(), 3)
                except asyncio.TimeoutError:
                    pass
            shutil.rmtree(directory)
            LOG.info("任务已由 Server 归档 %s", job["jobId"])
        except asyncio.CancelledError:
            await self.send("task.cancelled", {"reason": "cancelled_or_lease_expired"}, job)
            LOG.info("任务已停止 %s", job["jobId"])
        except Exception as error:
            LOG.warning("任务执行失败 %s (%s)", job["jobId"], type(error).__name__)
            await self.send("task.failed", {"message": str(error) if isinstance(error, ValueError) else "执行失败，请检查网络、编码和本地磁盘"}, job)
        finally:
            self.active.pop(job["attemptId"], None)
            if (self.comfy and self.comfy.poisoned) or (self.video and self.video.poisoned):
                if self.comfy and self.video and self.comfy.endpoint_id == self.video.endpoint_id:
                    self.comfy.poisoned = self.video.poisoned = True
                await self.hello()

    async def download(self, job, asset, output):
        source = self.root / "attempts" / job["attemptId"] / "inputs" / asset["id"]
        def copy():
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as src, output.open("wb") as dst:
                for chunk in iter(lambda: src.read(256 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
                    dst.write(chunk)
            if digest.hexdigest() != asset["sha256"] or size != asset["size"]:
                raise ValueError("输入素材校验失败")
        await asyncio.to_thread(copy)

    async def hello(self):
        image_profiles = self.comfy.public_profiles if self.comfy else []
        speech_profiles = self.speech.public_profiles if self.speech else []
        video_profiles = self.video.public_profiles if self.video else []
        capabilities = (["video.generate.v1"] if video_profiles else []) + (["audio.speech.v1"] if speech_profiles else []) + self.capabilities + sorted({op for p in image_profiles for op in p["operations"]})
        await self.send("hello", {"workerId": self.worker_id, "capabilities": capabilities, "imageProfiles": image_profiles, "speechProfiles": speech_profiles, "videoProfiles": video_profiles, "activeAttempts": list(self.active)})
        for state in list(self.active.values()):
            if not state["inputs_ready"].is_set():
                await self.send("task.accepted", job=state["job"])

    @staticmethod
    def cancel(state):
        if not state.get("cancelling"):
            state["cancelling"] = True
            state["task"].cancel()

    async def maintenance(self):
        heartbeat_at = 0
        while True:
            current = time.monotonic()
            if current >= heartbeat_at:
                await self.send("heartbeat", {"activeAttempts": list(self.active)})
                heartbeat_at = current + 10
            for state in list(self.active.values()):
                if current > state["deadline"]:
                    self.cancel(state)
            await asyncio.sleep(1)

    async def handle(self, message):
        VALIDATOR.validate(message)
        kind = message["type"]
        attempt = message.get("attemptId")
        state = self.active.get(attempt)
        if kind == "task.assign" and not state:
            if self.active:
                await self.send("task.failed", {"message": "Worker 正忙"}, message)
                return
            for key in ("attemptId", "jobId", "leaseId"):
                uuid.UUID(message[key])
            source = message["payload"]["input"]
            inputs = source.get("referenceAssets", [source["asset"]] if "asset" in source else [])
            if len(inputs) > 16:
                raise ValueError("参考素材数量超限")
            for asset in inputs:
                uuid.UUID(asset["id"])
                if not isinstance(asset["size"], int) or not 0 < asset["size"] <= self.args.upload_limit:
                    raise ValueError("素材大小无效")
            state = {"deadline": time.monotonic() + message["payload"]["leaseSeconds"], "committed": asyncio.Event(), "inputs_ready": asyncio.Event(), "job": message, "inputs": {a["id"]: a for a in inputs}}
            if not inputs:
                state["inputs_ready"].set()
            self.active[attempt] = state
            state["task"] = asyncio.create_task(self.execute(message))
        elif kind.startswith("task.") and state and any(message.get(k) != state["job"][k] for k in ("jobId", "leaseId")):
            raise ValueError("过期任务尝试")
        elif kind == "task.inputs_ready" and state:
            folder = self.root / "attempts" / attempt / "inputs"
            if not all((folder / key).is_file() for key in state["inputs"]):
                raise ValueError("输入素材未完整上传")
            state["inputs_ready"].set()
        elif kind == "lease.renewed" and state:
            state["deadline"] = time.monotonic() + message["payload"]["leaseSeconds"]
        elif kind == "task.cancel" and state:
            self.cancel(state)
        elif kind == "task.commit_ack" and state:
            state["committed"].set()
            self.active.pop(attempt, None)
        elif kind == "error":
            LOG.warning("Server 协议提示: %s", message["payload"].get("code"))

    async def start(self):
        if self.video:
            await self.video.check()
            await self.video.recover(self.root)
        if self.speech:
            await self.speech.check()
        if self.comfy:
            await self.comfy.check()
            await self.comfy.recover(self.root)
        self.maintenance_task = asyncio.create_task(self.maintenance())

    async def close(self):
        self.maintenance_task.cancel()
        tasks = [state["task"] for state in list(self.active.values())]
        for state in list(self.active.values()):
            self.cancel(state)
        await asyncio.gather(self.maintenance_task, *tasks, return_exceptions=True)
        if self.video:
            await self.video.http.aclose()
        if self.comfy:
            await self.comfy.http.aclose()
