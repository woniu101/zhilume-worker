from fixture_identity import fixture_config
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
from zhilume_worker.video_workflows import profiles, build_graph, validate_input
from zhilume_worker.video import VideoExecutor

CONFIG = dict(url='http://comfy', exclusive=True, profiles=[dict(modelId='minimax-h3-ref2va', identity=dict(revision='fixture-v1',quantization='fp32',artifacts={'model':'revision:fixture-v1'}), models=dict(diffusion='h3.safetensors', clip='clip.safetensors', vae='video.safetensors', audioVae='audio.safetensors'))])
CONFIG=fixture_config(CONFIG)

def request(p):
    return dict(modelId=p['modelId'],profileId=p['profileId'],workflowRevision=p['workflowRevision'],mode='reference',prompt='Use <Video 1> motion',width=512,height=288,frames=124,steps=20,seed=0,includeAudio=True,references=[dict(role='video',assetId='source',start=0,frames=56)],referenceAssetIds=['source'])

class VideoTest(unittest.IsolatedAsyncioTestCase):
    def test_reference_graph_roles_and_frame_contract(self):
        p=profiles(CONFIG)[0]; r=request(p['public'])
        g=build_graph(p,'video.generate.v1',r,['clip.mp4'],'test')
        self.assertEqual(g['condition']['inputs']['ref_videos.ref_video_1'],['reference_0',0])
        self.assertEqual(g['video']['inputs']['audio'],['decode_audio',0])
        self.assertEqual(g['output']['inputs']['format.codec'],'h264')
        for bad in [dict(frames=120),dict(references=[None]),dict(referenceAssetIds=['other']),dict(references=[dict(role='video',assetId='source',start=0,frames=141)])]:
            with self.assertRaises(ValueError): validate_input(p['public'],{**r,**bad})
        self.assertNotIn('audio',build_graph(p,'video.generate.v1',{**r,'includeAudio':False},['clip.mp4'],'test')['video']['inputs'])
        fl=profiles({**CONFIG,'profiles':[{**CONFIG['profiles'][0],'modelId':'minimax-h3-fl2va'}]})[0]
        r.update({k:fl['public'][k] for k in ('modelId','profileId','workflowRevision')});r.update(mode='first-last',references=[dict(role='first',assetId='a'),dict(role='last',assetId='a')],referenceAssetIds=['a'])
        g=build_graph(fl,'video.generate.v1',r,['a.png','a.png'],'test')
        self.assertEqual(g['condition']['inputs']['last_frame'],['reference_1',0])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg integration requires binaries')
    async def test_real_reference_preparation_and_output_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source.mp4'
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=512x288:r=24','-f','lavfi','-i','sine=frequency=440:sample_rate=32000','-t',str(124/24),'-c:v','libx264','-pix_fmt','yuv420p','-ac','2','-c:a','aac',str(source)],check=True)
            def handle(req): return httpx.Response(200,content=source.read_bytes())
            e=VideoExecutor({**CONFIG,'ffmpeg':shutil.which('ffmpeg'),'ffprobe':shutil.which('ffprobe')},transport=httpx.MockTransport(handle))
            try:
                p=e.profiles[0];r=request(p['public'])
                r['references'].append(dict(role='audio',assetId='source',start=.5,frames=56))
                files=await e.prepare_inputs(p,'video.generate.v1',r,[source],root,AsyncMock())
                self.assertEqual([f.suffix for f in files],['.mp4','.wav'])
                history={'outputs':{'output':{'images':[dict(filename='out.mp4',type='output',subfolder='')]}}}
                result=await e.collect_output(history,r,root,AsyncMock());self.assertTrue(result[0].is_file())
                with self.assertRaisesRegex(ValueError,'音轨'):
                    await e.collect_output(history,{**r,'includeAudio':False},root,AsyncMock())
                r['references'][0]['start']=5
                with self.assertRaisesRegex(ValueError,'实际时长'):
                    await e.prepare_inputs(p,'video.generate.v1',r,[source],root,AsyncMock())
            finally: await e.http.aclose()
