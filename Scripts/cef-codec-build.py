#!/usr/bin/env python3
"""Pinned, opt-in macOS arm64 CEF codec build. No download during plan/preflight.

This is a build recipe, not codec licensing, Widevine certification, or evidence
of protected playback. See docs/codec-build.md for resource and acceptance gates.
"""
import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

PACKAGE = Path(__file__).resolve().parent.parent
# Check the actual startup VM volume, even if this fork is on an external disk.
SYSTEM_STORAGE = Path("/System/Volumes/VM")
MARKER = ".cefswift-codec-build.json"
FRAMEWORK = Path("Release/Chromium Embedded Framework.framework")
BINARY = FRAMEWORK / "Chromium Embedded Framework"


def load_lock():
    lock = json.loads((PACKAGE / "CEF_CODEC_BUILD.json").read_text())
    manifest = json.loads((PACKAGE / "CEF_VERSION.json").read_text())
    for key in ("cef", "chromium"):
        if lock[key] != manifest[key]:
            raise ValueError(f"Codec build lock disagrees with CEF_VERSION.json: {key}. Update both deliberately.")
    if lock["schema"] != 1 or lock["platform"] != "macosarm64":
        raise ValueError("Only schema 1 / native macosarm64 is supported.")
    return lock


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def output(*args, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def run(args, *, cwd, env):
    print("+ " + shlex.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def stop_build(process):
    """Only signal the process group created for this invocation, never other apps."""
    if process.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue


def run_build(args, *, cwd, env, minimum_internal_gib=15, poll_seconds=5):
    """Monitor the startup/data filesystem as well as the external build disk.

    macOS swap stays on the internal APFS container even with external sources.
    No cleanup is attempted on a resource stop; the source checkout is preserved.
    """
    print("+ " + shlex.join(map(str, args)), flush=True)
    process = subprocess.Popen(list(map(str, args)), cwd=cwd, env=env, start_new_session=True)
    try:
        while process.poll() is None:
            free = shutil.disk_usage(SYSTEM_STORAGE).free / 2**30
            if free < minimum_internal_gib:
                raise ValueError(f"Internal disk reserve fell to {free:.1f} GiB; stopping only this build. Sources preserved.")
            if shutil.disk_usage(cwd).free / 2**30 < 30:
                raise ValueError("Build disk reserve fell below 30 GiB; stopping only this build. Sources preserved.")
            time.sleep(poll_seconds)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)
    finally:
        stop_build(process)


def validate_root(raw, lock):
    root = Path(raw)
    if not root.is_absolute() or any(c.isspace() for c in str(root)):
        raise ValueError("Build root must be absolute and contain no whitespace.")
    root = root.resolve()
    if any(c.isspace() for c in str(root)):
        raise ValueError("Resolved build root must contain no whitespace (check symlink targets).")
    if root in (Path("/"), Path.home(), PACKAGE) or len(root.parts) < 4:
        raise ValueError("Choose a dedicated build subdirectory, not a volume, home, or repository root.")
    if root == PACKAGE or PACKAGE in root.parents or root in PACKAGE.parents:
        raise ValueError("Build outside the CefSwift source checkout.")
    if not root.parent.is_dir():
        raise ValueError("Build root's parent must already exist (mount the chosen build disk first).")
    marker = root / MARKER
    if root.exists():
        if not root.is_dir():
            raise ValueError("Build root is not a directory.")
        if marker.exists():
            if json.loads(marker.read_text()) != lock:
                raise ValueError("Build root belongs to a different recipe. Use a new directory; nothing was deleted.")
        elif any(root.iterdir()):
            raise ValueError("Refusing an unowned non-empty directory. Use a dedicated empty build root.")
    return root


def preflight(root, minimum_gib):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("This recipe requires a native arm64 Mac (not Rosetta or Linux).")
    if not math.isfinite(minimum_gib) or minimum_gib <= 0:
        raise ValueError("--min-free-gib must be positive.")
    free = shutil.disk_usage(root if root.exists() else root.parent).free / 2**30
    print(f"Free disk: {free:.1f} GiB; requested planning reserve: {minimum_gib:g} GiB", flush=True)
    if free < minimum_gib:
        raise ValueError("Insufficient disk reserve. No download started. Choose another build disk/host.")
    internal_free = shutil.disk_usage(SYSTEM_STORAGE).free / 2**30
    print(f"Internal disk free: {internal_free:.1f} GiB; startup reserve: 30 GiB", flush=True)
    if internal_free < 30:
        raise ValueError("Insufficient internal disk reserve for macOS swap/caches. No download started. Free space or reduce other workloads first.")
    # diskutil accepts a device/mount point, not an arbitrary build directory.
    device = output("/bin/df", "-P", str(root if root.exists() else root.parent)).splitlines()[-1].split()[0]
    disk = plistlib.loads(subprocess.check_output(["/usr/sbin/diskutil", "info", "-plist", device]))
    if disk.get("FilesystemType", "").lower() != "apfs":
        raise ValueError("Use an APFS build volume, as required by Chromium's macOS build instructions.")
    developer_dir = output("/usr/bin/xcode-select", "-p")
    if ".app/Contents/Developer" not in developer_dir:
        raise ValueError("Select a full Xcode installation, not standalone Command Line Tools.")
    xcode = output("/usr/bin/xcodebuild", "-version")
    if not re.search(r"Xcode (2[6-9]|[3-9][0-9])\.", xcode):
        raise ValueError("CEF 151 requires Xcode 26 or newer; verify the selected SDK before building.")
    ram_gib = int(output("/usr/sbin/sysctl", "-n", "hw.memsize")) / 2**30
    if ram_gib < 32:
        raise ValueError("This local-build recipe reserves at least 32 GiB RAM; choose a larger build Mac.")
    return {"free_gib": round(free, 1), "internal_free_gib": round(internal_free, 1), "ram_gib": ram_gib, "xcode": xcode,
            "macos": platform.mac_ver()[0], "python": platform.python_version()}


def build_environment(root, lock, jobs=4):
    if jobs < 1:
        raise ValueError("--jobs must be positive.")
    # automate-git prints GN_/CEF_/DEPOT_TOOLS_ variables. Do not leak unrelated
    # credentials/configuration, or inherit flags that can change the recipe.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GN_", "CEF_", "GCLIENT_", "GYP_", "DEPOT_TOOLS_"))}
    env.update({"PATH": str(root / "depot_tools") + os.pathsep + os.environ.get("PATH", ""),
                "DEPOT_TOOLS_UPDATE": "0", "GN_OUT_CONFIGS": "Release_GN_arm64",
                "PYTHONUNBUFFERED": "1", "TMPDIR": str(root / "tmp"),
                # Pinned autoninja uses this offset for local builds. Its
                # NINJA_CORE_LIMIT applies only to remote execution, not here.
                "NINJA_CORE_ADDITION": str(jobs - (os.cpu_count() or 1)),
                "GN_DEFINES": " ".join(f"{key}={json.dumps(value)}" for key, value in lock["gn_args"].items())})
    return env


def build_command(root, lock):
    return [sys.executable, str(root / "automate-git.py"), f"--download-dir={root}",
            f"--depot-tools-dir={root / 'depot_tools'}", f"--branch={lock['cef_branch']}",
            f"--checkout={lock['cef_commit']}", f"--chromium-checkout=refs/tags/{lock['chromium']}",
            "--arm64-build", "--no-debug-build", "--no-depot-tools-update", "--no-chromium-history",
            "--no-distrib",
            "--build-log-file", "--force-build"]


def package_command(root, destination):
    return [sys.executable, str(root / "chromium/src/cef/tools/make_distrib.py"),
            f"--output-dir={destination}", "--ninja-build", "--arm64-build",
            "--minimal", "--no-symbols", "--no-docs", "--no-archive", "--quiet"]


def download_verified(url, destination, expected):
    if destination.exists():
        if sha256(destination) != expected:
            raise ValueError(f"Hash mismatch in existing {destination}; inspect it before retrying.")
        return
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Upstream automate-git.py SHA256 does not match the build lock.")
    destination.write_bytes(data)


def prepare(root, lock, env):
    root.mkdir(exist_ok=True)
    (root / MARKER).write_text(json.dumps(lock, indent=2) + "\n")
    (root / "tmp").mkdir(exist_ok=True)
    base = f"https://raw.githubusercontent.com/chromiumembedded/cef/{lock['cef_commit']}"
    download_verified(base + "/tools/automate/automate-git.py", root / "automate-git.py", lock["automate_sha256"])
    with urllib.request.urlopen(base + "/CHROMIUM_BUILD_COMPATIBILITY.txt", timeout=60) as response:
        compatibility = ast.literal_eval(response.read().decode())
    if (compatibility["chromium_checkout"] != "refs/tags/" + lock["chromium"] or
            compatibility["depot_tools_checkout"] != lock["depot_tools_commit"]):
        raise ValueError("CEF upstream compatibility does not match the lock. No Chromium checkout started.")
    depot = root / "depot_tools"
    if not depot.exists():
        run(["git", "clone", "--no-checkout", "https://chromium.googlesource.com/chromium/tools/depot_tools.git", depot], cwd=root, env=env)
        run(["git", "checkout", "--detach", lock["depot_tools_commit"]], cwd=depot, env=env)
    if output("git", "rev-parse", "HEAD", cwd=depot) != lock["depot_tools_commit"]:
        raise ValueError("Existing depot_tools has a different revision. No reset was performed.")
    # Disabling gclient auto-updates also skips its Python bootstrap on a fresh
    # checkout. Initialize the pinned tools explicitly; unlike update_depot_tools,
    # ensure_bootstrap does not update/sync the Git repository.
    run_build([depot / "ensure_bootstrap"], cwd=depot, env=env)


def validate_compiled(root, lock):
    src = root / "chromium/src"
    if output("git", "rev-parse", "HEAD", cwd=src / "cef") != lock["cef_commit"]:
        raise ValueError("Built CEF source revision differs from the lock.")
    # --no-chromium-history creates a checkout without local tag refs. Read the
    # committed version, not a mutable working-tree file or an absent tag.
    version = dict(line.split("=", 1) for line in output("git", "show", "HEAD:chrome/VERSION", cwd=src).splitlines())
    if ".".join(version[key] for key in ("MAJOR", "MINOR", "BUILD", "PATCH")) != lock["chromium"]:
        raise ValueError("Chromium source version differs from the lock.")
    if output("git", "rev-parse", "HEAD", cwd=root / "depot_tools") != lock["depot_tools_commit"]:
        raise ValueError("depot_tools checkout differs from the lock.")
    args_text = (src / "out/Release_GN_arm64/args.gn").read_text()
    required = dict(lock["gn_args"], is_debug=False, target_cpu="arm64")
    for key, value in required.items():
        match = re.search(rf"^\s*{key}\s*=\s*(.+?)\s*$", args_text, re.M)
        if not match or json.loads(match[1]) != value:
            raise ValueError(f"Generated args.gn does not contain the required {key}={value}.")
    binary = src / "out/Release_GN_arm64/cefclient.app/Contents/Frameworks/Chromium Embedded Framework.framework/Versions/A/Chromium Embedded Framework"
    if not binary.is_file():
        raise ValueError("No compiled Release framework. Run build first; package never compiles.")
    if output("/usr/bin/lipo", "-archs", str(binary)).split() != ["arm64"]:
        raise ValueError("Compiled Release framework is not arm64.")
    return args_text, binary


def package_existing(root, lock, host, env):
    validate_compiled(root, lock)
    # Upstream deletes an existing distribution directory. A unique destination
    # preserves every previous artifact, including interrupted packaging attempts.
    packages = root / "packages"
    packages.mkdir(exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="build-", dir=packages))
    run_build(package_command(root, destination), cwd=root / "chromium/src/cef/tools", env=env)
    dist = destination / f"cef_binary_{lock['cef']}_macosarm64_minimal"
    finalize(root, lock, host, dist)
    return dist


