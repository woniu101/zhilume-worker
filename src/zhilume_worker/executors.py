"""Internal executor lifecycle; imports and failures are isolated per adapter."""
import asyncio
import importlib
import json
from pathlib import Path
from typing import Protocol
from .resources import hardware, ResourceLease


class Executor(Protocol):
    @property
    def public_profiles(self) -> list: ...
    async def check(self): ...
    async def release(self): ...


ADAPTERS = {'image': ('comfy', 'ComfyExecutor'), 'speech': ('speech', 'SpeechExecutor'), 'video': ('video', 'VideoExecutor')}


class ExecutorManager:
    def __init__(self, worker):
        self.worker = worker
        self.root = worker.root
        self.config_path = self.root / 'config' / 'executors.json'
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_error = ''
        try:
            self.entries = json.loads(self.config_path.read_text('utf-8')) if self.config_path.exists() else {}
            if not isinstance(self.entries, dict): raise ValueError('配置须为对象')
        except (ValueError, OSError):
            self.entries = {}; self.config_error = '执行器配置文件无法读取，请重新保存配置'
        entry_errors = {}
        for kind, entry in list(self.entries.items()):
            if kind not in ADAPTERS:
                self.entries.pop(kind); continue
            if not isinstance(entry, dict) or not isinstance(entry.get('config', {}), dict) or type(entry.get('enabled', False)) is not bool:
                self.entries[kind] = {'enabled': False, 'config': {}}
                entry_errors[kind] = '执行器配置结构无效，请重新保存此执行器配置'
        for kind, flag, attr in [('image','enable_image_execution','comfy_config'),('speech','enable_speech_execution','speech_config'),('video','enable_video_execution','video_config')]:
            if getattr(worker.args, flag, False):
                try: config = json.loads(Path(getattr(worker.args, attr)).read_text('utf-8'))
                except Exception: config = {}
                self.entries[kind] = {'enabled': True, 'config': config}
        self.instances = {}
        self.states = {k: {'state': 'error' if self.config_error or k in entry_errors else 'disabled', 'reason': entry_errors.get(k, self.config_error), 'inferenceVerified': False} for k in ADAPTERS}
        self.gpus = []
        self.actions = asyncio.Lock()
        self.draining = set()

    def persist(self):
        tmp = self.config_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), 'utf-8'); tmp.chmod(0o600); tmp.replace(self.config_path)

    def snapshot(self):
        return [{'id': k, **self.states[k], 'enabled': self.entries.get(k, {}).get('enabled', False), 'config': self.entries.get(k, {}).get('config', {}),
                 'profiles': self.instances[k].public_profiles if k in self.instances and self.states[k]['state'] == 'ready' else []} for k in ADAPTERS]

    async def check(self, kind, enable=False):
        async with self.actions:
            if kind not in ADAPTERS: raise ValueError('未知执行器')
            if self.worker.active: raise ValueError('任务执行中，不能检查或切换环境')
            if kind in self.instances:
                previous = self.instances[kind]
                if self.worker.quarantined_lease and self.worker.quarantined_lease.owner == self.worker.worker_id + ':' + kind:
                    if hasattr(previous, 'http'): await previous.http.aclose()
                    self.instances.pop(kind)
                else: await self._stop(kind)
            self.states[kind] = {'state': 'checking', 'reason': '', 'inferenceVerified': False}
            instance = None
            resource = None
            try:
                module, cls = ADAPTERS[kind]
                factory = getattr(importlib.import_module('.' + module, __package__), cls)
                config = self.entries.get(kind, {}).get('config', {})
                instance = factory(config)
                self.gpus = await hardware()
                if not self.gpus: raise ValueError('未发现 NVIDIA GPU；可保存环境配置，推理尚未就绪')
                await instance.check()
                if enable:
                    resource = self.worker.quarantined_lease or ResourceLease([g['uuid'] for g in self.gpus], self.worker.worker_id + ':' + kind, recovery=True)
                    if not self.worker.quarantined_lease: await resource.acquire()
                    if resource.owner != self.worker.worker_id + ':' + kind: raise ValueError('请先恢复原执行器并确认资源释放')
                    resource.mark_in_use()
                    if hasattr(instance, 'recover'): await instance.recover(self.root)
                    # Empty queue alone is not enough: recover/unload before advertising a GPU.
                    await instance.release()
                    resource.confirm_released()
                    resource.release(); resource = None
                    self.worker.quarantined_lease = None
                    (self.root / 'resource-quarantine.json').unlink(missing_ok=True)
                self.instances[kind] = instance
                self.states[kind]['state'] = 'ready' if enable else 'checked'
                self.entries[kind]['enabled'] = enable
                self.persist()
            except Exception as error:
                if resource and resource.files and resource.owner == self.worker.worker_id + ':' + kind:
                    self.worker.quarantined_lease = resource
                    (self.root / 'resource-quarantine.json').write_text('{"releaseConfirmed":false}')
                if instance and hasattr(instance, 'http'): await instance.http.aclose()
                self.states[kind] = {'state': 'error', 'reason': str(error) if isinstance(error, (ValueError, FileNotFoundError, ModuleNotFoundError)) else type(error).__name__ + '：环境检查失败，请检查服务地址与依赖', 'inferenceVerified': False}
            await self.worker.hello()
            return self.states[kind]

    async def configure(self, kind, config):
        async with self.actions:
            if kind not in ADAPTERS or not isinstance(config, dict): raise ValueError('执行器配置无效')
            if self.worker.active: raise ValueError('任务执行中，不能修改环境')
            if kind in self.instances: await self._stop(kind)
            self.entries[kind] = {'enabled': False, 'config': config}; self.persist()
            self.states[kind] = {'state': 'disabled', 'reason': '配置已保存，请显式检查并启用', 'inferenceVerified': False}
            await self.worker.hello()

    async def disable(self, kind, policy):
        if kind not in ADAPTERS or policy not in ('wait', 'cancel'): raise ValueError('请选择等待任务结束或取消任务')
        self.entries.setdefault(kind, {'config': {}})['enabled'] = False; self.persist()
        self.draining.add(kind); self.states[kind]['state'] = 'draining'
        await self.worker.hello()
        prefix = {'image': 'image.', 'speech': 'audio.', 'video': 'video.'}[kind]
        active = [s for s in self.worker.active.values() if s['job']['payload']['operation'].startswith(prefix)]
        tasks = [s['task'] for s in active]
        if policy == 'cancel':
            for state in active: self.worker.cancel(state)
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        async with self.actions: await self._stop(kind)

    async def _stop(self, kind):
        instance = self.instances.get(kind)
        if instance:
            resource = self.worker.quarantined_lease or ResourceLease([g['uuid'] for g in self.gpus], self.worker.worker_id + ':' + kind, recovery=True)
            if not self.worker.quarantined_lease: await resource.acquire()
            if resource.owner != self.worker.worker_id + ':' + kind: raise ValueError('请先恢复原执行器并确认资源释放')
            resource.mark_in_use()
            try: await instance.release()
            except Exception:
                self.worker.quarantined_lease = resource
                (self.root / 'resource-quarantine.json').write_text('{"releaseConfirmed":false}')
                self.states[kind]['state'] = 'error'; self.states[kind]['reason'] = '资源释放未确认，禁止切换执行器'; raise ValueError(self.states[kind]['reason'])
            resource.confirm_released(); resource.release(); self.worker.quarantined_lease = None
            (self.root / 'resource-quarantine.json').unlink(missing_ok=True)
            if hasattr(instance, 'http'): await instance.http.aclose()
            self.instances.pop(kind, None)
        self.draining.discard(kind)
        self.states[kind]['state'] = 'disabled'

    def ready(self, kind):
        return self.instances.get(kind) if self.states[kind]['state'] == 'ready' and kind not in self.draining else None

    async def start(self):
        for kind, entry in list(self.entries.items()):
            if kind in ADAPTERS and entry.get('enabled'): await self.check(kind, True)

    async def close(self):
        for kind in list(self.instances):
            try: await self._stop(kind)
            except ValueError: pass
