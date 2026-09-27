"""Optional llama.cpp executor. Core never imports inference dependencies.

One owned server per task, started only after the physical GPU lease is held.
No model download, external service adoption or resident model between jobs.
"""
import asyncio
import hashlib
import json
import os
import re
import signal
import socket
import sys
from pathlib import Path
import httpx
from .specification import sign
from .resources import hardware


def public_profile(c):
    if not isinstance(c.get('modelId'), str) or not 1 <= len(c['modelId']) <= 160:
        raise ValueError('请填写语言模型标识')
    limits = {}
    for key, default, low, high in [('maxInputCharacters', 6000, 1, 12000), ('maxOutputTokens', 1024, 16, 4096), ('contextSize', 8192, 2048, 32768)]:
        value = c.get(key, default)
        if type(value) is not int or not low <= value <= high: raise ValueError(f'{key} 超出允许范围')
        limits[key] = value
    if limits['maxOutputTokens'] >= limits['contextSize']: raise ValueError('输出预算须小于上下文容量')
    p = sign(dict(modelId=c['modelId'], backend='llama.cpp', workflowRevision='language.llamacpp.v1',
        operations=['text.generate.v1', 'prompt.optimize.v1'], capabilities=['text'], maxImages=0,
        outputFormats=['txt'], validation='unverified', **limits), c.get('identity'))
    for key in ('model', 'binary'):
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', p['identity']['artifacts'].get(key, '')):
            raise ValueError('模型与 llama-server 程序都必须提供实际 SHA256 标识')
    return p


class LanguageExecutor:
    def __init__(self, config):
        self.config, self.profile = config, public_profile(config)
        self.ready, self.process_handle, self.stamps = False, None, {}
        if not re.fullmatch(r'\d+', config.get('device', '0')): raise ValueError('设备须为可见 GPU 序号')
        if type(config.get('port', 8190)) is not int or not 1024 <= config.get('port', 8190) <= 65535: raise ValueError('服务端口无效')

    @property
    def public_profiles(self): return [self.profile] if self.ready else []

    async def check(self):
        def verify():
            for key, artifact in [('binary', 'binary'), ('modelFile', 'model')]:
                p = Path(self.config.get(key, ''))
                if not p.is_absolute() or not p.is_file(): raise ValueError(f'{key} 文件不存在或软链接失效')
                if key == 'binary' and not os.access(p, os.X_OK): raise ValueError('llama-server 缺少执行权限')
                with p.open('rb') as stream:
                    if key == 'modelFile' and stream.read(4) != b'GGUF': raise ValueError('需要单文件 GGUF 模型')
                    stream.seek(0)
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if 'sha256:' + digest != self.profile['identity']['artifacts'][artifact]: raise ValueError(f'{key} 内容与执行规格摘要不符')
                st = p.stat(); self.stamps[key] = (st.st_size, st.st_mtime_ns)
        await asyncio.to_thread(verify)
        self.ready = True

    async def release(self):
        p = self.process_handle
        if p:
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(p.wait(), 5)
            except asyncio.TimeoutError: pass
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await asyncio.wait_for(p.wait(), 5)
            self.process_handle = None
        # Also used on restart to recover persisted quarantine; fail closed if
        # resources cannot be measured or an orphan still owns significant VRAM.
        for _ in range(20):
            gpus = await hardware()
            if gpus and all(int(g['usedMiB']) <= self.config.get('idleVramLimitMiB', 1024) for g in gpus): return
            await asyncio.sleep(.25)
        raise ValueError('语言模型显存释放未确认，请检查本机残留进程')

    async def process(self, operation, source, paths, directory, progress):
        if not self.ready or operation not in self.profile['operations'] or source.get('profileId') != self.profile['profileId'] or source.get('modelId') != self.profile['modelId'] or source.get('workflowRevision') != self.profile['workflowRevision']:
            raise ValueError('语言模型执行规格不匹配')
        if source.get('referenceAssetIds') or paths or source.get('schema'): raise ValueError('本执行器首版仅支持纯文本')
        if not isinstance(source.get('text'), str) or not 0 < len(source['text'].strip()) <= self.profile['maxInputCharacters']: raise ValueError('语言模型输入长度无效')
        if not isinstance(source.get('systemPrompt'), str) or not 0 < len(source['systemPrompt']) <= 20000: raise ValueError('提示词规则无效')
        if sys.platform != 'linux': raise ValueError('语言模型推理当前仅支持 Linux / WSL2')
        for key in ('binary', 'modelFile'):
            st = Path(self.config[key]).stat()
            if self.stamps.get(key) != (st.st_size, st.st_mtime_ns): raise ValueError('程序或模型已变更，请重新检查执行规格')
        port = self.config.get('port', 8190)
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', port))
            except OSError: raise ValueError('语言服务端口已占用；不接管未知进程')
        command = [self.config['binary'], '-m', self.config['modelFile'], '--host', '127.0.0.1', '--port', str(port),
            '--alias', 'zhilume-language', '-c', str(self.profile['contextSize']), '-ngl', '999', '--split-mode', 'none',
            '--device', 'CUDA0', '--parallel', '1', '--no-context-shift', '--jinja']
        env = {k: v for k, v in os.environ.items() if not k.startswith(('LLAMA_ARG_', 'HF_'))}
        env.update(CUDA_VISIBLE_DEVICES=self.config.get('device', '0'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        await progress('加载独立语言模型')
        with (directory / 'language-runtime.log').open('ab') as log:
            launch = asyncio.create_task(asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).with_name('owned_exec.py')), str(os.getpid()), *command,
                cwd=str(directory), env=env, stdout=log, stderr=log, start_new_session=True))
            try: self.process_handle = await asyncio.shield(launch)
            except asyncio.CancelledError:
                self.process_handle = await launch
                await asyncio.shield(self.release())
                raise
        try:
            async with asyncio.timeout(self.config.get('timeoutSeconds', 600)):
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=30, trust_env=False) as client:
                    while True:
                        if self.process_handle.returncode is not None: raise ValueError('语言模型启动失败，请检查执行器日志')
                        try:
                            r = await client.get('/health')
                            if r.status_code == 200 and r.json().get('status') == 'ok': break
                        except (httpx.HTTPError, ValueError): pass
                        await asyncio.sleep(.3)
                    await progress('生成文本')
                    async with client.stream('POST', '/v1/chat/completions', timeout=self.config.get('timeoutSeconds', 600), json={
                        'model': 'zhilume-language', 'stream': False, 'max_tokens': self.profile['maxOutputTokens'],
                        'messages': [{'role': 'system', 'content': source['systemPrompt']}, {'role': 'user', 'content': source['text']}],
                    }) as r:
                        if r.status_code != 200: raise ValueError(f'语言模型服务返回 HTTP {r.status_code}')
                        raw = bytearray()
                        async for chunk in r.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 256000: raise ValueError('语言模型响应过大')
                    result = json.loads(raw); choice = result['choices'][0]
                    content = choice.get('message', {}).get('content')
                    if choice.get('finish_reason') != 'stop' or not isinstance(content, str) or not 0 < len(content.strip()) <= 12000: raise ValueError('语言模型输出不完整或过大')
                    output = directory / 'language.txt'; output.write_text(content, 'utf-8')
                    return output, '语言模型结果.txt'
        finally:
            # Worker keeps its GPU lease until release succeeds or quarantines it.
            await asyncio.shield(self.release())
