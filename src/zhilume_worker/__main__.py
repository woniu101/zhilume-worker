import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import random
import shutil
import time
import uuid
from pathlib import Path

import httpx
import jsonschema
from websockets.asyncio.client import connect

from . import media
from .comfy import ComfyExecutor

LOG = logging.getLogger("zhilume.worker")
CAPABILITIES = ["mock.text.echo.v1", "mock.media.copy.v1"]
SCHEMA = json.loads((Path(__file__).parent / "contracts/worker-message.schema.json").read_text("utf-8"))
VALIDATOR = jsonschema.Draft7Validator(SCHEMA)


class Worker:
    def __init__(self, args):
        self.args = args
        self.comfy = ComfyExecutor.from_file(args.comfy_config) if getattr(args, "enable_image_execution", False) else None
        self.capabilities = CAPABILITIES + (list(media.OPERATIONS) if media.binary() else [])
        self.root = Path(args.state).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity_file = self.root / "identity.json"
        self.active = {}
        self.socket = None
        self.send_lock = asyncio.Lock()
        self.http = httpx.AsyncClient(base_url=args.server.rstrip("/"), timeout=httpx.Timeout(60, read=120))

    async def send(self, kind, payload=None, job=None, **extras):
        message = {"protocolVersion": "1.0", "messageId": str(uuid.uuid4()), "type": kind, "payload": payload or {}, **extras}
        if job:
            message.update({key: job[key] for key in ("jobId", "attemptId", "leaseId")})
        VALIDATOR.validate(message)
        async with self.send_lock:
            if self.socket:
                try:
                    await self.socket.send(json.dumps(message))
                except Exception:
                    pass  # Reconnection and result outbox handle transport loss.

    async def register(self):
        if self.identity_file.exists():
            identity = json.loads(self.identity_file.read_text("utf-8"))
            if identity["server"] != self.args.server.rstrip("/"):
                raise ValueError("状态目录属于另一个 Server，请指定新的 --state 目录")
        else:
            enrollment = self.args.enrollment or os.environ.get("ZHILUME_ENROLLMENT")
            if not enrollment:
                raise ValueError("首次接入需要 Server 管理台生成的 --enrollment 凭证")
            response = await self.http.post("/api/v1/workers/register", json={"token": enrollment, "name": self.args.name, "platform": platform.system()})
            response.raise_for_status()
            identity = {**response.json(), "server": self.args.server.rstrip("/")}
            temporary = self.identity_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(identity), "utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.identity_file)
        self.http.headers["Authorization"] = "Bearer " + identity["credential"]
        self.identity = identity

    def headers(self, job):
        return {"X-Attempt-Id": job["attemptId"], "X-Lease-Id": job["leaseId"]}

    async def execute(self, job):
        directory = self.root / "attempts" / job["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        state = self.active[job["attemptId"]]
        try:
            await self.send("task.accepted", job=job)
            source = job["payload"]["input"]
            operation = job["payload"]["operation"]
            if operation == CAPABILITIES[0]:
                filename = "文本回显.txt"
                output = directory / "output.txt"
                output.write_text(source["text"], "utf-8")
            elif operation == CAPABILITIES[1] or operation in media.OPERATIONS:
                filename = source["asset"]["filename"]
                output = directory / ("output" + Path(filename).suffix)
                await self.download(job, source["asset"], output)
            elif operation.startswith("image.") and self.comfy:
                references = source["referenceAssets"]
                if [a["id"] for a in references] != source["referenceAssetIds"]:
                    raise ValueError("参考图顺序不一致")
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
                output, filename = await self.comfy.process(operation, source, paths, directory, image_progress, job["attemptId"])
            else:
                raise ValueError("不支持的能力")
            if operation in media.OPERATIONS:
                sequence = 0
                async def progress(value):
                    nonlocal sequence
                    sequence += 1
                    await self.send("task.progress", {"progress": value, "stage": "CPU 媒体处理"}, job, sequence=sequence)
                output, filename = await media.process(operation, output, directory, source, progress)
            elif operation in CAPABILITIES:
                for step in range(1, 6):
                    await asyncio.sleep(self.args.delay / 5)
                    await self.send("task.progress", {"progress": step / 6, "stage": "模拟执行（不调用模型）"}, job, sequence=step)

            async def chunks():
                with output.open("rb") as file:
                    while chunk := file.read(256 * 1024):
                        yield chunk
                        await asyncio.sleep(0)

            response = await self.http.post(f'/api/v1/worker/jobs/{job["jobId"]}/output', params={"filename": filename}, headers={**self.headers(job), "Content-Type": "application/octet-stream"}, content=chunks())
            response.raise_for_status()
            result = response.json()
            state["result"] = {"assetId": result["id"], "sha256": result["sha256"]}
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
            if self.comfy and self.comfy.poisoned:
                await self.hello()

    async def download(self, job, asset, output):
        digest = hashlib.sha256()
        size = 0
        async with self.http.stream("GET", f'/api/v1/worker/jobs/{job["jobId"]}/inputs/{asset["id"]}', headers=self.headers(job)) as response:
            response.raise_for_status()
            with output.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > asset["size"]:
                        raise ValueError("输入素材长度超出声明")
                    digest.update(chunk)
                    file.write(chunk)
        if digest.hexdigest() != asset["sha256"] or size != asset["size"]:
            raise ValueError("输入素材校验失败")

    async def hello(self):
        image_profiles = self.comfy.public_profiles if self.comfy else []
        capabilities = self.capabilities + sorted({op for p in image_profiles for op in p["operations"]})
        await self.send("hello", {"capabilities": capabilities, "imageProfiles": image_profiles, "activeAttempts": list(self.active)})

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
            state = {"deadline": time.monotonic() + message["payload"]["leaseSeconds"], "committed": asyncio.Event()}
            self.active[attempt] = state
            state["task"] = asyncio.create_task(self.execute(message))
        elif kind == "lease.renewed" and state:
            state["deadline"] = time.monotonic() + message["payload"]["leaseSeconds"]
        elif kind == "task.cancel" and state:
            self.cancel(state)
        elif kind == "task.commit_ack" and state:
            state["committed"].set()
            self.active.pop(attempt, None)
        elif kind == "error":
            LOG.warning("Server 协议提示: %s", message["payload"].get("code"))

    async def run(self):
        if self.comfy:
            await self.comfy.check()
            await self.comfy.recover(self.root)
        await self.register()
        maintenance = asyncio.create_task(self.maintenance())
        url = self.args.server.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/api/v1/worker/connect"
        backoff = 1
        try:
            while True:
                try:
                    async with connect(url, additional_headers={"Authorization": self.http.headers["Authorization"]}, max_size=256 * 1024) as socket:
                        self.socket = socket
                        await self.hello()
                        LOG.info("已连接 Server；图片执行%s", "已显式启用，尚未 GPU 验收" if self.comfy else "未启用")
                        backoff = 1
                        async for raw in socket:
                            await self.handle(json.loads(raw))
                except (OSError, ValueError, httpx.HTTPError, jsonschema.ValidationError) as error:
                    LOG.warning("连接暂不可用: %s", type(error).__name__)
                except Exception as error:
                    LOG.warning("连接中断: %s", type(error).__name__)
                finally:
                    self.socket = None
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.2))
                backoff = min(backoff * 2, 30)
        finally:
            maintenance.cancel()
            tasks = [state["task"] for state in list(self.active.values())]
            for state in list(self.active.values()):
                self.cancel(state)
            await asyncio.gather(maintenance, *tasks, return_exceptions=True)
            await self.http.aclose()
            if self.comfy:
                await self.comfy.http.aclose()


def main():
    parser = argparse.ArgumentParser(description="Zhilume Worker：CPU 媒体处理与可选 ComfyUI 图片执行")
    parser.add_argument("--server", default="http://127.0.0.1:4310")
    parser.add_argument("--enrollment")
    parser.add_argument("--name", default=platform.node())
    parser.add_argument("--state", default=".state")
    parser.add_argument("--delay", type=float, default=3, help="模拟计算秒数")
    parser.add_argument("--comfy-config", help="专用 ComfyUI 配置 JSON")
    parser.add_argument("--enable-image-execution", action="store_true", help="显式允许提交 GPU 图片任务")
    args = parser.parse_args()
    if args.enable_image_execution and not args.comfy_config:
        parser.error("启用图片执行需要 --comfy-config")
    if args.delay < 0 or not args.server.startswith(("http://", "https://")):
        parser.error("Server 地址或 delay 不合法")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(Worker(args).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
