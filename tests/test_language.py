import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import httpx
import sys
from zhilume_worker.language import LanguageExecutor, public_profile

def config():
    return dict(modelId='test-model', identity=dict(revision='test-v1', quantization='Q4_K_M', artifacts={'model':'sha256:'+'a'*64,'binary':'sha256:'+'b'*64}))

class LanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_identity_is_path_independent_but_limits_and_weights_are_not(self):
        c = config(); p = public_profile(c)
        self.assertEqual(p, public_profile({**c,'binary':'/elsewhere/bin','modelFile':'/elsewhere/model','device':'1'}))
        self.assertNotEqual(p['profileId'], public_profile({**c,'contextSize':4096})['profileId'])
        with self.assertRaises(ValueError): public_profile({**c,'maxOutputTokens':9000})
        self.assertEqual(p['capabilities'], ['text'])

    async def test_check_missing_or_wrong_model_and_digest(self):
        c = config()
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root)/'server.exe'; binary.write_bytes(b'fixture');binary.chmod(0o700)
            model = Path(root)/'m.gguf'; model.write_bytes(b'GGUFfixture')
            c.update(binary=str(binary),modelFile=str(model))
            e = LanguageExecutor(c)
            with self.assertRaisesRegex(ValueError,'摘要'): await e.check()
            c['identity']['artifacts']={k:'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest() for k,p in [('binary',binary),('model',model)]}
            e = LanguageExecutor(c)
            await e.check(); self.assertEqual(len(e.public_profiles),1)
            model.unlink()
            with self.assertRaisesRegex(ValueError,'不存在'): await e.check()

    async def test_release_fails_closed_if_vram_unknown_or_still_used(self):
        e = LanguageExecutor(config())
        with patch('zhilume_worker.language.hardware', AsyncMock(return_value=[])), patch('zhilume_worker.language.asyncio.sleep', AsyncMock()):
            with self.assertRaisesRegex(ValueError,'释放未确认'): await e.release()
        with patch('zhilume_worker.language.hardware', AsyncMock(return_value=[{'usedMiB':'2'}])): await e.release()

    async def test_rejects_vision_schema_or_other_spec_before_starting_process(self):
        e=LanguageExecutor(config()); e.ready=True
        source=dict(profileId=e.profile['profileId'],modelId=e.profile['modelId'],workflowRevision=e.profile['workflowRevision'],text='hello',systemPrompt='test',referenceAssetIds=['image'])
        with self.assertRaisesRegex(ValueError,'纯文本'): await e.process('text.generate.v1',source,[],Path('.'),AsyncMock())

    async def test_owned_process_cancel_and_normal_output(self):
        # Real async HTTP client with in-memory transport; no binary or model is executed.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); binary=root/'llama-server'; model=root/'m.gguf'
            binary.write_bytes(b'fixture');binary.chmod(0o700);model.write_bytes(b'GGUFfixture')
            c=config();c.update(binary=str(binary),modelFile=str(model))
            c['identity']['artifacts']={k:'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest() for k,p in [('binary',binary),('model',model)]}
            e=LanguageExecutor(c);await e.check()
            started=asyncio.Event();hold=False
            async def handler(request):
                if request.url.path == '/health': return httpx.Response(200,json={'status':'ok'})
                started.set()
                if hold: await asyncio.sleep(60)
                return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'中文结果'}}]})
            client_type=httpx.AsyncClient
            def client(**kwargs): return client_type(**kwargs,transport=httpx.MockTransport(handler))
            process=SimpleNamespace(pid=12345,returncode=None,wait=AsyncMock(return_value=0))
            source=dict(profileId=e.profile['profileId'],modelId=e.profile['modelId'],workflowRevision=e.profile['workflowRevision'],text='hello',systemPrompt='test',referenceAssetIds=[])
            with patch('zhilume_worker.language.signal',SimpleNamespace(SIGTERM=15,SIGKILL=9)), patch('zhilume_worker.language.sys',SimpleNamespace(platform='linux',executable=sys.executable)), patch('zhilume_worker.language.asyncio.create_subprocess_exec',AsyncMock(return_value=process)) as spawn, patch('zhilume_worker.language.os.killpg',create=True) as kill, patch('zhilume_worker.language.hardware',AsyncMock(return_value=[{'usedMiB':'2'}])), patch('zhilume_worker.language.httpx.AsyncClient',side_effect=client):
                output,_=await e.process('text.generate.v1',source,[],root,AsyncMock())
                self.assertEqual(output.read_text('utf-8'),'中文结果');self.assertIsNone(e.process_handle)
                self.assertTrue(spawn.call_args.kwargs['start_new_session']);self.assertTrue(kill.called)
                hold=True;started.clear()
                task=asyncio.create_task(e.process('text.generate.v1',source,[],root,AsyncMock()))
                await started.wait();task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertIsNone(e.process_handle);self.assertGreaterEqual(process.wait.await_count,4)
