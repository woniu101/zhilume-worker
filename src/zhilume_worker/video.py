"""H3 media preparation and output checks over the shared Comfy task lifecycle."""
import asyncio
import json
import math
from pathlib import Path

from .comfy import ComfyExecutor
from .processes import run_child
from . import video_workflows as workflows


class VideoExecutor(ComfyExecutor):
    profile_factory = staticmethod(workflows.profiles)
    environment_validator = staticmethod(workflows.validate_environment)
    graph_builder = staticmethod(workflows.build_graph)

    def __init__(self, config, transport=None):
        self.ffmpeg, self.ffprobe = config.get('ffmpeg'), config.get('ffprobe')
        if any(not isinstance(p, str) or not Path(p).is_absolute() or not Path(p).is_file() for p in (self.ffmpeg, self.ffprobe)):
            raise ValueError('H3 需配置存在的 FFmpeg 和 FFprobe 绝对路径')
        super().__init__(config, transport)

    async def probe(self, source, directory, key):
        log = directory / (key + '-probe.json')
        log.write_bytes(b'')
        await run_child([self.ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(source)], directory, log, timeout=120)
        return json.loads(log.read_text('utf-8'))

    async def prepare_inputs(self, profile, operation, request, paths, directory, progress):
        workflows.validate_input(profile['public'], request)
        sources = dict(zip(request['referenceAssetIds'], paths, strict=True))
        prepared = []
        for i, ref in enumerate(request['references']):
            await progress('准备 H3 参考片段')
            source = sources[ref['assetId']]
            if ref['role'] in ('image', 'first', 'last'):
                def image():
                    from PIL import Image
                    with Image.open(source) as img:
                        if img.width * img.height > 20_000_000: raise ValueError('参考图片超过 2000 万像素')
                        img.load()
                await asyncio.to_thread(image)
                prepared.append(source); continue
            info = await self.probe(source, directory, 'input-'+str(i))
            role = ref['role']; streams = [s for s in info['streams'] if s['codec_type'] == role]
            duration = float(streams[0].get('duration') or info.get('format', {}).get('duration', 0)) if streams else 0
            seconds = ref['frames'] / 24
            if not math.isfinite(duration) or ref['start'] + seconds > duration + .03:
                raise ValueError('参考素材缺少对应轨道或指定片段超出实际时长')
            output = directory / (f'h3-reference-{i}' + ('.mp4' if role == 'video' else '.wav'))
            command = [self.ffmpeg, '-nostdin', '-v', 'error', '-y', '-ss', str(ref['start']), '-i', str(source)]
            if role == 'video':
                # Explicitly visual-only. Sound is supplied separately as an audio reference.
                command += ['-map', '0:v:0', '-an', '-vf', 'fps=24,scale=768:768:force_original_aspect_ratio=decrease:force_divisible_by=2', '-frames:v', str(ref['frames']), '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
            else:
                command += ['-map','0:a:0','-vn','-t',str(seconds),'-ac','2','-ar','32000','-c:a','pcm_s16le']
            await run_child(command + [str(output)], directory, directory/'video.log', timeout=120)
            check = await self.probe(output, directory, 'prepared-'+str(i))
            stream = next(s for s in check['streams'] if s['codec_type'] == role)
            if role == 'video' and int(stream.get('nb_read_frames', 0)) != ref['frames']:
                raise ValueError('视频参考帧数不足，未提交 GPU 推理')
            if role == 'audio' and abs(float(stream.get('duration', 0)) - seconds) > .05:
                raise ValueError('音频参考片段不足，未提交 GPU 推理')
            prepared.append(output)
        return prepared

    async def collect_output(self, history, request, directory, progress):
        files = history.get('outputs', {}).get('output', {}).get('images', [])
        if len(files) != 1 or files[0].get('type') != 'output' or not files[0].get('filename', '').endswith('.mp4'):
            raise ValueError('H3 未返回预期 MP4')
        await progress('校验视频与音轨')
        output = directory/'generated.mp4'; size = 0
        async with self.http.stream('GET', '/view', params={k: files[0][k] for k in ('filename','subfolder','type')}) as response:
            response.raise_for_status()
            with output.open('wb') as file:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 256 * 1024**2: raise ValueError('生成视频超过 256 MiB')
                    file.write(chunk)
        info = await self.probe(output, directory, 'output')
        video = [s for s in info['streams'] if s['codec_type'] == 'video']
        audio = [s for s in info['streams'] if s['codec_type'] == 'audio']
        if len(video) != 1 or video[0]['codec_name'] != 'h264' or [video[0]['width'],video[0]['height']] != [request['width'],request['height']] or int(video[0].get('nb_read_frames',0)) != request['frames'] or video[0].get('r_frame_rate') != '24/1':
            raise ValueError('生成视频的尺寸、帧数或编码不符合任务')
        if bool(audio) != request['includeAudio'] or (audio and (len(audio) != 1 or audio[0].get('channels') != 2 or str(audio[0].get('sample_rate')) != '32000')):
            raise ValueError('生成音轨不符合 32kHz 立体声设置')
        return output, '生成视频.mp4'
