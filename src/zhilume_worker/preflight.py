"""Read-only ComfyUI/model readiness report. Never submits prompts or allocates GPU."""
import argparse
import asyncio
import json
from pathlib import Path
import httpx

CATALOG = json.loads((Path(__file__).parent / "contracts/operation-catalog.json").read_text("utf-8"))


def inspect_models(roots, profile):
    result = {}
    for category, pattern in profile["modelPatterns"].items():
        matches = []
        for root in roots:
            if root.exists():
                # Match real mounted files; a catalog path is not proof of a mount.
                matches.extend(str(p.resolve()) for p in root.rglob(pattern) if p.is_file() and p.suffix == ".safetensors")
        result[category] = sorted(set(matches))
    return result


async def report(url, roots):
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url.rstrip("/") + "/object_info")
        response.raise_for_status()
        nodes = response.json()
    profiles = []
    for profile in CATALOG["imageModels"]:
        files = inspect_models(roots, profile)
        missing = [name for name in profile["requiredNodes"] if name not in nodes]
        profiles.append({"id": profile["id"], "operations": profile["operations"], "files": files, "missingNodes": missing,
                         "environmentComplete": not missing and all(files.values()), "inferenceVerified": False, "executable": False})
    return {"catalogVersion": CATALOG["version"], "gpuStarted": False, "profiles": profiles}


def main():
    parser = argparse.ArgumentParser(description="只读检查 ComfyUI 节点和已挂载模型，不运行 GPU、不下载模型")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--model-root", action="append", type=Path, help="可重复传入实际模型挂载目录；默认 /model 和 /models")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(report(args.comfy_url, args.model_root or [Path('/model'), Path('/models')])), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
