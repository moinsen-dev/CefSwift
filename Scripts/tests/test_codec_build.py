"""Offline contract tests; never starts a Chromium download or compilation."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "cef-codec-build.py"
spec = importlib.util.spec_from_file_location("codec_build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class CodecBuildTests(unittest.TestCase):
    def test_compiled_validation_accepts_no_history_checkout_without_tag_refs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lock = build.load_lock()
            output_dir = root / "chromium/src/out/Release_GN_arm64"
            output_dir.mkdir(parents=True)
            args = dict(lock["gn_args"], is_debug=False, target_cpu="arm64")
            (output_dir / "args.gn").write_text("\n".join(f"{key}={json.dumps(value)}" for key, value in args.items()))
            binary = output_dir / "cefclient.app/Contents/Frameworks/Chromium Embedded Framework.framework/Versions/A/Chromium Embedded Framework"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture")
            responses = [lock["cef_commit"], "MAJOR=151\nMINOR=0\nBUILD=7922\nPATCH=174", lock["depot_tools_commit"], "arm64"]
            with patch.object(build, "output", side_effect=responses) as commands:
                _, actual = build.validate_compiled(root, lock)
                self.assertEqual(actual, binary)
                self.assertIn(unittest.mock.call("git", "show", "HEAD:chrome/VERSION", cwd=root / "chromium/src"), commands.call_args_list)

    def test_build_defers_packaging_to_explicit_symbol_free_command(self):
        root = Path("/Volumes/BuildSSD/cef")
        self.assertIn("--no-distrib", build.build_command(root, build.load_lock()))
        command = build.package_command(root, root / "packages/new")
        self.assertIn("--no-symbols", command)
        self.assertIn("--minimal", command)
        self.assertIn("--arm64-build", command)
        self.assertIn("--no-archive", command)
        self.assertEqual(command[1], str(root / "chromium/src/cef/tools/make_distrib.py"))

    def test_package_only_preserves_existing_distribution_and_never_compiles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "chromium/src/cef/binary_distrib/partial.txt"
            old.parent.mkdir(parents=True)
            old.write_text("preserve me")
            lock = build.load_lock()
            with patch.object(build, "validate_compiled", return_value=("args", root / "binary")), \
                 patch.object(build, "run_build") as commands, \
                 patch.object(build, "finalize") as finalize, \
                 patch.object(build, "prepare") as prepare:
                build.package_existing(root, lock, {}, {})
                prepare.assert_not_called()
                self.assertEqual(commands.call_count, 1)
                command = commands.call_args.args[0]
                self.assertIn("make_distrib.py", command[1])
                self.assertIn("--no-symbols", command)
                destination = finalize.call_args.args[3]
                self.assertTrue(destination.is_relative_to(root / "packages"))
                self.assertEqual(old.read_text(), "preserve me")

    def test_prepare_bootstraps_pinned_depot_tools_without_enabling_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            depot = root / "depot_tools"
            depot.mkdir()
            lock = build.load_lock()
            compatibility = {"chromium_checkout": "refs/tags/" + lock["chromium"],
                             "depot_tools_checkout": lock["depot_tools_commit"]}
            env = build.build_environment(root, lock)
            with patch.object(build, "download_verified"), \
                 patch.object(build.urllib.request, "urlopen") as network, \
                 patch.object(build, "output", return_value=lock["depot_tools_commit"]), \
                 patch.object(build, "run_build") as monitored:
                network.return_value.__enter__.return_value.read.return_value = repr(compatibility).encode()
                build.prepare(root, lock, env)
                monitored.assert_called_once_with([depot / "ensure_bootstrap"], cwd=depot, env=env)
                self.assertEqual(env["DEPOT_TOOLS_UPDATE"], "0")

    def test_external_space_does_not_hide_full_internal_disk(self):
        with tempfile.TemporaryDirectory() as folder:
            usage = lambda free: type("Usage", (), {"free": free * 2**30})
            with patch.object(build.platform, "system", return_value="Darwin"), \
                 patch.object(build.platform, "machine", return_value="arm64"), \
                 patch.object(build.shutil, "disk_usage", side_effect=[usage(3700), usage(9)]), \
                 patch.object(build.subprocess, "check_output") as commands:
                with self.assertRaisesRegex(ValueError, "internal disk reserve"):
                    build.preflight(Path(folder) / "build", 200)
                commands.assert_not_called()
                self.assertFalse((Path(folder) / "build").exists())

    def test_local_job_limit_and_temporary_files_use_requested_build_root(self):
        with patch.object(build.os, "cpu_count", return_value=14):
            env = build.build_environment(Path("/Volumes/BuildSSD/cef"), build.load_lock(), jobs=4)
            self.assertEqual(env["NINJA_CORE_ADDITION"], "-10")
            self.assertEqual(env["TMPDIR"], "/Volumes/BuildSSD/cef/tmp")
            self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_resource_stop_preserves_checkout_and_terminates_owned_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.txt"
            source.write_text("preserve me")
            owned = []
            real_popen = subprocess.Popen

            def start(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                owned.append(process)
                return process

            with patch.object(build.subprocess, "Popen", side_effect=start), \
                 patch.object(build.shutil, "disk_usage", return_value=type("Usage", (), {"free": 9 * 2**30})):
                with self.assertRaisesRegex(ValueError, "Sources preserved"):
                    build.run_build(["/bin/sleep", "30"], cwd=root, env=build.os.environ.copy(), poll_seconds=0.01)
            self.assertEqual(len(owned), 1)
            self.assertIsNotNone(owned[0].poll())
            self.assertEqual(source.read_text(), "preserve me")

    def test_plan_pins_upstream_and_enables_codecs_without_creating_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "build"
            result = subprocess.run([sys.executable, str(SCRIPT), "plan", "--build-root", str(root)],
                                    capture_output=True, text=True, check=True)
            plan = json.loads(result.stdout)
            self.assertFalse(root.exists())
            self.assertFalse(plan["downloads_started"])
            self.assertIn('--checkout=2384915b7b1f0fe5ad1107e48d80c34e86b698d7', plan["command"])
            self.assertIn('--chromium-checkout=refs/tags/151.0.7922.174', plan["command"])
            self.assertIn('proprietary_codecs=true', plan["environment"]["GN_DEFINES"])
            self.assertIn('ffmpeg_branding="Chrome"', plan["environment"]["GN_DEFINES"])

    def test_low_disk_aborts_before_xcode_or_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(build.platform, "system", return_value="Darwin"), \
                 patch.object(build.platform, "machine", return_value="arm64"), \
                 patch.object(build.shutil, "disk_usage", return_value=type("Usage", (), {"free": 25 * 2**30})), \
                 patch.object(build.subprocess, "check_output") as commands:
                with self.assertRaisesRegex(ValueError, "No download started"):
                    build.preflight(Path(folder) / "build", 200)
                commands.assert_not_called()
                self.assertFalse((Path(folder) / "build").exists())

    def test_unowned_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "user-work.txt"
            original.write_text("preserve me")
            with self.assertRaisesRegex(ValueError, "unowned non-empty"):
                build.validate_root(folder, build.load_lock())
            self.assertEqual(original.read_text(), "preserve me")

    def test_recipe_change_cannot_reuse_previous_checkout(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / build.MARKER).write_text('{"old":"recipe"}')
            with self.assertRaisesRegex(ValueError, "different recipe"):
                build.validate_root(folder, build.load_lock())

    def test_missing_mount_does_not_create_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "missing-volume" / "build"
            with self.assertRaisesRegex(ValueError, "parent must already exist"):
                build.validate_root(str(root), build.load_lock())
            self.assertFalse(root.parent.exists())

    def test_unsafe_paths_are_rejected(self):
        for path in ("relative/path", "/", str(Path.home()), str(build.PACKAGE), "/tmp/has space/build"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build.validate_root(path, build.load_lock())

    def test_inherited_build_flags_do_not_override_recipe_or_leak(self):
        with patch.dict(build.os.environ, {"GN_DEFINES": "proprietary_codecs=false", "CEF_SECRET": "private"}):
            env = build.build_environment(Path("/tmp/cef-build/root"), build.load_lock())
            self.assertNotIn("CEF_SECRET", env)
            self.assertIn("proprietary_codecs=true", env["GN_DEFINES"])
            self.assertEqual(env["DEPOT_TOOLS_UPDATE"], "0")

    def test_existing_modified_automate_script_is_not_executed_or_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "automate-git.py"
            script.write_text("modified")
            with patch.object(build.urllib.request, "urlopen") as network:
                with self.assertRaisesRegex(ValueError, "Hash mismatch"):
                    build.download_verified("https://example.invalid", script, "0" * 64)
                network.assert_not_called()
            self.assertEqual(script.read_text(), "modified")


if __name__ == "__main__":
    unittest.main()
