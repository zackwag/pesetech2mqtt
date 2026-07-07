import importlib.util
import tempfile
import unittest

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_rotation_module():
    spec = importlib.util.spec_from_file_location(
        "diagnostic_rotation_under_test",
        REPO_ROOT / "gateway/diagnostics/rotation.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROTATION = load_rotation_module()


class DiagnosticRotationTest(unittest.TestCase):
    def test_rotate_if_needed_keeps_bounded_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.log"
            path.write_text("a" * 10, encoding="utf-8")
            (Path(directory) / "monitor.log.1").write_text("b", encoding="utf-8")
            (Path(directory) / "monitor.log.2").write_text("c", encoding="utf-8")

            ROTATION.rotate_if_needed(str(path), next_bytes=1, max_bytes=10, max_files=2)

            self.assertEqual((Path(directory) / "monitor.log.1").read_text(encoding="utf-8"), "a" * 10)
            self.assertEqual((Path(directory) / "monitor.log.2").read_text(encoding="utf-8"), "b")
            self.assertFalse((Path(directory) / "monitor.log.3").exists())


if __name__ == "__main__":
    unittest.main()
