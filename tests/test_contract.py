import json
import hashlib
import unittest
from pathlib import Path
import jsonschema
from zhilume_worker.worker import VALIDATOR, Worker
from types import SimpleNamespace
from tempfile import TemporaryDirectory


class ContractTest(unittest.TestCase):
    def test_worker_does_not_advertise_basic_media(self):
        with TemporaryDirectory() as root:
            worker = Worker(SimpleNamespace(state=root, enable_image_execution=False))
            self.assertFalse(any(op.startswith("media.") for op in worker.capabilities))

    def test_shared_fixtures(self):
        folder = Path(__file__).parents[1] / "src/zhilume_worker/contracts"
        fixtures = json.loads((folder / "fixtures.json").read_text("utf-8"))
        for message in fixtures["valid"]:
            VALIDATOR.validate(message)
        for message in fixtures["invalid"]:
            with self.assertRaises(jsonschema.ValidationError):
                VALIDATOR.validate(message)


if __name__ == "__main__":
    unittest.main()
