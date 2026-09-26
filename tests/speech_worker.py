"""CPU integration fixture only: replace model preflight, never the production transport/runner."""
from zhilume_worker.speech import SpeechExecutor
from zhilume_worker.__main__ import main

async def fixture_check(self):
    self.ready = True

if __name__ == '__main__':
    SpeechExecutor.check = fixture_check
    main()
