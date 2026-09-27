"""Linux core release lifecycle. Also runnable directly before Worker is installed.

No inference installation, downloads of weights, instance management or service
startup. Activation holds the same state lock as the Worker and is refused while
it is running. Releases are never removed by this tool.
"""
import argparse
import contextlib
import email
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    temp.chmod(0o600); temp.replace(path)


@contextlib.contextmanager
def exclusive(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        try: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: raise ValueError('服务或另一部署操作正在运行；请先停止服务再切换程序')
        try: yield
        finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Releases:
    def __init__(self, program, state):
        if os.name != 'posix': raise ValueError('发布安装与切换工具暂支持 Linux / WSL2')
        self.program, self.state = Path(program).resolve(), Path(state).resolve()
        if self.program == self.state or self.program in self.state.parents or self.state in self.program.parents:
            raise ValueError('程序与持久数据目录必须分开，不能互相嵌套')
        self.versions = self.program / 'versions'
        self.metadata = self.program / 'deployment.json'

    def initialize(self):
        for part in ('config', 'credentials', 'attempts', 'logs'):
            (self.state / part).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.versions.mkdir(parents=True, exist_ok=True)

    def status(self):
        data = json.loads(self.metadata.read_text('utf-8')) if self.metadata.exists() else {}
        current = self.program / 'current'
        if current.is_symlink():
            actual = current.resolve().parent / 'release.json'
            if actual.is_file(): data['current'] = json.loads(actual.read_text('utf-8'))['version']
        data.update(program=str(self.program), state=str(self.state),
                    releases=[p.name for p in self.versions.iterdir() if (p / 'release.json').exists()] if self.versions.exists() else [])
        return data

    def install(self, wheel, python='3.12'):
        wheel = Path(wheel).resolve()
        with zipfile.ZipFile(wheel) as archive:
            metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(metadata) != 1: raise ValueError('wheel 元数据无效')
            info = email.message_from_bytes(archive.read(metadata[0]))
        version = info['Version'] or ''
        if info['Name'] != 'zhilume-worker' or not re.fullmatch(r'[0-9][a-zA-Z0-9.+-]{0,60}', version):
            raise ValueError('请选择 Zhilume Worker 发布 wheel')
        uv = shutil.which('uv')
        if not uv: raise ValueError('请先显式安装 uv，并提供 Python 3.11 或更高版本')
        self.initialize()
        target = self.versions / version
        with exclusive(self.program / 'deployment.lock'):
            if target.exists(): raise ValueError('此版本目录已存在；不覆盖旧程序或失败安装，请使用新的版本目录')
            target.mkdir()
            manifest = {'version': version, 'wheelSha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'status': 'installing'}
            write_json(target / 'installation.json', manifest)
            try:
                subprocess.run([uv, 'venv', '--no-managed-python', '--python', python, str(target / 'venv')], check=True)
                interpreter = target / 'venv/bin/python'
                subprocess.run([uv, 'pip', 'install', '--python', str(interpreter), str(wheel)], check=True)
                subprocess.run([str(interpreter), '-c', 'from zhilume_worker.gateway import create_app; from importlib.resources import files; assert files("zhilume_worker").joinpath("web/index.html").is_file()'], check=True)
                packages = subprocess.run([uv, 'pip', 'freeze', '--python', str(interpreter)], check=True, capture_output=True, text=True).stdout
                (target / 'installed-requirements.txt').write_text(packages, 'utf-8')
                write_json(target / 'release.json', {**manifest, 'status': 'ready'})
                write_json(target / 'installation.json', {**manifest, 'status': 'ready'})
            except BaseException:
                write_json(target / 'installation.json', {**manifest, 'status': 'incomplete'})
                raise
        return {'version': version, 'status': 'installed', 'activated': False,
                'next': '显式 activate 后启动服务；未安装推理环境或下载模型'}

    def activate(self, version):
        if not re.fullmatch(r'[0-9][a-zA-Z0-9.+-]{0,60}', version): raise ValueError('版本无效')
        self.initialize()
        target = self.versions / version
        if target.resolve().parent != self.versions.resolve() or not (target / 'release.json').is_file():
            raise ValueError('版本未完成安装')
        with exclusive(self.program / 'deployment.lock'), exclusive(self.state / 'service.lock'):
            data = self.status()
            if data.get('current') == version: return data
            current = self.program / 'current'
            if current.exists() and not current.is_symlink(): raise ValueError('current 不是程序链接，拒绝覆盖')
            temp = self.program / '.current-next'
            if temp.is_symlink(): temp.unlink()
            temp.symlink_to(target / 'venv', target_is_directory=True)
            os.replace(temp, current)
            write_json(self.metadata, {'current': version, 'previous': data.get('current')})
        return self.status()

    def rollback(self):
        version = self.status().get('previous')
        if not version: raise ValueError('没有上一版本可回退')
        return self.activate(version)

    def service(self, user, lock_directory):
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user): raise ValueError('系统服务用户名无效')
        # systemd specifiers and quoted paths must not be interpreted as commands.
        def quoted(path):
            text = str(path)
            if any(c in text for c in '\n\r\x00'): raise ValueError('目录包含无效字符')
            return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        return '\n'.join(['[Unit]', 'Description=Zhilume Worker', 'After=network-online.target',
            '[Service]', 'Type=simple', f'User={user}',
            'WorkingDirectory=' + quoted(self.state),
            'ExecStart=' + quoted(self.program / 'current/bin/zhilume-worker') + ' --state ' + quoted(self.state),
            'Environment=' + quoted('ZHILUME_RESOURCE_LOCK_DIR=' + str(Path(lock_directory).resolve())),
            'Restart=on-failure', 'RestartSec=5', 'KillMode=control-group', 'TimeoutStopSec=90', 'UMask=0077',
            '[Install]', 'WantedBy=multi-user.target', ''])


def main():
    parser = argparse.ArgumentParser(description='Worker 核心安装、初始化、切换与回退；不启动服务或推理')
    parser.add_argument('action', choices=['install', 'initialize', 'activate', 'rollback', 'status', 'service'])
    parser.add_argument('--program', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--wheel'); parser.add_argument('--python', default='3.12')
    parser.add_argument('--version'); parser.add_argument('--user', default='zhilume')
    parser.add_argument('--lock-directory', default='/var/lib/zhilume-gpu-locks')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        release = Releases(args.program, args.state)
        if args.action == 'install':
            if not args.wheel: parser.error('install 需要 --wheel')
            result = release.install(args.wheel, args.python)
        elif args.action == 'activate':
            if not args.version: parser.error('activate 需要 --version')
            result = release.activate(args.version)
        elif args.action == 'initialize': release.initialize(); result = release.status()
        elif args.action == 'rollback': result = release.rollback()
        elif args.action == 'service': print(release.service(args.user, args.lock_directory)); return
        else: result = release.status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__': main()
