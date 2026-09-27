import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zhilume_worker.speech import SpeechExecutor, public_profile, validate_input, MODEL
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

    async def test_text_emotion_requires_nonempty_chat_template_before_advertising(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = ['indextts/infer_v2_5.py', 'config.yaml', 'gpt.pth', 's2mel.pth', 'codec.pth',
                     'wav2vec2bert_stats.pt', 'feat1.pt', 'feat2.pt', 'multilingual_zh_ja_yue_char_del.tiktoken',
                     'hf_cache/w2v-bert-2.0/config.json', 'hf_cache/w2v-bert-2.0/model.safetensors',
                     'hf_cache/bigvgan/config.json', 'hf_cache/bigvgan/bigvgan_generator.pt', 'hf_cache/campplus_cn_common.bin',
                     'qwen0.6bemo4-merge/config.json', 'qwen0.6bemo4-merge/model.safetensors',
                     'qwen0.6bemo4-merge/tokenizer.json', 'qwen0.6bemo4-merge/tokenizer_config.json']
            for name in files:
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('fixture')
            config = dict(python=sys.executable, ffmpeg=sys.executable, repository=str(root), modelDirectory=str(root), enableEmotionText=True)
            executor = SpeechExecutor(config)
            with patch('subprocess.check_output', return_value=MODEL['upstreamRevision'].encode()):
                with self.assertRaisesRegex(ValueError, 'chat_template.jinja'):
                    await executor.check()
                self.assertEqual(executor.public_profiles, [])
                template = root / 'qwen0.6bemo4-merge/chat_template.jinja'
                template.write_text('')
                with self.assertRaisesRegex(ValueError, 'chat_template.jinja'):
                    await executor.check()
                template.write_text('{{ messages }}')
                await executor.check()
                self.assertIn('text', executor.public_profiles[0]['emotionModes'])

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
