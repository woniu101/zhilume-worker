"""Versioned, independently authored ComfyUI API graphs; no GPU work on import."""
import hashlib
import json
from pathlib import Path, PurePosixPath

CATALOG = json.loads((Path(__file__).parent / "contracts/operation-catalog.json").read_text("utf-8"))
MODELS = {m["id"]: m for m in CATALOG["imageModels"]}


def profiles(config):
    result = []
    entries = config.get("profiles", [])
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise ValueError("需要 1–16 个模型执行配置")
    for entry in entries:
        model = MODELS.get(entry.get("modelId"))
        if not model:
            raise ValueError("未知图片模型")
        files = entry.get("models", {})
        if set(files) != {"diffusion", "clip", "vae"}:
            raise ValueError("必须配置 diffusion、clip、vae 文件名")
        for name in files.values():
            if not isinstance(name, str) or not name or "\\" in name or ":" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("模型必须使用 ComfyUI 模型列表中的相对文件名")
        refs = entry.get("maxReferences", 4 if model["id"] == "qwen-image-2.1" else 0)
        maximum = entry.get("maxSize", 1536)
        resolution = entry.get("referenceResolution", 1024)
        steps = entry.get("defaultSteps", 25 if model["id"] == "qwen-image-2.1" else 50)
        if type(refs) is not int or not 0 <= refs <= model["referenceLimits"]["maximum"] or (model["id"] == "qwen-image-2512" and refs != 0):
            raise ValueError("参考图数量配置无效")
        if any(type(n) is not int or not 256 <= n <= 2048 or n % 32 for n in (maximum, resolution)) or maximum < 512:
            raise ValueError("执行尺寸应为 32 的倍数，最大不超过 2048")
        if type(steps) is not int or not 1 <= steps <= 100:
            raise ValueError("默认步数应为 1–100")
        operations = [op for op in model["operations"] if op == "image.generate.v1" or refs >= (1 if op == "image.edit.v1" else 2)]
        public = dict(modelId=model["id"], workflowRevision=model["workflowRevision"], operations=operations,
                      maxReferences=refs, formats=model["formats"], minSize=256, maxSize=maximum, sizeStep=32,
                      referenceResolution=resolution, defaultSteps=steps, maxSteps=100, validation="unverified")
        identity = json.dumps({**public, "models": files}, sort_keys=True, separators=(",", ":"))
        public["profileId"] = hashlib.sha256(identity.encode()).hexdigest()
        if any(p["public"]["profileId"] == public["profileId"] for p in result):
            raise ValueError("执行配置重复")
        result.append({"public": public, "models": files})
    return result


def validate_environment(info, profile):
    """Only node and loader inventory checks; never calls /prompt."""
    model = MODELS[profile["public"]["modelId"]]
    missing = [node for node in model["requiredNodes"] if node not in info]
    if missing:
        raise ValueError("ComfyUI 缺少节点：" + ", ".join(missing))
    for node, field, key in (("UNETLoader", "unet_name", "diffusion"), ("CLIPLoader", "clip_name", "clip"), ("VAELoader", "vae_name", "vae")):
        choices = info[node].get("input", {}).get("required", {}).get(field, [None])[0]
        if not isinstance(choices, list) or profile["models"][key] not in choices:
            raise ValueError(f"ComfyUI 模型列表中未找到已配置的 {key} 文件")


def build_graph(profile, operation, request, uploaded, prefix):
    p = profile["public"]
    if operation not in p["operations"] or any(request.get(k) != p[k] for k in ("modelId", "profileId", "workflowRevision")):
        raise ValueError("任务与执行配置不一致")
    minimum = 0 if operation == "image.generate.v1" else 1 if operation == "image.edit.v1" else 2
    maximum = minimum if minimum < 2 else p["maxReferences"]
    if not minimum <= len(uploaded) <= maximum or len(uploaded) != len(request["referenceAssetIds"]):
        raise ValueError("参考图数量不符合工作流")
    if request["outputFormat"] not in p["formats"] or type(request["steps"]) is not int or not 1 <= request["steps"] <= 100:
        raise ValueError("格式或步数不符合执行配置")
    if type(request["seed"]) is not int or not 0 <= request["seed"] <= 9007199254740991:
        raise ValueError("随机种子无效")
    if minimum == 0 and any(type(request.get(k)) is not int or not 256 <= request[k] <= p["maxSize"] or request[k] % 32 for k in ("width", "height")):
        raise ValueError("生成尺寸超出执行配置")
    files = profile["models"]
    graph = {}
    def node(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]
    unet = node("unet", "UNETLoader", unet_name=files["diffusion"], weight_dtype="default")
    clip = node("clip", "CLIPLoader", clip_name=files["clip"], type="qwen_image", device="default")
    vae = node("vae", "VAELoader", vae_name=files["vae"])
    if p["modelId"] == "qwen-image-2512":
        positive = node("positive", "CLIPTextEncode", clip=clip, text=request["prompt"])
        negative = node("negative", "CLIPTextEncode", clip=clip, text=request["negativePrompt"])
        latent = node("latent", "EmptySD3LatentImage", width=request["width"], height=request["height"], batch_size=1)
        unet = node("sampling", "ModelSamplingAuraFlow", model=unet, shift=3.1)
        cfg = 4.0
    else:
        images = {f"images.image_{i + 1}": node(f"reference_{i + 1}", "LoadImage", image=name) for i, name in enumerate(uploaded)}
        positive = node("encode", "TextEncodeQwenImage21", clip=clip, vae=vae, prompt=request["prompt"], negative_prompt=request["negativePrompt"], resolution=p["referenceResolution"], **images)
        negative = ["encode", 1]
        latent = ["encode", 2] if uploaded else node("latent", "EmptyLatentImage", width=request["width"], height=request["height"], batch_size=1)
        cfg = 1.0
    samples = node("sample", "KSampler", model=unet, positive=positive, negative=negative, latent_image=latent,
                   seed=request["seed"], steps=request["steps"], cfg=cfg, sampler_name="euler", scheduler="simple", denoise=1.0)
    pixels = node("decode", "VAEDecode", samples=samples, vae=vae)
    node("output", "SaveImage", images=pixels, filename_prefix=prefix)
    return graph
