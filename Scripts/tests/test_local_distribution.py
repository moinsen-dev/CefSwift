"""Compile and exercise the real Swift import/assembler against tiny Mach-O fixtures."""
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[2]
VERSION = "1.2.3+gtest+chromium-1.2.3.4"
NAME = "Chromium Embedded Framework"


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "requires native arm64 macOS")
class LocalDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool_dir = tempfile.TemporaryDirectory()
        cls.harness = Path(cls.tool_dir.name) / "import-test"
        sources = [PACKAGE / "Plugins/CefPlugin" / name for name in
                   ("Shell.swift", "FrameworkLayout.swift", "LocalDistribution.swift", "AppBundler.swift")]
        subprocess.run(["swiftc", *map(str, sources), str(PACKAGE / "Scripts/tests/LocalDistributionHarness.swift"),
                        "-o", str(cls.harness)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tool_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "distribution"
        self.framework = self.root / "Release" / f"{NAME}.framework"
        (self.framework / "Libraries").mkdir(parents=True)
        resources = self.framework / "Resources"
        resources.mkdir()
        (resources / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleExecutable": NAME, "CFBundleIdentifier": "dev.cefswift.fixture-framework",
            "CFBundlePackageType": "FMWK", "CFBundleVersion": "1.2.3"}))
        self.binary = self.framework / NAME
        subprocess.run(["xcrun", "clang", "-arch", "arm64", "-dynamiclib", "-x", "c", "-",
                        "-o", str(self.binary)], input="int fixture(void) { return 42; }", text=True,
                       capture_output=True, check=True)
        (self.root / "include").mkdir()
        (self.root / "include/cef_version.h").write_text(f'#define CEF_VERSION "{VERSION}"\n')
        self.metadata = {"schema": 1, "cef": VERSION, "chromium": "1.2.3.4", "platform": "macosarm64",
                         "framework_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        self.write_metadata()

    def write_metadata(self):
        (self.root / "cef-build.json").write_text(json.dumps(self.metadata))

    def invoke(self, *args):
        return subprocess.run([str(self.harness), str(self.root), *map(str, args)], text=True, capture_output=True)

    def test_matching_custom_build_is_accepted_without_modifying_source(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCAL_IMPORT_OK", result.stdout)
        self.assertFalse((self.framework / "Versions").exists())

    def test_missing_provenance_fails_instead_of_downloading(self):
        (self.root / "cef-build.json").unlink()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs a valid cef-build.json", result.stderr)

    def test_version_and_platform_mismatches_are_rejected(self):
        for field, wrong in (("cef", "999"), ("chromium", "999"), ("platform", "macosx64"), ("schema", 2)):
            with self.subTest(field=field):
                original = self.metadata[field]
                self.metadata[field] = wrong
                self.write_metadata()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("does not match the pinned", result.stderr)
                self.metadata[field] = original

    def test_tampered_binary_is_rejected(self):
        with self.binary.open("ab") as stream:
            stream.write(b"changed")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA256 mismatch", result.stderr)

    def test_mismatched_headers_are_rejected(self):
        (self.root / "include/cef_version.h").write_text('#define CEF_VERSION "other"\n')
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("headers do not match", result.stderr)

    def test_wrong_macho_architecture_is_rejected_even_with_matching_metadata(self):
        subprocess.run(["xcrun", "clang", "-arch", "x86_64", "-dynamiclib", "-x", "c", "-", "-o", str(self.binary)],
                       input="int fixture(void) { return 42; }", text=True, capture_output=True, check=True)
        self.metadata["framework_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.write_metadata()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match arm64", result.stderr)

    def test_bundle_converts_only_copy_preserves_provenance_and_signs_all_helpers(self):
        destination = Path(self.temp.name) / "output"
        destination.mkdir()
        result = self.invoke(destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = destination / "ImportFixture.app"
        frameworks = app / "Contents/Frameworks"
        self.assertEqual(len(list(frameworks.glob("*.app"))), 5)
        self.assertTrue((frameworks / f"{NAME}.framework/Versions/Current").is_symlink())
        self.assertEqual((app / "Contents/Resources/cef-build.json").read_bytes(), (self.root / "cef-build.json").read_bytes())
        self.assertFalse((self.framework / "Versions").exists())
        self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), self.metadata["framework_sha256"])
        verified = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)


if __name__ == "__main__":
    unittest.main()
