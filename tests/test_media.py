import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from zhilume_worker import media
from zhilume_worker.preflight import CATALOG, inspect_models


class MediaTest(unittest.IsolatedAsyncioTestCase):
    def test_invalid_parameters(self):
        for start, end in [(0, 0), (-1, 1), (True, 1), (0, float('nan')), (0, 86401)]:
            with self.assertRaises(ValueError):
                media.arguments(media.OPERATIONS[0], "input", "output", start, end)

    async def test_actual_cpu_processing(self):
        if not media.binary():
            self.skipTest("FFmpeg is required for CPU acceptance")
        with tempfile.TemporaryDirectory(prefix="zhilume-cpu-test-") as root:
            directory = Path(root)
            source = directory / "source.mp4"
            subprocess.run([media.binary(), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=128x72:r=10:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            values = []
            async def progress(value):
                values.append(value)
            for operation in media.OPERATIONS:
                output, name = await media.process(operation, source, directory, {"start": .5, "end": 1.5}, progress)
                self.assertGreater(output.stat().st_size, 64)
                if operation == media.OPERATIONS[1]:
                    import wave
                    with wave.open(str(output)) as wav:
                        self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 1, places=2)
            self.assertTrue(values)

    def test_model_profiles_are_not_executable_and_2512_has_no_edit(self):
        profiles = CATALOG['imageModels']
        self.assertEqual(profiles[0]['operations'], ['image.generate'])
        self.assertIn('image.reference', profiles[1]['operations'])
        with tempfile.TemporaryDirectory() as root:
            result = inspect_models([Path(root)], profiles[1])
            self.assertFalse(any(result.values()))
