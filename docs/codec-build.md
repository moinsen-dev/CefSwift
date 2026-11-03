# Experimental macOS arm64 codec build

## Moinsen TV experiment parked — 2026-11-03

The owner has paused Moinsen TV while its future direction remains undecided.
This branch preserves the existing codec recipe, local distribution importer and
tests as a source checkpoint. No new Chromium build or provider certification is
being started. The authoritative product status is `ROADMAP.md` in
[`moinsen-dev/moinsen_tv`, branch `codex/moinsen-tv-step3-checkpoint`](https://github.com/moinsen-dev/moinsen_tv/blob/codex/moinsen-tv-step3-checkpoint/ROADMAP.md).
Build artifacts on RetroData and local app/profile data are retained outside Git.
The TV application's pinned dependency revision remains unchanged; its explicit
local codec packaging path can use this checkpoint of the fork.

The September results below are historical. Later Netflix diagnosis reached the
login/catalogue after restoring the default user agent, but protected playback
still failed with MSL 7 / 205064 and an `invalid device` reason. Missing host
signatures were observed separately; causation is unproven. Prime playback also
remains unresolved. Public Widevine playback does not establish provider support.

Checkpoint validation on 2026-11-03: all 22 offline recipe/import tests passed,
including compiling the Swift importer and signing tiny fixture bundles. This
does not rerun Chromium compilation or streaming-provider acceptance.

## Current result — 2026-09-22

Packaging and import succeeded without repeating compilation. The pinned
`automate-git.py` omitted `--no-symbols` for a first minimal distribution on this
branch; the original missing-dSYM failure was reproduced in an isolated output.
The recipe now builds with `--no-distrib` and packages separately using explicit
`make_distrib.py --minimal --arm64-build --no-symbols --no-docs --no-archive`.
The new `package` action reuses compiled artifacts, without preparing/fetching
sources or invoking Ninja. Unique `<build-root>/packages/build-*` destinations
preserve previous partial and successful outputs.

```sh
python3 Scripts/cef-codec-build.py package --build-root /Volumes/RetroData/cef-151-codecs
```

Validation checks source versions/revisions, generated GN flags, arm64, and an
identical SHA-256 between the compiled and packaged framework. No-history
Chromium checkouts lack tag refs: the committed `chrome/VERSION` is checked and
the observed Chromium commit is recorded. Metadata labels host information as
packaging-time, not an independently recorded compilation environment.

Verified output for this run:
`/Volumes/RetroData/cef-151-codecs/packages/build-kwzjfk8v/cef_binary_151.3.24+g2384915+chromium-151.0.7922.174_macosarm64_minimal`.
Framework SHA-256 before app signing:
`45641f62d77a87a09eb7abd8fd19caeb7c114267e05680b422e0a030d00888d3`.
The Moinsen spike bundle passes deep/strict codesign verification. Runtime probes
report HTML5/MSE H.264 and AAC, plus Widevine H.264/AAC access and MediaKeys creation.
The separate Moinsen spike also passed a public Shaka Widevine H.264/AAC playback
probe: `com.widevine.alpha`, `usable` key status, advancing decoded video frames,
playback time and audio bytes. Clear H.264/AAC video was visibly confirmed.
Protected video was white in the automated screenshot; the user subsequently
reported the other tests, including Widevine, working. Netflix still reports
an unsupported browser and Prime still stalls at playback. This is not VMP
certification or Netflix/Prime approval.
Runtime evidence lives in the Moinsen TV project at
`docs/evidence/cef-media-playback-2026-09-22.json`.

22 offline tests passed (including packaging regression and preserving prior
outputs); 80 Swift tests in 22 suites passed, with two existing skips. Actionlint
and diff checks passed. Local changes are not committed or published.

## Historical build progress — 2026-09-12

Status: 2026-09-12. The first real source build completed checkout and setup hooks,
then was deliberately interrupted during the CEF API/sandbox hash check because
the startup disk fell to about 9 GiB free with about 45 GiB of system swap in use.
Sources/tools (~31 GiB) remain on RetroData. **No completed compilation,
distribution, codec playback, VMP or Netflix/Prime acceptance is claimed.**
After the user closed other programs, preflight passed with 39.3 GiB internal
space. The resumed API check passed all 26 versioned hashes. A missing depot_tools
Python bootstrap was reproduced and fixed (see below); GN generated 31,835 targets
and Ninja started with its actual command line confirming `-j 4`. Check the live
process and `/Volumes/RetroData/cef-151-codecs/build-7922-release.log` before any
further resume; do not start a second build in the same directory.
At about 11:45 Europe/Berlin the log reached 446/58,664 build steps, including
actual C++ compilation and archiving, with about 39 GiB internal space remaining.
That snapshot is not completion or an estimate of remaining wall-clock time.
Implementation branch: `codex/cef-codec-build-pipeline`, local and not published.

## Pins and limits

`CEF_CODEC_BUILD.json` keeps CEF 151.3.24 / Chromium 151.0.7922.174 to isolate the
codec experiment from an API/browser upgrade. This is not a shipping update policy.
It pins the full CEF commit, Chromium tag, CEF-compatible depot_tools commit and
SHA256 of upstream `automate-git.py`. The arm64 non-component Release recipe enables
`proprietary_codecs=true` and `ffmpeg_branding="Chrome"`, without remote compilation.
`is_official_build=false` and `symbol_level=0` keep the first experiment simpler;
this is not an optimized release recipe or a grant of codec/CDM redistribution rights.

Source-pinned does not mean bit-identical: Xcode, OS, tool bootstrapping and upstream
dependency downloads remain inputs. Output records the host, actual GN arguments
and SHA256 of the pre-signing framework binary. It does not certify DRM support.

References: [CEF build guide](https://chromiumembedded.github.io/cef/branches_and_building.html),
[pinned compatibility file](https://github.com/chromiumembedded/cef/blob/2384915b7b1f0fe5ad1107e48d80c34e86b698d7/CHROMIUM_BUILD_COMPATIBILITY.txt),
[pinned automation](https://github.com/chromiumembedded/cef/blob/2384915b7b1f0fe5ad1107e48d80c34e86b698d7/tools/automate/automate-git.py),
[Chromium macOS requirements](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md).

## Build

Use native Apple Silicon, full Xcode 26+, Python 3.11+, Git, APFS, and a dedicated
directory outside the source checkout. This recipe reserves 32 GiB RAM and
**200 GiB free disk** as planning thresholds, not guaranteed upstream minima.
Prefer a larger SSD: source/intermediates/updates can exceed an initial estimate.

The development Mac had 48 GiB RAM/Xcode 26.6 but only about 25–31 GiB free and no
external build disk on 2026-09-11. Default preflight stopped before downloading.
Nothing was deleted to make room.

On 2026-09-12 the user authorized reformatting RetroData as APFS, and the first
real build was started there. External space is now sufficient. Startup-disk
pressure remains a separate gate: swap is on `/System/Volumes/VM`, sharing the
internal APFS container on this Mac.

The script now requires **30 GiB free on the startup VM volume before launch** and
stops its own build process group if that falls below **15 GiB**, or if the build
disk falls below **30 GiB**. These are project safety reserves, not an upstream
guarantee. It never deletes caches, kills other apps or changes macOS swap settings.
Checks run every five seconds during upstream automation; a very sudden disk spike
can still outrun a polling guard. Resolve other workloads/storage pressure first.

Local compilation defaults to `--jobs 4`, using the pinned autoninja's
`NINJA_CORE_ADDITION` (its `NINJA_CORE_LIMIT` only applies to remote execution).
Temporary files use `<build-root>/tmp`; Python output is unbuffered for timely
diagnostics. Source pins/GN codec flags are unchanged. On resume, the full CEF
API/hash check is repeated so the interrupted one-time check is not skipped.

`DEPOT_TOOLS_UPDATE=0` also skips gclient's usual Python bootstrap on a fresh
checkout. After verifying the depot_tools revision, preparation explicitly runs
the pinned `ensure_bootstrap` under the same resource guard. This initializes
tools without updating/syncing that Git checkout. The original failure
(`depot_tools/gn --version`: `python3_bin_reldir.txt not found`) was reproduced;
the real GN probe and generation then passed with the Git pin unchanged.
The prepare-call regression test went red before the fix and green afterward;
all 19 offline tests passed on 2026-09-12, as did actionlint and diff checks.

From this fork checkout, replace the example root with the real build location.
The parent must already exist. `plan` and `preflight` do not download or write:

```sh
python3 Scripts/cef-codec-build.py plan --build-root /Volumes/BuildSSD/cef-151-codecs
python3 Scripts/cef-codec-build.py preflight --build-root /Volumes/BuildSSD/cef-151-codecs
python3 Scripts/cef-codec-build.py build --build-root /Volumes/BuildSSD/cef-151-codecs
```

Only the explicit `build` starts downloads/compilation, after preflight. Re-running
resumes the same marked directory. An unmarked non-empty directory or changed lock
is rejected; no `--force-clean`/`--force-update` is requested. Upstream automation
manages generated files inside this directory: keep hand edits and other projects
out. A changed CEF/Chromium pin requires a fresh dedicated directory.

`--min-free-gib N` permits a consciously selected reserve on a known build machine;
do not lower it to force a first build on the current nearly full disk. Preflight
does not continuously monitor space or predict remaining work. Compiler/toolchain
fixes may still be needed because the full build is not yet exercised.

Successful output (each attempt gets a new directory):

```text
<build-root>/packages/build-<unique>/
  cef_binary_151.3.24+g2384915+chromium-151.0.7922.174_macosarm64_minimal/
    cef-build.json
    args.gn
    include/cef_version.h
    Release/Chromium Embedded Framework.framework/
```

Metadata is emitted only after upstream build/package success and verification of
CEF revision, generated arguments, version, architecture and framework SHA256.
Transfer the entire distribution, including metadata, to bundle on another Mac.
The pipeline leaves it unpacked and never publishes an artifact.

## Import explicitly

The updated plugin accepts a custom distribution, without calling the CDN downloader
or aliasing its `.cef/` cache. Invalid version/platform/header/hash/architecture stops
bundling; there is no automatic fallback. It converts only the copy to versioned
framework layout, preserving the original build artifact. Metadata is copied to
`Contents/Resources/cef-build.json` before Apple signing; its binary hash describes
the original pre-signing binary, not the final signed app binary.

```sh
swift package --package-path Examples \
  --allow-writing-to-package-directory --allow-network-connections all \
  cef bundle --product Browser --configuration debug --sign - \
  --local-distribution /Volumes/BuildSSD/cef-151-codecs/chromium/src/cef/binary_distrib/cef_binary_151.3.24+g2384915+chromium-151.0.7922.174_macosarm64_minimal \
  --output dist/codec
```

Network permission is declared globally by the plugin, but local import does not
download CEF. A separate output directory preserves existing bundles. The local
metadata is provenance, not a signed vendor attestation or runtime codec test.

For an app pinned to the older published revision, first inspect `swift package
show-dependencies` and existing edits, then temporarily select this checkout with
`swift package edit CefSwift --path <fork-path>`. Restore with `swift package unedit
CefSwift`; never force over unexpected edits. Publish/re-pin is a separate step.

## Tests and acceptance

```sh
python3 -m unittest discover -s Scripts/tests -v
swift test
codesign --verify --deep --strict /path/to/codec/App.app
```

Offline tests use tiny Mach-O fixtures, not CEF, to verify refusal/mismatch handling,
source preservation, copied metadata and a signed app with five helpers. Ordinary
CI runs these tests, never a full Chromium build.

After the real build: run Moinsen TV's `cef_media_capabilities.mjs` to check
H.264/AAC HTML5/MSE and EME support. Then play public clear and Widevine-protected
content, checking advancing time/frames, audible sound, pause/seek/fullscreen.
Measure VMP separately; test Netflix/Prime accounts only afterward. CDM presence or
EME initialization alone proves none of those later gates.

The current bundler performs local Apple signing only. It has no VMP hook, hardened
runtime release configuration or notarization and is not distribution-ready.
Agree vendor-specific layout/audit requirements first. On macOS, VMP signing
precedes Apple signing: [Castlabs 3PL](https://github.com/castlabs/electron-releases/wiki/EVS#3pl).
