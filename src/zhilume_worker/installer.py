"""Explicit isolated Linux installs; never starts services or downloads weights."""
import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
from pathlib import Path
from .processes import run_child

COMFY_REVISION = '79be670e2d9be63e238785af307369d2b9039ed1'
SPEECH_REVISION = 'ee40fa7d6c6b8a2c7f06105f9f1e65775b74868c'
SPEECH_LOCK_SHA256 = '2bcec9c6bd4d20d733bdc0537a2f52b39fd67a2e88ad2308836f56fd72da1283'
PROFILE_ROOT = Path(__file__).parent / 'install_profiles'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(kind, directory, python):
    if kind not in ('image', 'video', 'speech'): raise ValueError('未知执行器')
    if not isinstance(directory, str) or not isinstance(python, str): raise ValueError('安装目录和 Python 路径须为字符串')
    root, interpreter = Path(directory), Path(python)
    if not root.is_absolute() or root.exists() or root.is_symlink(): raise ValueError('安装目录须为尚不存在的绝对路径；失败重试或升级使用新目录，不覆盖旧环境')
    if not interpreter.is_absolute() or not interpreter.is_file(): raise ValueError('请指定已有 Python 的绝对路径')
    root = root.resolve()
    speech = kind == 'speech'
    spec = {'kind': kind, 'directory': str(root), 'python': str(interpreter), 'source': str(root/'source'),
        'environment': str(root/'venv'), 'revision': SPEECH_REVISION if speech else COMFY_REVISION,
        'repository': 'https://github.com/index-tts/index-tts.git' if speech else 'https://github.com/Comfy-Org/ComfyUI.git',
        'profile': 'indextts-upstream-cu128-py311' if speech else 'comfy-cu128-py312',
        'pythonVersion': '3.11' if speech else '3.12', 'platform': 'Linux x86_64 (glibc >= 2.28)',
        'minimumFreeGiB': 24, 'requiredTools': ['git', 'uv'] + (['ffmpeg'] if speech else []),
        'lockSha256': SPEECH_LOCK_SHA256 if speech else digest(PROFILE_ROOT/'comfy-cu128-py312.txt'),
        'validation': 'pending-clean-environment-gpu', 'downloadsWeights': False, 'startsInference': False,
        'steps': ['检查平台、Python、工具和磁盘', '检出固定上游版本', '校验并保存依赖锁', '安装独立环境', '检查依赖和导入', '记录结果，等待模型配置与 GPU 验收']}
    spec['planId'] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    return spec


