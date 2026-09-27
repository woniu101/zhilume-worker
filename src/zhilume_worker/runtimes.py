"""Explicitly owned local ComfyUI processes. External services are never adopted.

Deployment configuration is separate from model execution specifications. A single
runtime can serve both image and video adapters. GPU models still load only via
the task layer's physical GPU lease.
"""
import asyncio
import json
import os
import re
import signal
import socket
from pathlib import Path

import httpx


def validate_runtime(value):
    if not isinstance(value, dict):
        raise ValueError('运行环境须为对象')
    if value.get('type') != 'comfyui':
        raise ValueError('当前仅支持托管 ComfyUI；IndexTTS 已按任务管理独立进程')
    for key in ('python', 'directory', 'modelPathsFile'):
        path = value.get(key, '')
        if not isinstance(path, str) or (key != 'modelPathsFile' and not path) or (path and not Path(path).is_absolute()):
            raise ValueError(f'{key} 须为部署机器上的绝对路径')
    if type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('托管服务端口须为 1024–65535')
    device = value.get('device', '0')
    if not isinstance(device, str) or not re.fullmatch(r'\d+', device):
        raise ValueError('GPU 设备须为可见设备序号，如 0')
    return {k: value.get(k, default) for k, default in (
        ('type', 'comfyui'), ('python', ''), ('directory', ''),
        ('modelPathsFile', ''), ('port', 8188), ('device', '0'))}


def command(config):
    c = validate_runtime(config)
    if not Path(c['python']).is_file() or not (Path(c['directory']) / 'main.py').is_file():
        raise ValueError('Python 或 ComfyUI main.py 不存在；请先配置或安装已有环境')
    args = [c['python'], '-u', str(Path(c['directory']) / 'main.py'),
            '--listen', '127.0.0.1', '--port', str(c['port']),
            '--cuda-device', c['device'], '--use-pytorch-cross-attention',
            '--disable-xformers', '--disable-all-custom-nodes']
    if c['modelPathsFile']:
        if not Path(c['modelPathsFile']).is_file():
            raise ValueError('模型搜索路径配置文件不存在')
        args += ['--extra-model-paths-config', c['modelPathsFile']]
    return args


class RuntimeManager:
    def __init__(self, root):
        self.root = root
        self.path = root / 'config' / 'runtimes.json'
        self.processes = {}
        self.entries = {}
        self.states = {}
        self.error = ''
        try:
            values = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
            if not isinstance(values, dict): raise ValueError()
            for key, value in values.items():
                try:
                    self.validate_id(key)
                    self.entries[key] = validate_runtime(value)
                    self.states[key] = {'state': 'stopped', 'reason': '服务尚未启动；需要显式启动'}
                except ValueError:
                    self.states[key] = {'state': 'error', 'reason': '配置无效，请重新保存此运行环境'}
        except (ValueError, OSError):
            self.error = '运行环境配置无法读取，请修正 config/runtimes.json；其他管理入口仍可使用'

    @staticmethod
    def validate_id(key):
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', key):
            raise ValueError('环境名称须为小写字母开头的字母、数字或连字符，最多 48 字符')

    def snapshot(self):
        for key, process in self.processes.items():
            if process.returncode is not None:
                self.states[key] = {'state': 'error', 'reason': '受管进程已退出，请检查本机运行日志并重新启动'}
        return [{'id': key, 'config': self.entries.get(key, {}), **state}
                for key, state in self.states.items()]

    def configure(self, key, value):
        self.validate_id(key)
        if key in self.processes and self.processes[key].returncode is None:
            raise ValueError('请先停止此运行服务再修改配置')
        config = validate_runtime(value)
        if any(k != key and v['port'] == config['port'] for k, v in self.entries.items()):
            raise ValueError('另一托管环境已使用此端口；图片与视频请共用同一环境')
        self.entries[key] = config
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), 'utf-8')
        temp.chmod(0o600); temp.replace(self.path)
        self.states[key] = {'state': 'stopped', 'reason': '配置已保存，未启动或安装服务'}

    def url(self, key):
        if key not in self.entries: raise ValueError('所选托管环境不存在，请先保存环境配置')
        return f"http://127.0.0.1:{self.entries[key]['port']}"

    async def check(self, key):
        if key not in self.entries: raise ValueError('运行环境不存在')
        args = command(self.entries[key])
        return {'state': 'checked', 'command': args, 'inferenceVerified': False,
                'detail': '程序路径可访问；尚未启动服务或加载模型'}

    async def start(self, key, timeout=90):
        if os.name != 'posix': raise ValueError('托管推理服务暂支持 Linux / WSL2；Windows 可配置和检查')
        await self.check(key)
        if key in self.processes and self.processes[key].returncode is None:
            return {'state': 'running', 'detail': '受管服务已运行'}
        c = self.entries[key]
        # Never reuse an unknown process after a crash or kill it by a saved PID.
        with socket.socket() as probe:
            try: probe.bind(('127.0.0.1', c['port']))
            except OSError: raise ValueError('端口已被占用，不接管或终止已有进程；请改端口或选择复用服务')
        logs = self.root / 'logs'; logs.mkdir(exist_ok=True)
        self.states[key] = {'state': 'starting', 'reason': '正在等待 ComfyUI 就绪；未执行推理'}
        try:
            with (logs / (key + '-runtime.log')).open('ab') as stream:
                p = await asyncio.create_subprocess_exec(*command(c), cwd=c['directory'],
                    stdout=stream, stderr=stream, start_new_session=True,
                    env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'})
            self.processes[key] = p
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                async with asyncio.timeout(timeout):
                    while True:
                        if p.returncode is not None: raise ValueError('ComfyUI 启动失败，请检查本机运行日志')
                        try:
                            r = await client.get(self.url(key) + '/system_stats')
                            if r.status_code == 200 and isinstance(r.json().get('devices'), list): break
                        except (httpx.HTTPError, ValueError): pass
                        await asyncio.sleep(.3)
            self.states[key] = {'state': 'running', 'reason': '服务在线；模型与真实推理需分别验证'}
            return self.states[key]
        except BaseException:
            await self.stop(key)
            self.states[key] = {'state': 'error', 'reason': '启动未完成，已回收受管进程；请检查本机运行日志'}
            raise

    async def stop(self, key):
        p = self.processes.get(key)
        if p:
            # Signal only the process group we created. Also reap descendant
            # processes when the group leader has already exited.
            try: os.killpg(p.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(p.wait(), 8)
            except asyncio.TimeoutError: pass
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            await p.wait()
            self.processes.pop(key, None)
        if key in self.states:
            self.states[key] = {'state': 'stopped', 'reason': '受管进程已停止；执行器资源隔离标记不会被自动清除'}
        return self.states.get(key, {'state': 'stopped'})

    async def close(self):
        for key in list(self.processes): await self.stop(key)
