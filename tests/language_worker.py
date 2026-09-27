"""CPU fixture: real Worker transport/leases, deterministic text adapter only."""
import asyncio
import os
from zhilume_worker.executors import ExecutorManager
from zhilume_worker.language import public_profile
from zhilume_worker.__main__ import main

class Fixture:
    def __init__(self):
        self.public_profiles = [public_profile(dict(modelId='fixture-language', identity=dict(revision='fixture-v1', quantization='Q4_K_M', artifacts={'model':'sha256:'+'a'*64,'binary':'sha256:'+'b'*64})))]
    async def release(self): pass
    async def process(self, operation, source, paths, directory, progress):
        assert source['profileId'] == self.public_profiles[0]['profileId']
        assert source['systemPrompt'] and not paths
        await progress('CPU fixture generating')
        await asyncio.sleep(10 if source['text'] == 'hold' else .4)
        if source['text'] == 'fail': raise ValueError('fixture failure')
        p = directory / 'language.txt'; p.write_text('result: '+source['text'], 'utf-8')
        return p, 'result.txt'

async def start(self):
    self.instances['language'] = Fixture()
    self.states['language']['state'] = 'ready'
    self.gpus = [dict(uuid='GPU-language-fixture-'+str(os.getpid()), name='CPU fixture', totalMiB='0', usedMiB='0')]
    self.entries['language'] = {'enabled':True, 'config':{}}

ExecutorManager.start = start
if __name__ == '__main__': main()
