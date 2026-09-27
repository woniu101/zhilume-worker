import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from test_management import args
from zhilume_worker.gateway import create_app, identity
from zhilume_worker.management import admin_identity
from zhilume_worker.environment import inspect_environment, validate_structure


class EnvironmentTest(unittest.TestCase):
    def test_templates_validation_logs_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with TestClient(create_app(args(root))) as client:
                admin = {'Authorization': 'Bearer ' + admin_identity(root)}
                task = {'Authorization': 'Bearer ' + identity(root)['credential']}
                self.assertEqual(client.get('/management/api/environment-templates', headers=task).status_code, 401)
                templates = client.get('/management/api/environment-templates', headers=admin).json()
                self.assertEqual(templates['image']['profiles'][0]['identity']['quantization'], '')
                self.assertEqual(client.put('/management/api/executors/video/config', headers=admin, json={'profiles': 'bad'}).status_code, 400)
                body = {'python': sys.executable, 'repository': tmp, 'modelDirectory': tmp, 'workingDirectory': ''}
                result = client.put('/management/api/executors/speech/config', headers=admin, json=body)
                self.assertEqual(result.status_code, 200)
                for _ in range(100):
                    operations = client.get('/management/api/operations', headers=admin).json()
                    if operations[0]['state'] != 'running': break
                    time.sleep(.01)
                self.assertEqual(operations[0]['state'], 'succeeded')
                self.assertIn('finishedAt', operations[0])
                self.assertNotIn('task', operations[0])
                self.assertEqual(client.get('/management/api/executors/speech/logs', headers=task).status_code, 401)
                logs = client.get('/management/api/executors/speech/logs', headers=admin).json()
                self.assertEqual([log['state'] for log in logs], ['running', 'succeeded'])
                saved = json.loads((root/'config/executors.json').read_text())['speech']['config']
                self.assertNotIn('workingDirectory', saved)
            with TestClient(create_app(args(root))) as client:
                self.assertEqual(len(client.get('/management/api/executors/speech/logs', headers=admin).json()), 2)

    def test_invalid_shapes_are_rejected_before_persistence(self):
        for config in [{'profiles': [None]}, {'profiles': [{'identity': 'bad'}]}, {'profiles': [{'sizes': [512]}]}, {'profiles': [{'frames': ['124']}]}, {'profiles': [{'referenceLimits': []}]}]:
            with self.assertRaises(ValueError): validate_structure('video', config)


class ReadOnlyChecksTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_paths_report_independently_without_gpu(self):
        from zhilume_worker.worker import Worker
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(args(tmp)); worker.worker_id = 'test'
            worker.manager.entries['speech'] = {'enabled': False, 'config': {'python': '/missing/python'}}
            with patch('zhilume_worker.executors.hardware', new=AsyncMock(return_value=[])):
                result = await worker.manager.check('speech')
            checks = {c['id']: c for c in result['checks']}
            self.assertEqual(result['state'], 'error')
            for key in ('python', 'repository', 'modelDirectory', 'ffmpeg', 'gpu', 'specification'):
                self.assertEqual(checks[key]['state'], 'failed')
                self.assertTrue(checks[key]['remedy'])
            self.assertEqual(worker.manager.states['image']['state'], 'disabled')
            self.assertFalse(result['inferenceVerified'])

    async def test_existing_paths_checked_without_loading_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = []
            await inspect_environment('speech', {'python': sys.executable, 'repository': tmp, 'modelDirectory': tmp, 'ffmpeg': sys.executable}, checks)
            by_id = {c['id']: c for c in checks}
            for key in ('python', 'repository', 'modelDirectory', 'ffmpeg'):
                self.assertEqual(by_id[key]['state'], 'passed')
            self.assertIn('dependencies', by_id)
            self.assertNotIn('torch', sys.modules)
