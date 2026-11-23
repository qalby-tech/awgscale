// swift-tools-version:5.5
// The app's side of the tunnel on iOS: a Tauri mobile plugin (build.rs compiles and links it). The name
// of the package and of its library is the crate's (CARGO_PKG_NAME), as swift-rs looks it up so.

import PackageDescription

let package = Package(
    name: "awgscale-tunnel-ios",
    platforms: [
        .macOS(.v10_13),
        .iOS(.v15),
    ],
    products: [
        .library(
            name: "awgscale-tunnel-ios",
            type: .static,
            targets: ["awgscale-tunnel-ios"])
    ],
    dependencies: [
        // copied there by build.rs from the tauri crate
        .package(name: "Tauri", path: "../.tauri/tauri-api")
    ],
    targets: [
        .target(
            name: "awgscale-tunnel-ios",
            dependencies: [
                .byName(name: "Tauri")
            ],
            path: "Sources",
            linkerSettings: [
                .linkedFramework("NetworkExtension")
            ])
    ]
)
