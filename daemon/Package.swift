// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "LurkDaemon",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "lurk-daemon", targets: ["LurkDaemon"])
    ],
    dependencies: [
    ],
    targets: [
        .executableTarget(
            name: "LurkDaemon",
            dependencies: [],
            path: "Sources/LurkDaemon",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ApplicationServices"),
                .linkedFramework("EventKit"),
                .linkedFramework("CoreGraphics"),
            ]
        )
    ]
)
