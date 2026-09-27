"""Read-only deployment checks shared by the CLI and management API.

No inference imports, downloads or GPU allocations. ComfyUI owns its own
Python environment and model search paths; the adapter talks to its HTTP API.
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
import httpx


def defaults():
    return json.loads(Path(__file__).with_name('deployment-defaults.json').read_text('utf-8'))


def validate_structure(kind, config):
    if kind not in ('image', 'speech', 'video') or not isinstance(config, dict):
        raise ValueError('执行器配置须为对象')
    if config.get('runtimeId'):
        from .runtimes import RuntimeManager
        RuntimeManager.validate_id(config['runtimeId'])
        if kind == 'speech': raise ValueError('IndexTTS 使用自己的独立 Python 进程，不关联 ComfyUI 服务')
    strings = ('python', 'repository', 'modelDirectory', 'workingDirectory', 'device', 'url', 'ffmpeg', 'ffprobe')
    for key in strings:
        if key in config and not isinstance(config[key], str): raise ValueError(f'{key} 须为文本')
    for key in ('exclusive', 'enableEmotionText'):
        if key in config and type(config[key]) is not bool: raise ValueError(f'{key} 须为开关')
    for key, low, high in [('timeoutSeconds', 1, 7200), ('idleVramLimitMiB', 256, 4096), ('maxTextCharacters', 1, 1000)]:
        if key in config and (type(config[key]) is not int or not low <= config[key] <= high): raise ValueError(f'{key} 应为 {low}–{high} 的整数')
    if config.get('workingDirectory') == '': config.pop('workingDirectory')
    if kind != 'speech' and 'profiles' in config:
        if not isinstance(config['profiles'], list) or len(config['profiles']) > 16 or any(not isinstance(p, dict) for p in config['profiles']):
            raise ValueError('模型配置须为对象列表，最多 16 项')
        for profile in config['profiles']:
            if 'models' in profile and (not isinstance(profile['models'], dict) or any(not isinstance(v, str) for v in profile['models'].values())):
                raise ValueError('模型文件名须为文本')

    entries = [config] if kind == 'speech' else config.get('profiles', [])
    for entry in entries:
        if 'identity' in entry:
            identity = entry['identity']
            if not isinstance(identity, dict) or any(k in identity and not isinstance(identity[k], str) for k in ('revision', 'quantization')):
                raise ValueError('模型版本与量化须为文本')
            if 'artifacts' in identity and (not isinstance(identity['artifacts'], dict) or any(not isinstance(v, str) for v in identity['artifacts'].values())):
                raise ValueError('组件固定标识须为文本映射')
        if 'sizes' in entry and (not isinstance(entry['sizes'], list) or any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v) for v in entry['sizes'])):
            raise ValueError('输出尺寸须为宽高整数对的列表')
        if 'frames' in entry and (not isinstance(entry['frames'], list) or any(type(v) is not int for v in entry['frames'])):
            raise ValueError('帧数须为整数列表')
        if 'referenceLimits' in entry and (not isinstance(entry['referenceLimits'], dict) or any(type(v) is not int for v in entry['referenceLimits'].values())):
            raise ValueError('参考数量须为整数映射')


async def inspect_environment(kind, config, items):
    async def step(key, title, fix, operation):
        row = dict(id=key, title=title, state='checking', detail='', remedy=fix)
        items.append(row)
        try:
            detail = await operation()
            row.update(state='passed', detail=detail or '检查通过', remedy='')
        except Exception as error:
            # Do not expose HTTP request URLs/credentials or arbitrary traceback text.
            detail = str(error) if isinstance(error, ValueError) else f'{type(error).__name__}：检查未通过'
            row.update(state='failed', detail=detail)

    async def path_check(key, directory=False, required=True):
        value = config.get(key, '')
        if not value and not required: return '未设置，使用程序默认目录'
        path = Path(value)
        def check():
            if not value or not path.is_absolute(): raise ValueError('请填写部署机器上的绝对路径')
            if path.is_symlink() and not path.exists(): raise ValueError('软链接目标不存在')
            if not (path.is_dir() if directory else path.is_file()): raise ValueError('目录不存在' if directory else '文件不存在或软链接失效')
            if not os.access(path, os.R_OK): raise ValueError('当前服务用户没有读取权限')
            return '路径可访问（软链接有效）' if path.is_symlink() else '路径可访问'
        return await asyncio.to_thread(check)

    if kind == 'speech':
        for key, title, directory, required in [('python', 'Python 程序', False, True), ('repository', 'IndexTTS 程序目录', True, True), ('modelDirectory', '模型目录', True, True), ('workingDirectory', '工作目录', True, False), ('ffmpeg', 'FFmpeg 程序', False, True)]:
            await step(key, title, '在环境表单修正路径，并检查服务用户权限、挂载及软链接目标。', lambda key=key, directory=directory, required=required: path_check(key, directory, required))
        async def dependencies():
            if any(x['id'] == 'python' and x['state'] == 'failed' for x in items): raise ValueError('Python 路径无效，未运行依赖检查')
            code = "import importlib.util,json; print(json.dumps([n for n in ['torch','torchaudio','transformers','omegaconf','numpy','soundfile'] if importlib.util.find_spec(n) is None]))"
            process = await asyncio.create_subprocess_exec(config['python'], '-I', '-c', code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            try: out, _ = await asyncio.wait_for(process.communicate(), 15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                process.kill(); await process.wait(); raise
            if process.returncode: raise ValueError('Python 无法执行只读依赖检查')
            missing = json.loads(out)
            if missing: raise ValueError('缺少依赖：' + ', '.join(missing))
            return '基础依赖可发现；未加载模型，完整运行仍需真实推理验收'
        await step('dependencies', 'Python 基础依赖', '选择已安装 IndexTTS 依赖的独立 Python 环境；依赖安装须单独执行。', dependencies)
    else:
        async def service():
            url = config.get('url', '')
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError('服务地址须为 HTTP(S) URL，不含凭证、查询参数或片段')
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(url.rstrip('/') + '/system_stats'); response.raise_for_status()
                if not isinstance(response.json().get('devices'), list): raise ValueError('地址未返回有效的 ComfyUI 服务信息')
            return 'ComfyUI 可访问；GPU、Python 和模型目录由该服务管理'
        await step('service', 'ComfyUI 服务', '检查服务是否启动、监听地址与访问路径；此处填写 ComfyUI 地址，不是 Worker 地址。', service)
        if kind == 'video':
            for key in ('ffmpeg', 'ffprobe'):
                await step(key, key.upper() + ' 程序', '填写 Worker 部署机器上已有程序的绝对路径。', lambda key=key: path_check(key))
    return not any(x['state'] == 'failed' for x in items)