def finalize(root, lock, host, dist):
    args_text, source_binary = validate_compiled(root, lock)
    header = (dist / "include/cef_version.h").read_text()
    if f'#define CEF_VERSION "{lock["cef"]}"' not in header:
        raise ValueError("Distribution CEF version differs from the lock.")
    archs = output("/usr/bin/lipo", "-archs", str(dist / BINARY)).split()
    if archs != ["arm64"]:
        raise ValueError(f"Expected arm64 framework, got {archs}.")
    digest = sha256(dist / BINARY)
    if digest != sha256(source_binary):
        raise ValueError("Packaged framework differs from the compiled source artifact.")
    metadata = {"schema": 1, "cef": lock["cef"], "chromium": lock["chromium"],
                "chromium_commit": output("git", "rev-parse", "HEAD", cwd=root / "chromium/src"),
                "platform": lock["platform"], "cef_commit": lock["cef_commit"],
                "framework_sha256": digest, "gn_args": args_text,
                "recipe": lock, "host": host,
                "host_phase": "packaging; compilation host not independently recorded",
                "verification": "compiled artifact packaged and checked; playback and VMP NOT tested"}
    (dist / "cef-build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (dist / "args.gn").write_text(args_text)
    print(f"\nLocal distribution: {dist}\nNo DRM playback or provider certification is implied.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "preflight", "build", "package"])
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--jobs", type=int, default=4, help="Local compiler jobs (default: 4 on this shared Mac).")
    parser.add_argument("--min-free-gib", type=float, default=200,
                        help="Conservative planning reserve, not an upstream guaranteed minimum (default: 200).")
    args = parser.parse_args()
    lock = load_lock()
    root = validate_root(args.build_root, lock)
    if args.action == "package" and not (root / MARKER).is_file():
        raise ValueError("Package requires an existing marked build root.")
    env = build_environment(root, lock, args.jobs)
    if args.action == "plan":
        print(json.dumps({"build_root": str(root), "cef": lock["cef"],
                          "environment": {k: env[k] for k in ("GN_DEFINES", "GN_OUT_CONFIGS", "DEPOT_TOOLS_UPDATE", "NINJA_CORE_ADDITION", "TMPDIR")},
                          "command": build_command(root, lock),
                          "minimum_free_gib": args.min_free_gib, "minimum_internal_free_gib": 30,
                          "jobs": args.jobs, "downloads_started": False}, indent=2))
        return
    host = preflight(root, args.min_free_gib)
    host["jobs"] = args.jobs
    if args.action == "preflight":
        print(json.dumps(host, indent=2))
        return
    if args.action == "package":
        package_existing(root, lock, host, env)
        return
    prepare(root, lock, env)
    # A previous interruption may have happened in automate-git's one-time
    # sandbox/API hash check. Its no-history resume path skips that check, so
    # explicitly finish it before resuming an existing source checkout.
    api_checker = root / "chromium/src/cef/tools/version_manager.py"
    if api_checker.is_file():
        run_build([sys.executable, str(api_checker), "-a"], cwd=api_checker.parent.parent, env=env)
    run_build(build_command(root, lock), cwd=root, env=env)
    package_existing(root, lock, host, env)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Build interrupted; source files preserved.", file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
