// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "ArrifyCore", platforms: [.iOS(.v18), .macOS(.v15)],
    products: [.library(name: "ArrifyCore", targets: ["ArrifyCore"])],
    targets: [.target(name: "ArrifyCore"), .testTarget(name: "ArrifyCoreTests", dependencies: ["ArrifyCore"])])
