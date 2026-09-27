import tempfile
import unittest
from pathlib import Path
from zhilume_worker.model_links import plan_links, apply_links


class ModelLinksTests(unittest.TestCase):
    def test_plan_missing_conflict_and_apply_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = root / "mount"; model.mkdir()
            comfy = root / "comfy"
            manifest = {"links": [{"target": "diffusion_models/model.safetensors", "sources": ["repo/model.safetensors"]}]}
            missing = plan_links(manifest, [model], comfy)
            self.assertEqual(missing[0]["status"], "missing")
            with self.assertRaises(ValueError):
                apply_links(missing)
            self.assertFalse(comfy.exists())
            (model / "repo").mkdir()
            source = model / "repo/model.safetensors"; source.write_bytes(b"test-weight-fixture")
            links = plan_links(manifest, [model], comfy)
            self.assertEqual(links[0]["status"], "ready")
            self.assertEqual(Path(links[0]["target"]), comfy / 'diffusion_models/model.safetensors')
            try:
                apply_links(links)
            except OSError as error:
                if getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows 当前没有创建 symlink 权限")
                raise
            self.assertEqual(plan_links(manifest, [model], comfy)[0]["status"], "linked")
            apply_links(plan_links(manifest, [model], comfy))
            target = Path(links[0]["target"])
            target.unlink(); target.write_bytes(b"user-file")
            with self.assertRaises(ValueError):
                apply_links(plan_links(manifest, [model], comfy))
            self.assertEqual(target.read_bytes(), b"user-file")
            with self.assertRaises(ValueError):
                plan_links({"links": [{"target": "../escape", "sources": ["repo/model.safetensors"]}]}, [model], comfy)
