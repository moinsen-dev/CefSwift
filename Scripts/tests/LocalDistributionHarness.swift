import Foundation

// Exercises the actual plugin import and assembler without loading CEF or using accounts.
@main
struct LocalDistributionHarness {
    static func main() {
        do { try exercise() }
        catch {
            FileHandle.standardError.write(Data("\(error)\n".utf8))
            exit(1)
        }
    }

    static func exercise() throws {
        let args = CommandLine.arguments
        let local = LocalDistribution(root: URL(fileURLWithPath: args[1]))
        try local.validate(cef: "1.2.3+gtest+chromium-1.2.3.4", chromium: "1.2.3.4", platform: "macosarm64")
        if args.count == 3 {
            let bundle = AppBundler(
                appName: "ImportFixture", displayName: "ImportFixture",
                bundleIdentifier: "dev.cefswift.import-fixture", minimumSystemVersion: "14.0",
                privacyUsageDescriptions: [:], mainExecutable: URL(fileURLWithPath: "/usr/bin/true"),
                helperExecutable: URL(fileURLWithPath: "/usr/bin/true"), frameworkSource: local.framework,
                provenanceSource: local.provenance, outputDirectory: URL(fileURLWithPath: args[2]),
                signingIdentity: "-")
            print(try bundle.assemble().path)
        }
        print("LOCAL_IMPORT_OK")
    }
}
