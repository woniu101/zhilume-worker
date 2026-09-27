import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zhilume_worker import installer


class InstallerTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(sys.platform=='linux','Linux owned process group acceptance')
    async def test_cancel_and_failed_leader_reap_real_child_processes(self):
        from zhilume_worker.processes import run_child
        for fail in (False,True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);pidfile=root/'child.pid'
                code='import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);Path(sys.argv[1]).write_text(str(p.pid));'+('sys.exit(2)' if fail else 'time.sleep(60)')
                task=asyncio.create_task(run_child([sys.executable,'-c',code,str(pidfile)],root,root/'log'))
                for _ in range(100):
                    if pidfile.exists():break
                    await asyncio.sleep(.02)
                self.assertTrue(pidfile.exists())
                if not fail:task.cancel()
                with self.assertRaises(ValueError if fail else asyncio.CancelledError):await task
                pid=int(pidfile.read_text())
                for _ in range(100):
                    stat=Path(f'/proc/{pid}/stat')
                    if not stat.exists() or stat.read_text().split()[2]=='Z':break
                    await asyncio.sleep(.02)
                else:
                    os.kill(pid,9);self.fail('Owned child survived failure/cancellation')

    def spec(self, root):
        return installer.plan('image', str(root/'new environment'), sys.executable)

    async def test_plan_is_read_only_and_rejects_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spec=self.spec(root)
            self.assertFalse(Path(spec['directory']).exists())
            self.assertEqual(spec['pythonVersion'],'3.12')
            self.assertEqual(installer.plan('speech',spec['directory'],sys.executable)['pythonVersion'],'3.11')
            self.assertFalse(spec['downloadsWeights']); self.assertFalse(spec['startsInference'])
            (root/'new environment').mkdir()
            with self.assertRaisesRegex(ValueError,'不覆盖'):self.spec(root)

    async def test_stale_plan_and_preflight_failure_do_not_create_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spec=self.spec(root)
            with self.assertRaisesRegex(ValueError,'计划已变化'):
                await installer.install('image',spec['directory'],sys.executable,root/'log',expected_plan='wrong')
            with patch.object(installer,'preflight',side_effect=ValueError('missing tool')):
                with self.assertRaisesRegex(ValueError,'missing tool'):
                    await installer.install('image',spec['directory'],sys.executable,root/'log')
            self.assertFalse(Path(spec['directory']).exists())

    async def test_low_disk_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=self.spec(Path(tmp));spec['pythonVersion']=f'{sys.version_info.major}.{sys.version_info.minor}'
            with patch.object(installer.platform,'system',return_value='Linux'), patch.object(installer.platform,'machine',return_value='x86_64'), patch.object(installer.platform,'libc_ver',return_value=('glibc','2.35')), patch.object(installer.shutil,'which',return_value='/tool'), patch.object(installer.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
                with self.assertRaisesRegex(ValueError,'24 GiB'):await installer.preflight(spec)
            self.assertFalse(Path(spec['directory']).exists())

    async def test_wrong_python_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec=self.spec(Path(tmp));spec['pythonVersion']='0.0'
            with patch.object(installer.platform,'system',return_value='Linux'), patch.object(installer.platform,'machine',return_value='x86_64'), patch.object(installer.platform,'libc_ver',return_value=('glibc','2.35')), patch.object(installer.shutil,'which',return_value='/tool'):
                with self.assertRaisesRegex(ValueError,'要求 Python'):await installer.preflight(spec)

    async def test_locked_commands_and_space_paths_are_argument_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spec=self.spec(root);calls=[];stages=[]
            async def child(command,cwd,log,**kwargs):
                calls.append((command,kwargs))
                Path(log).write_text('fixture\n',encoding='utf-8')
            with patch.object(installer,'preflight',new=AsyncMock(return_value={})), patch.object(installer,'run_child',side_effect=child):
                result=await installer.install('image',spec['directory'],sys.executable,root/'log',progress=stages.append)
            self.assertEqual(result['state'],'installed-unchecked'); self.assertFalse(result['inferenceVerified'])
            sync=next(c for c,_ in calls if c[:3]==['uv','pip','sync'])
            self.assertIn('--require-hashes',sync);self.assertIn('cu128',sync)
            self.assertTrue(any('new environment' in a for a in sync))
            self.assertTrue(all(k['env']['UV_PYTHON_DOWNLOADS']=='never' for _,k in calls))
            self.assertEqual(installer.digest(Path(spec['directory'])/'dependencies.lock'),spec['lockSha256'])
            self.assertGreater(len(stages),5)

    async def test_failure_and_cancel_preserve_existing_environment(self):
        for error in [ValueError('dependency failure'),asyncio.CancelledError()]:
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);old=root/'existing';old.mkdir();(old/'identity').write_text('keep')
                spec=self.spec(root)
                with patch.object(installer,'preflight',new=AsyncMock(return_value={})), patch.object(installer,'run_child',side_effect=error):
                    with self.assertRaises(type(error)):
                        await installer.install('image',spec['directory'],sys.executable,root/'log')
                record=json.loads((Path(spec['directory'])/'installation.json').read_text('utf-8'))
                self.assertEqual(record['state'],'cancelled' if isinstance(error,asyncio.CancelledError) else 'incomplete')
                self.assertEqual((old/'identity').read_text(),'keep')
                with self.assertRaises(ValueError):self.spec(root)


if __name__=='__main__':unittest.main()
