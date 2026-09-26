"""Prepare a dedicated ComfyUI installation without loading weights or starting GPU jobs."""
import argparse
import asyncio
import json
import platform
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .image_workflows import profiles, validate_environment
from .media import binary
from .model_links import plan_links, apply_links


async def check_services(server, config, transport=None):
    result = {"serverChecked": bool(server), "comfyChecked": bool(config), "serverReachable": False, "comfyEnvironmentComplete": False, "inferenceVerified": False, "errors": []}
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        if server:
            try:
                endpoint = urlsplit(server)
                if endpoint.scheme not in ("http", "https") or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                    raise ValueError("Server 地址格式无效")
                response = await client.get(server.rstrip("/") + "/api/v1/system")
                response.raise_for_status()
                value = response.json()
                if value.get("name") != "Zhilume Server" or value.get("protocolVersion") != "1.0":
                    raise ValueError("Server 协议不匹配")
                result["serverReachable"] = True
            except Exception as error:
                result["errors"].append("Server 检查失败：" + type(error).__name__)
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
    parser.add_argument("--server")
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
        result.update(asyncio.run(check_services(args.server, config)))
    except Exception as error:
        result["errors"] = [str(error) if isinstance(error, ValueError) else type(error).__name__]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("errors") or result.get("linksReady") is False:
        sys.exit(2)


if __name__ == "__main__":
    main()
