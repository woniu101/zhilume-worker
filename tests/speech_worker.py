import fixture_worker
"""CPU integration fixture only: replace model preflight, never the production transport/runner."""
from zhilume_worker import executors
from zhilume_worker.environment import inspect_environment
from zhilume_worker.speech import SpeechExecutor
from zhilume_worker.__main__ import main

async def fixture_check(self):
    self.ready = True

async def fixture_environment(kind, config, rows):
    await inspect_environment(kind, config, rows)
    # Inference is replaced by the test repository; never install Torch for a CPU fixture.
    for row in rows:
        if kind == 'speech' and row['id'] == 'dependencies':
            row.update(state='passed', detail='CPU fixture inference modules', remedy='')

if __name__ == '__main__':
    executors.inspect_environment = fixture_environment
    SpeechExecutor.check = fixture_check
    main()
