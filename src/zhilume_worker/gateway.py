"""Authenticated inbound transport; all network requests originate at Server."""
import asyncio
import hashlib
import json
import secrets
import time
import uuid
import platform
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.responses import FileResponse
from .worker import Worker


def identity(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "identity.json"
    if not path.exists():
        value = {"workerId": str(uuid.uuid4()), "credential": secrets.token_urlsafe(32)}
        with path.open("x", encoding="utf-8") as f:
            path.chmod(0o600)
            json.dump(value, f)
    return json.loads(path.read_text("utf-8"))


def create_app(args):
    worker = Worker(args)
    credentials = identity(worker.root)
    worker.worker_id = credentials["workerId"]
    owner_file = worker.root / "owner"
    owner = owner_file.read_text("utf-8") if owner_file.exists() else None
    transfers = set()

    @asynccontextmanager
    async def lifespan(app):
        await worker.start()
        try:
            yield
        finally:
            await worker.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker

    def auth(headers, bound=False):
        if not secrets.compare_digest(headers.get("authorization", ""), "Bearer " + credentials["credential"]):
            raise HTTPException(401, "Worker 接入密钥无效")
        if bound and (not owner or headers.get("x-zhilume-server-id") != owner):
            raise HTTPException(403, "Worker 已绑定其他 Server 或尚未连接")

    def attempt(request, attempt_id):
        auth(request.headers, True)
        state = worker.active.get(attempt_id)
        if not state or state.get("cancelling") or state["deadline"] < time.monotonic() or request.headers.get("x-lease-id") != state["job"]["leaseId"]:
            raise HTTPException(409, "任务尝试已失效")
        return state

    @app.get("/api/v1/system")
    async def system(request: Request):
        auth(request.headers)
        return {"name": "Zhilume Worker", "workerId": worker.worker_id, "workerName": args.name, "platform": platform.system(), "protocolVersion": "2.0", "boundServerId": owner}

    @app.websocket("/api/v1/connect")
    async def connect(socket: WebSocket):
        nonlocal owner
        try:
            auth(socket.headers)
            server_id = socket.headers.get("x-zhilume-server-id", "")
            uuid.UUID(server_id)
            if owner and owner != server_id:
                raise ValueError("Already paired")
            if not owner:
                owner_file.write_text(server_id, "utf-8")
                owner_file.chmod(0o600)
                owner = server_id
        except (HTTPException, ValueError):
            await socket.close(code=1008)
            return
        await socket.accept()
        previous = worker.socket
        worker.socket = socket
        if previous:
            await previous.close(code=1000)
        try:
            await worker.hello()
            while True:
                raw = await socket.receive_text()
                if worker.socket is not socket:
                    break
                await worker.handle(json.loads(raw))
        except WebSocketDisconnect:
            pass
        except Exception:
            await socket.close(code=1008)
        finally:
            if worker.socket is socket:
                worker.socket = None

    @app.put("/api/v1/attempts/{attempt_id}/inputs/{asset_id}")
    async def upload(attempt_id: str, asset_id: str, request: Request):
        state = attempt(request, attempt_id)
        asset = state["inputs"].get(asset_id)
        if not asset:
            raise HTTPException(403, "素材不属于此任务")
        folder = worker.root / "attempts" / attempt_id / "inputs"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / asset_id
        if destination.exists():
            return {"sha256": asset["sha256"]}
        key = (attempt_id, asset_id)
        if key in transfers:
            raise HTTPException(409, "素材正在上传")
        transfers.add(key)
        temporary = destination.with_suffix(".upload")
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("wb") as file:
                async for chunk in request.stream():
                    attempt(request, attempt_id)
                    size += len(chunk)
                    if size > asset["size"]:
                        raise HTTPException(413, "素材超过声明大小")
                    digest.update(chunk)
                    await asyncio.to_thread(file.write, chunk)
            if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
                raise HTTPException(422, "素材校验失败")
            attempt(request, attempt_id)
            temporary.replace(destination)
            return {"sha256": asset["sha256"]}
        finally:
            transfers.discard(key)
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/attempts/{attempt_id}/output")
    async def output(attempt_id: str, request: Request):
        state = attempt(request, attempt_id)
        if "result" not in state:
            raise HTTPException(409, "输出尚未准备好")
        return FileResponse(state["output"], media_type="application/octet-stream")

    return app
