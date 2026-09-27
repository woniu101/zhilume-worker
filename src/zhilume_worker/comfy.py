"""Dedicated ComfyUI executor. Explicit opt-in; uncertain cancellation fails closed."""
import asyncio
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath

import httpx


from .image_workflows import profiles, validate_environment, build_graph


def normalize_image(path, output_format):
    from PIL import Image
    with Image.open(path) as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("生成图片超出像素限制")
        image.load()
        if output_format == "rgba":
            if image.mode != "RGBA":
                raise ValueError("模型未返回 RGBA 图像，不能将 RGB 结果标记为透明输出")
            converted = image.copy()
        else:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            converted = Image.alpha_composite(background, rgba).convert("RGB")
    converted.save(path, "PNG")


class ComfyExecutor:
    profile_factory = staticmethod(profiles)
    environment_validator = staticmethod(validate_environment)
    graph_builder = staticmethod(build_graph)

    def __init__(self, config, transport=None):
        if config.get("exclusive") is not True:
            raise ValueError("生成执行需要专用 ComfyUI；请配置 exclusive: true")
        self.profiles = self.profile_factory(config)
        self.timeout = config.get("timeoutSeconds", 1800)
        if type(self.timeout) is not int or not 1 <= self.timeout <= 7200:
            raise ValueError("执行超时应为 1–7200 秒")
        self.http = httpx.AsyncClient(base_url=config["url"].rstrip("/"), timeout=60, transport=transport)
        self.endpoint_id = hashlib.sha256(config["url"].rstrip("/").encode()).hexdigest()
        self.poisoned = False
        self.idle_vram_limit = config.get('idleVramLimitMiB', 1536)
        if type(self.idle_vram_limit) is not int or not 256 <= self.idle_vram_limit <= 4096:
            raise ValueError('卸载后的显存上限应为 256–4096 MiB，按专用服务空载基线设置')

    async def release(self):
        if self.poisoned: raise ValueError('ComfyUI 状态未确认，不能释放资源锁')
        queue = (await self.http.get('/queue')).json()
        if queue.get('queue_running') or queue.get('queue_pending'): raise ValueError('ComfyUI 仍有任务')
        response = await self.http.post('/free', json={'unload_models': True, 'free_memory': True})
        response.raise_for_status()
        stable = 0
        for _ in range(60):
            stats = await self.http.get('/system_stats'); stats.raise_for_status()
            devices = stats.json().get('devices', [])
            # Comfy adds unused torch reservations to vram_free. Add them back when
            # measuring physical occupancy; cudaMallocAsync/custom allocations are
            # not reliably reflected in active_bytes alone. /free is asynchronous.
            def unloaded(d):
                fields = ('vram_total', 'vram_free', 'torch_vram_total', 'torch_vram_free')
                if any(type(d.get(k)) not in (int, float) for k in fields): return False
                physical_used = d['vram_total'] - d['vram_free'] + d['torch_vram_free']
                return d['type'] == 'cuda' and physical_used <= self.idle_vram_limit * 1024 ** 2 and d['torch_vram_total'] - d['torch_vram_free'] < 64 * 1024 ** 2
            stable = stable + 1 if devices and all(unloaded(d) for d in devices) else 0
            if stable >= 2: return
            await asyncio.sleep(1)
        raise ValueError('ComfyUI 显存释放未确认')

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text("utf-8")))

    async def check(self):
        try:
            import PIL
        except ModuleNotFoundError:
            raise ValueError('缺少图片适配器依赖：请显式安装 zhilume-worker[image]，核心管理服务无需此依赖') from None
        response = await self.http.get("/object_info")
        response.raise_for_status()
        for profile in self.profiles:
            self.environment_validator(response.json(), profile)

    async def recover(self, root):
        """Reconcile crash leftovers before publishing any image capability."""
        for path in root.glob("attempts/*/comfy-attempt.json"):
            record = json.loads(path.read_text("utf-8"))
            if record["state"] != "submitted":
                continue
            if record["endpoint"] != self.endpoint_id:
                raise ValueError("存在另一个 ComfyUI 的未确认任务，请先检查原实例")
            prompt_id = str(uuid.UUID(record["promptId"]))
            try:
                await asyncio.wait_for(self.stop(prompt_id, require_evidence=True), 15)
            except Exception:
                self.poisoned = True
                raise ValueError("上次 GPU 任务状态未确认，请检查 ComfyUI 队列和本地任务记录") from None
            self.record(path, prompt_id, "stopped")

    def record(self, path, prompt_id, state):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"promptId": prompt_id, "endpoint": self.endpoint_id, "state": state}), "utf-8")
        temporary.replace(path)

    @property
    def public_profiles(self):
        return [] if self.poisoned else [p["public"] for p in self.profiles]

    async def queue(self):
        response = await self.http.get("/queue")
        response.raise_for_status()
        value = response.json()
        if not all(isinstance(value.get(k), list) for k in ("queue_running", "queue_pending")):
            raise ValueError("ComfyUI 队列响应无效")
        return value

    async def stop(self, prompt_id, require_evidence=False):
        """Target only this attempt. Never clear the queue or interrupt globally."""
        seen = False
        for _ in range(10):
            queue = await self.queue()
            pending = any(row[1] == prompt_id for row in queue["queue_pending"])
            running = any(row[1] == prompt_id for row in queue["queue_running"])
            if not pending and not running:
                if require_evidence and not seen:
                    response = await self.http.get("/history/" + prompt_id)
                    response.raise_for_status()
                    if not response.json().get(prompt_id):
                        raise ValueError("提交响应丢失且无法确定是否入队")
                return
            seen = True
            if pending:
                response = await self.http.post("/queue", json={"delete": [prompt_id]})
                response.raise_for_status()
            if running:
                response = await self.http.post("/interrupt", json={"prompt_id": prompt_id})
                response.raise_for_status()
            await asyncio.sleep(.5)
        raise ValueError("无法确认 ComfyUI 停止，已禁用此 ComfyUI 执行，请检查实例后重启 Worker")

    async def process(self, operation, request, paths, directory, progress, attempt_id=None):
        if self.poisoned:
            raise ValueError("ComfyUI 状态待人工核对，请检查实例后重启 Worker")
        profile = next((p for p in self.profiles if p["public"]["profileId"] == request["profileId"]), None)
        if not profile:
            raise ValueError("模型执行配置不存在")
        prompt_id = str(uuid.UUID(attempt_id)) if attempt_id else str(uuid.uuid4())
        record_path = directory / "comfy-attempt.json"
        submitted = False
        acknowledged = False
        try:
            async with asyncio.timeout(self.timeout):
                await self.check()
                queue = await self.queue()
                if queue["queue_pending"] or queue["queue_running"]:
                    raise ValueError("专用 ComfyUI 正忙，请等待当前任务完成")
                prepared = await self.prepare_inputs(profile, operation, request, paths, directory, progress)
                uploaded = await self.upload_inputs(prepared, prompt_id, progress)
                graph = self.graph_builder(profile, operation, request, uploaded, "zhilume/" + prompt_id)
                # Set before POST: loss of the response does not mean submission failed.
                self.record(record_path, prompt_id, "submitted")
                submitted = True
                response = await self.http.post("/prompt", json={"prompt": graph, "prompt_id": prompt_id, "client_id": prompt_id})
                if response.status_code == 400:
                    # ComfyUI rejects validation errors before adding a prompt to its queue.
                    submitted = False
                    self.record(record_path, prompt_id, "rejected")
                    raise ValueError("ComfyUI 工作流校验失败，未开始推理；请核对节点版本与模型配置")
                response.raise_for_status()
                if response.json().get("prompt_id") != prompt_id:
                    self.poisoned = True
                    raise ValueError("ComfyUI 不支持指定任务 ID，请升级后重启 Worker，并检查队列")
                acknowledged = True
                await progress("模型执行中（等待 ComfyUI 结果）")
                while True:
                    response = await self.http.get("/history/" + prompt_id)
                    response.raise_for_status()
                    history = response.json().get(prompt_id)
                    if history:
                        status = history.get("status", {})
                        if status.get("status_str") == "error":
                            raise ValueError("ComfyUI 执行失败，请检查模型、显存及实例日志")
                        if status.get("completed"):
                            break
                    await asyncio.sleep(.5)
                result = await self.collect_output(history, request, directory, progress)
                self.record(record_path, prompt_id, "completed")
                return result
        except BaseException:
            if submitted:
                try:
                    await asyncio.wait_for(self.stop(prompt_id, require_evidence=not acknowledged), 15)
                    if not self.poisoned:
                        self.record(record_path, prompt_id, "stopped")
                except BaseException:
                    self.poisoned = True
                    raise ValueError("无法确认 GPU 任务停止；此 ComfyUI 执行已禁用，请检查实例后重启 Worker") from None
            raise

    async def prepare_inputs(self, profile, operation, request, paths, directory, progress):
        return paths

    async def upload_inputs(self, paths, prompt_id, progress):
        uploaded = []
        for i, path in enumerate(paths):
            await progress("传输参考素材到 ComfyUI")
            with path.open("rb") as file:
                response = await self.http.post("/upload/image", files={"image": (f"zhilume_{prompt_id}_{i}{path.suffix}", file)}, data={"type": "input", "overwrite": "false"})
            response.raise_for_status()
            value = response.json()
            name = str(PurePosixPath(value.get("subfolder", "")) / value["name"])
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name:
                raise ValueError("ComfyUI 上传路径无效")
            uploaded.append(name)
        return uploaded

    async def collect_output(self, history, request, directory, progress):
        images = history.get("outputs", {}).get("output", {}).get("images", [])
        if len(images) != 1 or images[0].get("type") != "output":
            raise ValueError("ComfyUI 未返回预期的单张图片")
        await progress("校验并归档生成图片")
        output = directory / "generated.png"
        size = 0
        async with self.http.stream("GET", "/view", params={k: images[0][k] for k in ("filename", "subfolder", "type")}) as response:
            response.raise_for_status()
            with output.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 64 * 1024 ** 2:
                        raise ValueError("生成图片超过 64 MB")
                    file.write(chunk)
        await asyncio.to_thread(normalize_image, output, request["outputFormat"])
        return output, "生成图片.png"
