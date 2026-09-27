"""IndexTTS adapter. Inference lives in its own Python environment and owned process."""
import asyncio
import hashlib
import json
import math
import os
import subprocess
import wave
from .processes import run_child
from pathlib import Path

MODEL = json.loads((Path(__file__).parent / 'contracts/operation-catalog.json').read_text('utf-8'))['speechModels'][0]


def public_profile(config):
    limit = config.get('maxTextCharacters', 1000)
    if type(limit) is not int or not 1 <= limit <= MODEL['maxTextCharacters'] or type(config.get('enableEmotionText', False)) is not bool:
        raise ValueError('语音配置无效')
    fingerprint = hashlib.sha256(json.dumps({'config': config, 'workflow': MODEL['workflowRevision'], 'upstream': MODEL['upstreamRevision']}, sort_keys=True).encode()).hexdigest()
    return dict(modelId=MODEL['id'], profileId=fingerprint, workflowRevision=MODEL['workflowRevision'], upstreamRevision=MODEL['upstreamRevision'],
                maxTextCharacters=limit, languages=MODEL['languages'], emotionModes=MODEL['emotionModes'] if config.get('enableEmotionText') else ['follow', 'reference', 'vector'], validation='unverified')


def validate_input(value, profile):
    def number(v, low, high):
        return type(v) in (int, float) and math.isfinite(v) and low <= v <= high
    if any(value.get(k) != profile[k] for k in ('modelId', 'profileId', 'workflowRevision')):
        raise ValueError('语音模型配置不匹配')
    if not isinstance(value.get('text'), str) or not value['text'].strip() or len(value['text']) > profile['maxTextCharacters']:
        raise ValueError('合成文字为空或超过配置上限')
    if value.get('language') not in profile['languages'] or value.get('emotionMode') not in profile['emotionModes'] or not number(value.get('speed'), .5, 2) or not number(value.get('emotionAlpha'), 0, 1):
        raise ValueError('语音参数不受支持')
    refs = []
    for clip in [value.get('speaker')] + ([value.get('emotionReference')] if value['emotionMode'] == 'reference' else []):
        if not isinstance(clip, dict) or not isinstance(clip.get('assetId'), str) or not clip['assetId'] or not number(clip.get('start'), 0, 86400) or not number(clip.get('end'), 0, 86400) or not 1 <= clip['end'] - clip['start'] <= 30:
            raise ValueError('参考音频片段应为 1–30 秒')
        if clip['assetId'] not in refs:
            refs.append(clip['assetId'])
    if refs != value.get('referenceAssetIds'):
        raise ValueError('语音参考素材角色不匹配')
    if value['emotionMode'] == 'text' and (not isinstance(value.get('emotionText'), str) or not 0 < len(value['emotionText'].strip()) <= 500):
        raise ValueError('情绪描述无效')
    vector = value.get('emotionVector')
    if value['emotionMode'] == 'vector' and (not isinstance(vector, list) or len(vector) != 8 or any(not number(n, 0, 1) for n in vector)):
        raise ValueError('情绪向量无效')
    if value.get('outputFormat') != 'wav':
        raise ValueError('语音输出必须为 WAV')


class SpeechExecutor:
    def __init__(self, config):
        self.config = config
        self.profile = public_profile(config)
        self.ready = False

    @classmethod
    def from_file(cls, filename):
        return cls(json.loads(Path(filename).read_text('utf-8')))

    @property
    def public_profiles(self):
        return [self.profile] if self.ready else []

    async def check(self):
        # Read-only: no imports of torch or inference model, no downloads and no CUDA allocation.
        def verify():
            for key in ('python', 'repository', 'modelDirectory', 'ffmpeg'):
                path = Path(self.config.get(key, ''))
                if not path.is_absolute() or not (path.is_file() if key in ('python', 'ffmpeg') else path.is_dir()):
                    raise ValueError(f'语音配置 {key} 必须为存在的绝对路径')
            repo, models = Path(self.config['repository']), Path(self.config['modelDirectory'])
            revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
            if revision != MODEL['upstreamRevision']:
                raise ValueError('IndexTTS 代码版本不匹配，请使用固定提交')
            required = ['config.yaml', 'gpt.pth', 's2mel.pth', 'codec.pth', 'wav2vec2bert_stats.pt', 'feat1.pt', 'feat2.pt', 'multilingual_zh_ja_yue_char_del.tiktoken',
                        'hf_cache/w2v-bert-2.0/config.json', 'hf_cache/bigvgan/config.json', 'hf_cache/campplus_cn_common.bin']
            if self.config.get('enableEmotionText'):
                required += ['qwen0.6bemo4-merge/config.json', 'qwen0.6bemo4-merge/tokenizer.json',
                             'qwen0.6bemo4-merge/tokenizer_config.json', 'qwen0.6bemo4-merge/chat_template.jinja']
            missing = [name for name in required if not (models / name).is_file() or (models / name).stat().st_size == 0]
            if missing:
                raise ValueError('IndexTTS 模型文件缺失或为空：' + ', '.join(missing) + '；只读检查不会自动下载')
            for folder in ['hf_cache/w2v-bert-2.0', 'hf_cache/bigvgan'] + (['qwen0.6bemo4-merge'] if self.config.get('enableEmotionText') else []):
                if not any(p.is_file() for pattern in ('*.bin', '*.safetensors', '*.pth', '*.pt') for p in (models / folder).glob(pattern)):
                    raise ValueError('IndexTTS 辅助模型只有配置，没有权重')
            if not (repo / 'indextts/infer_v2_5.py').is_file():
                raise ValueError('IndexTTS 2.5 推理入口缺失')
        await asyncio.to_thread(verify)
        self.ready = True

    run_child = staticmethod(run_child)

    async def process(self, operation, source, paths, directory, progress):
        if operation != 'audio.speech.v1' or not self.ready:
            raise ValueError('语音执行器未就绪')
        validate_input(source, self.profile)
        await progress('准备音色与情绪参考')
        source_paths = dict(zip(source['referenceAssetIds'], paths, strict=True))
        clips = {}
        for role in ['speaker'] + (['emotionReference'] if source['emotionMode'] == 'reference' else []):
            clip = source[role]
            output = directory / (role + '.wav')
            await self.run_child([self.config['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-ss', str(clip['start']), '-i', str(source_paths[clip['assetId']]),
                '-t', str(clip['end'] - clip['start']), '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(output)], directory, directory / 'speech.log', timeout=60)
            with wave.open(str(output)) as audio:
                if not 1 <= audio.getnframes() / audio.getframerate() <= 30.05:
                    raise ValueError('实际参考片段不足 1 秒或超过 30 秒，请调整截取范围')
            clips[role] = str(output)
        output = directory / 'speech.wav'
        request = directory / 'speech-request.json'
        request.write_text(json.dumps({'config': self.config, 'input': source, 'clips': clips, 'output': str(output), 'parentPid': os.getpid()}), 'utf-8')
        await progress('加载 IndexTTS 并合成语音')
        await self.run_child([self.config['python'], str(Path(__file__).with_name('speech_runner.py')), str(request)], self.config['repository'], directory / 'speech.log')
        await progress('校验语音结果')
        if not output.is_file() or output.stat().st_size > 64 * 1024 ** 2:
            raise ValueError('语音输出缺失或过大')
        with wave.open(str(output)) as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getnframes() <= 0 or audio.getframerate() != 24000:
                raise ValueError('语音输出应为 24kHz 单声道 PCM16 WAV')
        return output, '语音合成.wav'
