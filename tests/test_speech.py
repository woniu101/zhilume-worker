import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zhilume_worker.speech import SpeechExecutor, public_profile, validate_input
from zhilume_worker.speech_runner import infer_kwargs


def request(profile):
    return dict(modelId=profile['modelId'], profileId=profile['profileId'], workflowRevision=profile['workflowRevision'],
                text='测试合成', language='ZH', speed=2, emotionMode='follow', emotionAlpha=.6, emotionVector=[0]*8,
                speaker=dict(assetId='voice', start=0, end=5), referenceAssetIds=['voice'], outputFormat='wav')


class SpeechTest(unittest.IsolatedAsyncioTestCase):
    def test_roles_modes_speed_and_fingerprint(self):
        profile = public_profile({})
        self.assertNotIn('text', profile['emotionModes'])
        self.assertNotEqual(profile['profileId'], public_profile({'enableEmotionText': True})['profileId'])
        value = request(profile)
        validate_input(value, profile)
        args = infer_kwargs(value, {'speaker': 'voice.wav'})
        self.assertEqual(args['duration_factor'], .5)
        self.assertNotIn('emo_text', args)
        self.assertNotIn('emo_alpha', args)
        with self.assertRaises(ValueError):
            validate_input({**value, 'speed': 0}, profile)
        with self.assertRaises(ValueError):
            validate_input({**value, 'emotionMode': 'text', 'emotionText': '开心'}, profile)
        value.update(emotionMode='reference', emotionReference=dict(assetId='emotion', start=1, end=4), referenceAssetIds=['voice', 'emotion'])
        validate_input(value, profile)
        self.assertEqual(infer_kwargs(value, {'speaker': 'v', 'emotionReference': 'e'})['emo_audio_prompt'], 'e')
        with self.assertRaises(ValueError):
            validate_input({**value, 'referenceAssetIds': ['emotion', 'voice']}, profile)

    async def test_preflight_never_advertises_missing_models(self):
        executor = SpeechExecutor({})
        self.assertEqual(executor.public_profiles, [])
        with self.assertRaises(ValueError):
            await executor.check()
        self.assertEqual(executor.public_profiles, [])

    async def test_cancel_reaps_owned_child_before_returning(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            executor = SpeechExecutor({})
            spawned = []
            create = asyncio.create_subprocess_exec
            async def capture(*args, **kwargs):
                p = await create(*args, **kwargs)
                spawned.append(p)
                return p
            with patch('asyncio.create_subprocess_exec', capture):
                task = asyncio.create_task(executor.run_child([sys.executable, '-c', 'import time; print("ready",flush=True); time.sleep(60)'], folder, folder/'speech.log'))
                for _ in range(100):
                    if (folder/'speech.log').exists() and 'ready' in (folder/'speech.log').read_text():
                        break
                    await asyncio.sleep(.05)
                self.assertTrue(spawned)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIsNotNone(spawned[0].returncode)


if __name__ == '__main__':
    unittest.main()
