"""Prepare a dedicated ComfyUI installation without loading weights or starting GPU jobs."""
import argparse
import asyncio
import json
import os
import platform
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .image_workflows import profiles, validate_environment
from .media import binary
from .model_links import plan_links, apply_links


async def check_services(worker, config, transport=None, credential=None):
    result = {"workerChecked": bool(worker), "comfyChecked": bool(config), "workerReachable": False, "comfyEnvironmentComplete": False, "inferenceVerified": False, "errors": []}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        if worker:
            try:
                endpoint = urlsplit(worker)
                if endpoint.scheme not in ("http", "https") or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                    raise ValueError("Worker 地址格式无效")
                response = await client.get(worker.rstrip("/") + "/api/v1/system", headers={"Authorization": "Bearer " + (credential or "")})
                response.raise_for_status()
                value = response.json()
                if value.get("name") != "Zhilume Worker" or value.get("protocolVersion") != "2.0":
                    raise ValueError("Worker 协议不匹配")
                result["workerReachable"] = True
            except Exception as error:
                result["errors"].append("Worker 检查失败：" + type(error).__name__)
        if config:
            try:
                if config.get("exclusive") is not True:
                    raise ValueError("必须使用专用 ComfyUI")
                response = await client.get(config["url"].rstrip("/") + "/object_info")
                response.raise_for_status()
                for profile in profiles(config):
                    validate_environment(response.json(), profile)
                result["comfyEnvironmentComplete"] = True
            except Exception as error:
                result["errors"].append("ComfyUI 检查失败：" + (str(error) if isinstance(error, ValueError) else type(error).__name__))
    return result


def main():
    parser = argparse.ArgumentParser(description="准备模型软链接和只读连通检查；不下载权重、不提交生成")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model-root", type=Path, action="append")
    parser.add_argument("--comfy-root", type=Path)
    parser.add_argument("--apply-links", action="store_true")
    parser.add_argument("--worker", help="可选 Worker 地址；密钥由 ZHILUME_WORKER_TOKEN 环境变量传入")
    parser.add_argument("--comfy-config", type=Path)
    args = parser.parse_args()
    if bool(args.manifest) != bool(args.comfy_root) or (args.apply_links and not args.manifest):
        parser.error("模型链接需要同时提供 --manifest 和 --comfy-root")
    result = {"python": platform.python_version(), "platform": platform.system(), "ffmpegAvailable": bool(binary()), "gpuJobsSubmitted": False}
    try:
        if args.manifest:
            links = plan_links(json.loads(args.manifest.read_text("utf-8")), args.model_root or [Path("/model"), Path("/models")], args.comfy_root)
            result["links"] = links
            if args.apply_links:
                apply_links(links)
            result["linksReady"] = all(link["status"] in ("ready", "linked") for link in links)
        config = json.loads(args.comfy_config.read_text("utf-8")) if args.comfy_config else None
        result.update(asyncio.run(check_services(args.worker, config, credential=os.environ.get("ZHILUME_WORKER_TOKEN"))))
    except Exception as error:
        result["errors"] = [str(error) if isinstance(error, ValueError) else type(error).__name__]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("errors") or result.get("linksReady") is False:
        sys.exit(2)


if __name__ == "__main__":
    main()
