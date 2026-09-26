"""CPU-only media execution. No model imports or GPU allocation."""
import asyncio
import math
import os
import shutil
from pathlib import Path

OPERATIONS = ("media.video.trim.v1", "media.audio.extract.v1")


def binary():
    return shutil.which(os.environ.get("ZHILUME_FFMPEG", "ffmpeg"))


def arguments(operation, source, output, start, end):
    if operation not in OPERATIONS or any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in (start, end)) or not 0 <= start < end <= 86400:
        raise ValueError("无效的媒体操作或截取范围")
    # Decode before seeking for frame-accurate trim; pad odd image sizes for yuv420p.
    args = ["-hide_banner", "-nostdin", "-y", "-i", str(source), "-ss", str(start), "-t", str(end - start)]
    if operation == OPERATIONS[0]:
        args += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]
    else:
        args += ["-map", "0:a:0", "-vn", "-c:a", "pcm_s16le"]
    return args + ["-threads", "2", "-progress", "pipe:1", "-nostats", str(output)]


async def process(operation, source: Path, directory: Path, params, progress):
    executable = binary()
    if not executable:
        raise ValueError("未安装 FFmpeg，CPU 媒体能力不可用")
    output = directory / ("clip.mp4" if operation == OPERATIONS[0] else "audio.wav")
    args = arguments(operation, source, output, params["start"], params["end"])
    process = await asyncio.create_subprocess_exec(executable, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    # Drain stderr concurrently but retain only the tail, even for very long inputs.
    errors = bytearray()
    async def drain():
        while chunk := await process.stderr.read(4096):
            errors.extend(chunk)
            if len(errors) > 8192:
                del errors[:-8192]
    reader = asyncio.create_task(drain())
    try:
        while line := await process.stdout.readline():
            if line.startswith(b"out_time_us="):
                try:
                    value = int(line.split(b"=")[1]) / 1_000_000 / (params["end"] - params["start"])
                    await progress(min(.95, max(0, value)))
                except ValueError:
                    pass
        code = await process.wait()
        await reader
        if code or not output.exists() or output.stat().st_size < 64:
            raise ValueError("媒体处理失败，请检查素材编码、音轨和截取范围")
        return output, output.name
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        await reader
