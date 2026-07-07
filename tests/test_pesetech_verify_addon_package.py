import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT_DIR = (
    Path(__file__).resolve().parents[1]
    / "scripts"
)
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def load_script(name):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_addon = load_script("pesetech_make_addon")
verifier = load_script("pesetech_verify_addon_package")


def write_file(root, relative_path, content="# test fixture\n"):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_minimal_source(root):
    write_file(root, "README.md", "readme\n")
    for relative_path in verifier.REQUIRED_SOURCE_FILES:
        write_file(root, relative_path)
    write_file(
        root,
        "scripts/pesetech_addon_status.py",
        (
            "strict_proof\nimport_check\nStrict host proof\nHOME_ASSISTANT_TOKEN\nprove-ha-addon\n"
            "--readiness-only\n--candidate-timeout\nnext_operation\n"
            "configuration_snippet\nmoves_real_light\n"
        ),
    )
    write_file(root, "docker/config/config.yaml", "mqtt:\n  password: secret\n")
    write_file(root, "docker/config/store.yaml", "nodes: {}\n")
    write_file(root, ".git/config", "secret git data\n")


class PesetechVerifyAddonPackageTest(unittest.TestCase):
    def test_verify_generated_addon_folder_and_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            archive_path = temp_path / "pesetech-ha-addon.tar.gz"
            local_archive_path = temp_path / "pesetech-ha-local-addon.tar.gz"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            make_addon.make_local_archive(output, local_archive_path)
            with tarfile.open(archive_path, "w:gz") as archive:
                for child in output.rglob("*"):
                    archive.add(child, arcname=child.relative_to(output))

            self.assertEqual(verifier.verify_addon_package(output), [])
            self.assertEqual(verifier.verify_addon_package(archive_path), [])
            self.assertEqual(verifier.verify_addon_package(local_archive_path, local_app=True), [])
            local_app_errors = verifier.verify_addon_package(archive_path, local_app=True)
            self.assertTrue(any("file outside pesetech_ble_mesh/" in error and "repository.yaml" in error for error in local_app_errors))
            self.assertTrue(any("missing required file: repository.yaml" in error for error in verifier.verify_addon_package(local_archive_path)))

    def test_rejects_broken_run_script_syntax(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            run_script = output / "pesetech_ble_mesh" / "run.sh"
            run_script.write_text(
                run_script.read_text(encoding="utf-8").replace(
                    "cleanup_cloud_secret_files() {",
                    "cleanup_cloud_secret_files() {{",
                ),
                encoding="utf-8",
            )

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("run.sh failed bash syntax check" in error for error in errors))

    def test_rejects_extra_config_yaml_and_secret_state_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            write_file(output, "pesetech_ble_mesh/source/docker/config/config.yaml", "mqtt:\n  password: secret\n")
            write_file(output, "pesetech_ble_mesh/source/docker/config/store.yaml", "nodes: {}\n")
            write_file(output, "pesetech_ble_mesh/source/docker/config/pesetech-preflight.json", "{}\n")
            write_file(output, "pesetech_ble_mesh/source/docker/config/pesetech-import-check.json", "{}\n")
            write_file(output, "pesetech_ble_mesh/source/docker/config/pesetech-runtime-check.json", "{}\n")
            write_file(output, "pesetech_ble_mesh/source/pesetech_mesh.json", "{}\n")

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("unexpected config.yaml" in error for error in errors))
        self.assertTrue(any("forbidden packaged file" in error and "store.yaml" in error for error in errors))
        self.assertTrue(any("forbidden packaged file" in error and "pesetech-preflight.json" in error for error in errors))
        self.assertTrue(any("forbidden packaged file" in error and "pesetech-import-check.json" in error for error in errors))
        self.assertTrue(any("forbidden packaged file" in error and "pesetech-runtime-check.json" in error for error in errors))
        self.assertTrue(any("forbidden packaged file" in error and "pesetech_mesh.json" in error for error in errors))

    def test_rejects_missing_app_config_yaml_with_local_install_hint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            (output / "pesetech_ble_mesh" / "config.yaml").unlink()

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("missing Home Assistant app config.yaml at pesetech_ble_mesh/config.yaml" in error for error in errors))

    def test_rejects_home_assistant_dockerfile_without_app_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            write_file(output, "pesetech_ble_mesh/Dockerfile", "FROM ${BUILD_FROM}\nCMD [\"python3\"]\n")

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("io.hass.type=\"app\"" in error for error in errors))
        self.assertTrue(any("CMD [ \"/run.sh\" ]" in error for error in errors))
        self.assertTrue(any("explicit base image" in error for error in errors))

    def test_rejects_status_script_without_strict_proof_hint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            write_file(output, "pesetech_ble_mesh/source/scripts/pesetech_addon_status.py", "# old status script\n")

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("status guidance" in error and "configuration_snippet" in error for error in errors))

    def test_rejects_missing_cloud_report_summary_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "pesetech-home-assistant"
            output = temp_path / "ha-addon"
            write_minimal_source(root)

            make_addon.make_addon(root, output)
            (output / "pesetech_ble_mesh" / "source" / "scripts" / "pesetech_cloud_report_summary.py").unlink()

            errors = verifier.verify_addon_package(output)

        self.assertTrue(any("missing required file" in error and "pesetech_cloud_report_summary.py" in error for error in errors))

    def test_main_returns_nonzero_for_invalid_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            broken = Path(temp_dir) / "broken-addon"
            broken.mkdir()

            with redirect_stdout(io.StringIO()):
                exit_code = verifier.main([str(broken)])

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
