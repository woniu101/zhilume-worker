"""Physical GPU identity and cross-process exclusion, independent of Worker identity."""
import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path


async def hardware():
    try:
        p = await asyncio.create_subprocess_exec('nvidia-smi', '--query-gpu=uuid,name,memory.total,memory.used', '--format=csv,noheader,nounits', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(p.communicate(), 5)
        except asyncio.TimeoutError:
            p.kill(); await p.wait(); return []
        return [dict(zip(('uuid', 'name', 'totalMiB', 'usedMiB'), [v.strip() for v in line.split(',')])) for line in out.decode().splitlines() if line.startswith('GPU-')]
    except (FileNotFoundError, OSError):
        return []


class ResourceLease:
    def __init__(self, resource_ids, owner=None, recovery=False):
        self.ids, self.files = sorted(set(resource_ids)), []
        self.owner, self.recovery, self.markers = owner, recovery, []

    @staticmethod
    def directory():
        return Path(os.environ.get('ZHILUME_RESOURCE_LOCK_DIR', '/tmp/zhilume-gpu-locks' if os.name != 'nt' else str(Path(tempfile.gettempdir()) / 'zhilume-gpu-locks')))

    @classmethod
    def quarantine_owners(cls, resource_ids):
        owners = set()
        for resource in resource_ids:
            marker = cls.directory() / (hashlib.sha256(resource.encode()).hexdigest() + '.quarantine')
            if marker.exists(): owners.add(marker.read_text())
        return owners

    async def acquire(self):
        # All local Workers must share this host directory; containers mount it from host.
        directory = self.directory()
        directory.mkdir(parents=True, exist_ok=True)
        try:
            for resource in self.ids:
                f = (directory / hashlib.sha256(resource.encode()).hexdigest()).open('a+b')
                self.files.append(f)
                while True:
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            f.seek(0); f.write(b'0'); f.flush(); f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        await asyncio.sleep(.2)
                marker = directory / (hashlib.sha256(resource.encode()).hexdigest() + '.quarantine')
                self.markers.append(marker)
                if marker.exists() and not (self.recovery and self.owner and marker.read_text() == self.owner):
                    raise ValueError('GPU 有未确认释放的执行器；请在原 Worker 检查并恢复，不能切换执行器')
        except BaseException:
            self.release(); raise

    def mark_in_use(self):
        # Persist before invoking inference: process death must not clear the safety gate.
        for marker in self.markers: marker.write_text(self.owner or 'unknown')

    def confirm_released(self):
        for marker in self.markers: marker.unlink(missing_ok=True)

    def release(self):
        for f in self.files: f.close()
        self.files.clear()
        self.markers.clear()


class StateLock:
    """Prevent two services/CLI processes from managing the same state directory."""
    def __init__(self, root):
        self.path = Path(root) / 'service.lock'
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.release(); raise ValueError('此数据目录已有 Worker 运行，请通过管理接口操作')

    def release(self):
        if self.file: self.file.close(); self.file = None
