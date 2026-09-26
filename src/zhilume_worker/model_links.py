"""Explicit model links only; no model import, download or filesystem overwrite."""
from pathlib import Path, PurePosixPath


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("模型路径须为相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("模型路径不能离开指定目录")
    return path


def plan_links(manifest, roots, comfy_root):
    if not isinstance(manifest.get("links"), list) or not 1 <= len(manifest["links"]) <= 64:
        raise ValueError("模型清单需要 1–64 个链接")
    destination_root = (comfy_root / "models").resolve()
    links, destinations = [], set()
    for entry in manifest["links"]:
        destination = destination_root / relative(entry["target"])
        # Check parent rather than destination so an existing correct symlink is allowed.
        if not destination.parent.resolve().is_relative_to(destination_root) or destination in destinations:
            raise ValueError("链接目标重复或离开 ComfyUI 模型目录")
        destinations.add(destination)
        candidates = [root / relative(source) for root in roots for source in entry["sources"]]
        matches = sorted({p.resolve() for p in candidates if p.is_file() and p.stat().st_size > 0})
        source = matches[0] if len(matches) == 1 else None
        status = "ready" if source else "missing" if not matches else "ambiguous"
        if destination.exists() or destination.is_symlink():
            status = "linked" if source and destination.is_symlink() and destination.resolve() == source else "conflict"
        links.append({"source": str(source) if source else None, "target": str(destination), "status": status, "matches": [str(p) for p in matches]})
    return links


def apply_links(links):
    if any(link["status"] not in ("ready", "linked") for link in links):
        raise ValueError("存在缺失、歧义或冲突文件，未创建链接")
    created = []
    try:
        for link in links:
            if link["status"] == "linked":
                continue
            target = Path(link["target"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link["source"])
            created.append(target)
    except Exception:
        for target in reversed(created):
            target.unlink()  # Roll back only symlinks created by this call.
        raise


