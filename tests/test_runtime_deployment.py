import asyncio
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from test_management import args
from zhilume_worker.gateway import create_app, identity
from zhilume_worker.management import admin_identity
from zhilume_worker.runtimes import RuntimeManager, validate_runtime
from zhilume_worker.releases import Releases, write_json
from zhilume_worker.resources import StateLock


def config(root, port=8188):
    return dict(type='comfyui', python=sys.executable, directory=str(root), modelPathsFile='', port=port, device='0')


class RuntimeAPITests(unittest.TestCase):
    def test_unauthorized_control_and_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with TestClient(create_app(args(root))) as client:
                admin = {'Authorization': 'Bearer ' + admin_identity(root)}
                schedule = {'Authorization': 'Bearer ' + identity(root)['credential']}
                self.assertEqual(client.put('/management/api/runtimes/comfy', headers=schedule, json=config(root)).status_code, 401)
                self.assertEqual(client.post('/management/api/runtimes/comfy/start', headers=schedule, json={}).status_code, 401)
                self.assertEqual(client.put('/management/api/runtimes/comfy', headers=admin, json={**config(root), 'port': '8188'}).status_code, 400)
                self.assertEqual(client.put('/management/api/runtimes/comfy', headers=admin, json=config(root)).status_code, 200)
                import time
                for _ in range(100):
                    if client.get('/management/api/operations', headers=admin).json()[0]['state'] != 'running': break
                    time.sleep(.01)
                self.assertEqual(client.get('/management/api/overview', headers=admin).json()['runtimes'][0]['state'], 'stopped')
            restored = RuntimeManager(root)
            self.assertEqual(restored.url('comfy'), 'http://127.0.0.1:8188')
            with self.assertRaises(ValueError): restored.configure('other', config(root))

    def test_broken_runtime_does_not_hide_healthy_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config').mkdir()
            (root/'config/runtimes.json').write_text(json.dumps({'broken': 'bad', 'good': config(root)}))
            runtimes=RuntimeManager(root)
            self.assertEqual({r['id']:r['state'] for r in runtimes.snapshot()}, {'broken':'error','good':'stopped'})


@unittest.skipUnless(os.name == 'posix', 'Linux/WSL2 process ownership and release activation')
class RuntimeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_service_start_and_shutdown_reaps_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
            (root/'main.py').write_text('''import http.server,sys,subprocess
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'])
Path('child.pid').write_text(str(child.pid))
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(b'{"devices":[]}')
 def log_message(self,*args): pass
http.server.HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
''')
            manager=RuntimeManager(root); manager.configure('comfy',config(root,port))
            await manager.start('comfy',timeout=5)
            self.assertEqual(manager.snapshot()[0]['state'],'running')
            process=manager.processes['comfy']
            with self.assertRaises(ValueError): manager.configure('comfy',config(root,port))
            await manager.close()
            self.assertIsNotNone(process.returncode)
            child=int((root/'child.pid').read_text())
            for _ in range(50):
                status=Path(f'/proc/{child}/stat')
                if not status.exists() or status.read_text().split()[2]=='Z': break
                await asyncio.sleep(.02)
            self.assertTrue(not status.exists() or status.read_text().split()[2]=='Z')

    async def test_existing_port_not_adopted_and_cancelled_start_is_reaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'main.py').write_text('import time;time.sleep(90)')
            manager=RuntimeManager(root)
            with socket.socket() as s:
                s.bind(('127.0.0.1',0));s.listen();port=s.getsockname()[1]
                manager.configure('comfy',config(root,port))
                with self.assertRaisesRegex(ValueError,'端口'): await manager.start('comfy')
                self.assertFalse(manager.processes)
            task=asyncio.create_task(manager.start('comfy'))
            for _ in range(100):
                if 'comfy' in manager.processes: break
                await asyncio.sleep(.01)
            process=manager.processes['comfy'];task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertIsNotNone(process.returncode)


class RuntimeSharingTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_same_quarantined_service_does_not_clear_gpu_gate(self):
        from zhilume_worker.worker import Worker
        from zhilume_worker.resources import ResourceLease
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'ZHILUME_RESOURCE_LOCK_DIR':tmp}):
            worker=Worker(args(tmp));worker.worker_id='original'
            worker.manager.entries={'image':{'enabled':False,'config':{'runtimeId':'shared'}}}
            lease=ResourceLease(['GPU-fixture'],'original:image')
            await lease.acquire();lease.mark_in_use();lease.release()
            with patch('zhilume_worker.executors.hardware',new_callable=AsyncMock,return_value=[{'uuid':'GPU-fixture'}]), patch.object(worker.manager.runtimes,'start',new_callable=AsyncMock) as start:
                await worker.manager.runtime_action('shared','start')
                start.assert_awaited_once()
                self.assertEqual(ResourceLease.quarantine_owners(['GPU-fixture']),{'original:image'})
                worker.worker_id='different'
                with self.assertRaisesRegex(ValueError,'其他未释放'):await worker.manager.runtime_action('shared','start')

    async def test_shared_stop_drains_all_consumers_and_preserves_failure(self):
        from zhilume_worker.worker import Worker
        with tempfile.TemporaryDirectory() as tmp:
            worker=Worker(args(tmp));worker.worker_id='test'
            manager=worker.manager
            manager.entries={k:{'enabled':True,'config':{'runtimeId':'shared'}} for k in ('image','video')}
            manager.runtimes.configure('shared',config(Path(tmp)))
            worker.active={'video-task':{'job':{'payload':{'operation':'video.generate'}}}}
            calls=[]
            async def disable(kind,policy):
                cancel.assert_called_once_with(worker.active['video-task'])
                self.assertEqual(manager.draining,{'image','video'})
                self.assertFalse(any(v['enabled'] for v in manager.entries.values()))
                calls.append((kind,policy))
                if kind=='image': raise ValueError('release failed')
            with patch.object(worker,'hello',new_callable=AsyncMock), patch.object(worker,'cancel') as cancel, patch.object(manager,'disable',side_effect=disable), patch.object(manager.runtimes,'stop',new_callable=AsyncMock) as stop:
                with self.assertRaisesRegex(ValueError,'释放仍未确认'): await manager.runtime_action('shared','stop','cancel')
                self.assertEqual(calls,[('image','cancel'),('video','cancel')]);stop.assert_awaited_once()


@unittest.skipUnless(os.name == 'posix', 'Linux release tool')
class ReleaseTests(unittest.TestCase):
    def test_switch_rollback_running_guard_and_identity_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); release=Releases(root/'program',root/'data');release.initialize()
            original=identity(release.state);token=admin_identity(release.state)
            for version in ('0.9.0','0.10.0'):
                target=release.versions/version;(target/'venv/bin').mkdir(parents=True)
                write_json(target/'release.json',{'version':version,'status':'ready'})
            release.activate('0.9.0')
            lock=StateLock(release.state);lock.acquire()
            try:
                with self.assertRaises(ValueError): release.activate('0.10.0')
            finally: lock.release()
            self.assertEqual(release.activate('0.10.0')['previous'],'0.9.0')
            self.assertEqual(release.rollback()['current'],'0.9.0')
            self.assertEqual(identity(release.state),original);self.assertEqual(admin_identity(release.state),token)
            with self.assertRaises(ValueError): release.activate('../escape')
            with self.assertRaises(ValueError): Releases(root/'data/program',root/'data')