async def preflight(spec):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise ValueError('标准推理安装暂支持 Linux / WSL2 x86_64；其他平台须另行验收')
    libc, version = platform.libc_ver()
    if libc != 'glibc' or tuple(int(v) for v in version.split('.')[:2]) < (2, 28):
        raise ValueError('标准安装要求 glibc 2.28 或以上')
    missing = [name for name in spec['requiredTools'] if not shutil.which(name)]
    if missing: raise ValueError('缺少基础工具：' + ', '.join(missing) + '；请先显式安装，不自动修改系统')
    p = await asyncio.create_subprocess_exec(spec['python'], '-I', '-c', 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try: stdout, _ = await asyncio.wait_for(p.communicate(), 15)
    except BaseException:
        if p.returncode is None: p.kill()
        await p.wait(); raise
    actual = stdout.decode().strip()
    if p.returncode or actual != spec['pythonVersion']:
        raise ValueError(f"此安装方案要求 Python {spec['pythonVersion']}，当前为 {actual or '无法执行'}；不会自动下载 Python")
    ancestor = Path(spec['directory']).parent
    while not ancestor.exists(): ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    if free < spec['minimumFreeGiB'] * 1024**3:
        raise ValueError(f"安装所在磁盘至少需 {spec['minimumFreeGiB']} GiB 空闲，目前 {free / 1024**3:.1f} GiB；未创建安装目录")
    return {'pythonVersion': actual, 'freeGiB': round(free / 1024**3, 1), 'platform': platform.platform()}


def record(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    temp.chmod(0o600); temp.replace(path)


async def install(kind, directory, python, log, *, expected_plan=None, progress=None):
    spec = plan(kind, directory, python)
    if expected_plan is not None and expected_plan != spec['planId']: raise ValueError('安装计划已变化，请重新查看后执行')
    checks = await preflight(spec)
    root = Path(spec['directory']); root.mkdir(parents=True, exist_ok=False)
    log = Path(log).resolve(); log.parent.mkdir(parents=True, exist_ok=True)
    marker = root/'installation.json'
    state = {**spec, 'state': 'installing', 'stage': '准备安装', 'preflight': checks, 'inferenceVerified': False}
    # Cache stays on the checked disk, separate from source and venv. Do not
    # inherit import paths or user configuration into the independent environment.
    env = {'GIT_LFS_SKIP_SMUDGE': '1', 'GIT_TERMINAL_PROMPT': '0', 'UV_PROJECT_ENVIRONMENT': str(root/'venv'),
        'UV_CACHE_DIR': str(root/'package-cache'), 'UV_NO_MANAGED_PYTHON': '1', 'UV_PYTHON_DOWNLOADS': 'never',
        'UV_NO_CONFIG': '1', 'UV_NO_ENV_FILE': '1', 'PYTHONPATH': '', 'PYTHONHOME': '', 'PYTHONNOUSERSITE': '1',
        'PIP_CONFIG_FILE': os.devnull, 'UV_HTTP_TIMEOUT': '120'}
    async def step(title, command, cwd=None):
        state['stage'] = title; record(marker, state)
        if progress: progress(title)
        try:
            await run_child(command, cwd or root, log, timeout=180 if command[0] == 'git' else 3600, env=env)
        except TimeoutError:
            raise ValueError(f'安装阶段“{title}”超时；请检查网络和本机 {log.name}，使用新目录重试') from None
        except ValueError:
            raise ValueError(f'安装阶段“{title}”失败；请检查本机 {log.name}，使用新目录重试') from None
    try:
        record(marker, state)
        source = root/'source'; interpreter = root/'venv/bin/python'
        await step('检出固定上游', ['git', 'init', str(source)])
        await step('检出固定上游', ['git', '-C', str(source), 'remote', 'add', 'origin', spec['repository']])
        await step('下载程序代码', ['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', spec['revision']])
        await step('检出固定上游', ['git', '-C', str(source), 'checkout', '--detach', 'FETCH_HEAD'])
        lock = source/'uv.lock' if kind == 'speech' else PROFILE_ROOT/'comfy-cu128-py312.txt'
        if digest(lock) != spec['lockSha256']: raise ValueError('依赖锁哈希不一致，停止安装')
        shutil.copyfile(lock, root/'dependencies.lock')
        if kind == 'speech':
            await step('安装 IndexTTS 锁定依赖', ['uv', 'sync', '--frozen', '--no-dev', '--no-managed-python', '--python', python], source)
        else:
            await step('创建独立 Python 环境', ['uv', 'venv', '--no-managed-python', '--python', python, str(root/'venv')])
            await step('安装 ComfyUI 锁定依赖', ['uv', 'pip', 'sync', '--python', str(interpreter), '--require-hashes', '--torch-backend', 'cu128', str(root/'dependencies.lock')])
        await step('检查依赖一致性', ['uv', 'pip', 'check', '--python', str(interpreter)])
        imports = 'import torch,torchaudio,transformers,numpy,soundfile,omegaconf' if kind == 'speech' else 'import torch,torchvision,torchaudio,transformers,numpy,PIL,av,comfy_kitchen'
        await step('检查基础模块导入（不加载模型）', [str(interpreter), '-I', '-c', imports])
        inventory = root/'installed-requirements.txt'
        await run_child(['uv', 'pip', 'freeze', '--python', str(interpreter)], root, inventory, timeout=60, env=env)
        state.update(state='installed-unchecked', stage='依赖安装完成，尚未检查模型或运行推理', dependencyInventory=inventory.name)
        record(marker, state)
        return {'state': state['state'], 'directory': str(root), 'python': str(interpreter), 'source': str(source), 'lockSha256': spec['lockSha256'], 'inferenceVerified': False, 'next': state['stage']}
    except BaseException as error:
        state.update(state='cancelled' if isinstance(error, asyncio.CancelledError) else 'incomplete', error='安装已取消，已有环境未改动' if isinstance(error, asyncio.CancelledError) else '安装未完成，请查看本机安装日志；使用新目录重试')
        try: record(marker, state)
        except OSError: pass  # Preserve the original cause, including a full disk.
        raise


def main():
    p = argparse.ArgumentParser(description='独立执行器安装；默认仅输出计划，不下载依赖或运行模型')
    p.add_argument('--executor', required=True, choices=['image', 'video', 'speech']); p.add_argument('--directory', required=True)
    p.add_argument('--python', required=True); p.add_argument('--execute', action='store_true'); p.add_argument('--log', default='executor-install.log')
    a = p.parse_args()
    try:
        result = asyncio.run(install(a.executor, a.directory, a.python, Path(a.log))) if a.execute else plan(a.executor, a.directory, a.python)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error: p.exit(1, str(error)+'\n')
    except KeyboardInterrupt: p.exit(130, '安装已取消，重试请使用新目录\n')


if __name__ == '__main__': main()
