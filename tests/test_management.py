from fixture_identity import fixture_config
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from zhilume_worker.gateway import create_app, identity
from zhilume_worker.management import admin_identity
from zhilume_worker.image_workflows import profiles
from zhilume_worker.resources import ResourceLease


def args(root):
    return SimpleNamespace(state=str(root), name='CPU test', host='127.0.0.1', port=4320, delay=.1, upload_limit=100000)


class ManagementTest(unittest.TestCase):
    def test_malformed_single_entry_does_not_break_overview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();(root/'config/executors.json').write_text('{"image":"invalid","speech":{"enabled":false,"config":{}}}')
            with TestClient(create_app(args(root))) as client:
                info=client.get('/management/api/overview',headers={'Authorization':'Bearer '+admin_identity(root)}).json()
                states={e['id']:e['state'] for e in info['executors']}
                self.assertEqual(states['image'],'error');self.assertEqual(states['speech'],'disabled')

    def test_packaged_page_and_management_remain_responsive_during_executor_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(args(tmp))
            async def slow_check(kind, enable=False):
                await asyncio.sleep(.5)
                return {'state':'error','reason':'fixture missing model'}
            with patch.object(app.state.worker.manager, 'check', side_effect=slow_check):
                with TestClient(app) as client:
                    admin={'Authorization':'Bearer '+admin_identity(Path(tmp))}
                    self.assertEqual(client.get('/management').status_code,200)
                    response=client.post('/management/api/executors/image/check',headers=admin,json={})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(client.get('/management/api/operations',headers=admin).json()[0]['state'],'running')
                    self.assertTrue(client.get('/management/api/overview',headers=admin).json()['serviceOnline'])
                    self.assertEqual(client.get('/management/api/operations',headers=admin).json()[0]['state'],'running')

    def test_management_authority_is_separate_and_bad_config_does_not_break_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config').mkdir();(root/'config/executors.json').write_text('{bad json')
            original=identity(root)
            with TestClient(create_app(args(root))) as client:
                schedule={'Authorization':'Bearer '+original['credential']}
                admin={'Authorization':'Bearer '+admin_identity(root)}
                self.assertEqual(client.get('/api/v1/system',headers=schedule).status_code,200)
                self.assertEqual(client.get('/management/api/overview',headers=schedule).status_code,401)
                self.assertEqual(client.get('/management/api/overview').status_code,401)
                self.assertEqual(client.get('/api/v1/system',headers=admin).status_code,401)
                info=client.get('/management/api/overview',headers=admin).json()
                self.assertTrue(info['serviceOnline']);self.assertFalse(info['bound'])
                self.assertEqual(info['executors'][0]['state'],'error')
                response=client.put('/management/api/executors/speech/config',headers=admin,json={'python':'/not-found','modelDirectory':'/not-found'})
                self.assertEqual(response.status_code,200)
                for _ in range(100):
                    import time
                    if client.get('/management/api/operations',headers=admin).json()[0]['state']!='running': break
                    time.sleep(.01)
                self.assertEqual(client.post('/management/api/executors/speech/check',headers=admin,json={}).status_code,200)
                report=client.get('/management/api/diagnostics',headers=admin).text
                self.assertNotIn(original['credential'],report);self.assertNotIn(admin_identity(root),report)
                self.assertEqual(client.get('/api/v1/system',headers=schedule).status_code,200)
            self.assertEqual(identity(root),original)
            with TestClient(create_app(args(root))) as client:
                self.assertEqual(client.get('/management/api/overview',headers=admin).status_code,200)

    def test_portable_identity_changes_only_for_execution_semantics(self):
        config=fixture_config(json.loads((Path(__file__).parent.parent/'config/comfy.example.json').read_text('utf-8')))
        one=profiles(config)[0]['public']
        config['url']='http://different-host:8188'
        config['profiles'][0]['models']={k:'other-folder/'+v for k,v in config['profiles'][0]['models'].items()}
        two=profiles(config)[0]['public'];self.assertEqual(one['profileId'],two['profileId'])
        config['profiles'][0]['identity']['quantization']='different-format'
        self.assertNotEqual(one['profileId'],profiles(config)[0]['public']['profileId'])
        config['profiles'][0]['maxSize']=1024
        self.assertNotEqual(two['profileId'],profiles(config)[0]['public']['profileId'])


class ResourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_crash_gate_survives_os_lock_release_and_only_owner_can_recover(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'ZHILUME_RESOURCE_LOCK_DIR':tmp}):
            first=ResourceLease(['GPU-test'], 'worker-a:image');await first.acquire();first.mark_in_use();first.release()
            with self.assertRaises(ValueError): await ResourceLease(['GPU-test'], 'worker-b:speech').acquire()
            with self.assertRaises(ValueError): await ResourceLease(['GPU-test'], 'worker-a:speech', recovery=True).acquire()
            recovery=ResourceLease(['GPU-test'], 'worker-a:image', recovery=True)
            await recovery.acquire();recovery.confirm_released();recovery.release()
            next_worker=ResourceLease(['GPU-test']);await next_worker.acquire();next_worker.release()

    async def test_state_directory_cannot_have_two_managers(self):
        from zhilume_worker.resources import StateLock
        with tempfile.TemporaryDirectory() as tmp:
            first,second=StateLock(tmp),StateLock(tmp);first.acquire()
            with self.assertRaises(ValueError):second.acquire()
            first.release();second.acquire();second.release()

    async def test_two_worker_process_leases_share_one_physical_resource(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ',{'ZHILUME_RESOURCE_LOCK_DIR':tmp}):
            first,second=ResourceLease(['GPU-test']),ResourceLease(['GPU-test'])
            await first.acquire();pending=asyncio.create_task(second.acquire());await asyncio.sleep(.05)
            self.assertFalse(pending.done());first.release();await asyncio.wait_for(pending,2);second.release()

    async def test_executor_check_does_not_block_control_loop(self):
        from zhilume_worker.worker import Worker
        with tempfile.TemporaryDirectory() as tmp:
            worker=Worker(args(tmp));worker.worker_id='fixture'
            worker.manager.entries['speech']={'enabled':False,'config':{}}
            with patch('zhilume_worker.executors.importlib.import_module',side_effect=ModuleNotFoundError('optional inference package missing')):
                result=await worker.manager.check('speech',True)
            self.assertEqual(result['state'],'error');self.assertEqual(worker.manager.states['image']['state'],'disabled')
