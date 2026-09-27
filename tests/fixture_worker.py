"""CPU transport fixture. Never installed as a production entry point."""
import os
from zhilume_worker import executors
from zhilume_worker.comfy import ComfyExecutor
from zhilume_worker.__main__ import main

async def fixture_hardware():
    return [dict(uuid='GPU-fixture-'+str(os.getpid()), name='CPU fixture', totalMiB='0', usedMiB='0')]

async def fixture_release(self):
    if self.poisoned: raise ValueError('fixture remains active')

executors.hardware = fixture_hardware
ComfyExecutor.release = fixture_release
if __name__ == '__main__': main()
