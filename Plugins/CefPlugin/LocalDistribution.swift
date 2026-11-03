import Foundation

/// An explicit custom build. Never aliases the official CDN cache or claims DRM support.
struct LocalDistribution {
    struct Provenance: Decodable {
        let schema: Int
        let cef: String
        let chromium: String
        let platform: String
        let framework_sha256: String
    }

    let root: URL
    var framework: URL { root.appendingPathComponent("Release/Chromium Embedded Framework.framework") }
    var provenance: URL { root.appendingPathComponent("cef-build.json") }

    func validate(cef: String, chromium: String, platform: String) throws {
        let metadata: Provenance
        do {
            metadata = try JSONDecoder().decode(Provenance.self, from: Data(contentsOf: provenance))
        } catch {
            throw CefPluginError("Local distribution needs a valid cef-build.json: \(error). See docs/codec-build.md.")
        }
        guard metadata.schema == 1, metadata.cef == cef, metadata.chromium == chromium,
              metadata.platform == platform else {
            throw CefPluginError("Local distribution does not match the pinned CEF/Chromium/platform. No CDN fallback attempted.")
        }
        let header = try String(contentsOf: root.appendingPathComponent("include/cef_version.h"), encoding: .utf8)
        guard header.contains("#define CEF_VERSION \"\(cef)\"") else {
            throw CefPluginError("Local distribution headers do not match the CEF pin.")
        }
        try FrameworkLayout.validate(framework: framework)
        let binary = framework.appendingPathComponent(FrameworkLayout.frameworkName)
        let hash = try Shell.runChecked("/usr/bin/shasum", ["-a", "256", binary.path], hint: "Cannot hash the local CEF binary.")
            .stdout.split(whereSeparator: \.isWhitespace).first.map(String.init)
        guard hash == metadata.framework_sha256 else {
            throw CefPluginError("Local framework SHA256 mismatch. Do not reuse provenance from another build.")
        }
        let expectedArch: String
        switch platform {
        case "macosarm64": expectedArch = "arm64"
        case "macosx64": expectedArch = "x86_64"
        default: throw CefPluginError("Unsupported local distribution platform '\(platform)'.")
        }
        let archs = try Shell.runChecked("/usr/bin/lipo", ["-archs", binary.path], hint: "Expected a macOS Mach-O framework.")
            .stdout.split(whereSeparator: \.isWhitespace).map(String.init)
        guard archs == [expectedArch] else {
            throw CefPluginError("Local framework architecture \(archs) does not match \(expectedArch).")
        }
    }
}
