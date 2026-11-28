import Foundation

/// Compile alongside App/AirPlayReceiverDiscovery.swift to exercise the app's exact browser.
@main struct AirPlayDiscoveryProbe {
    @MainActor static func main() async {
        let discovery = AirPlayReceiverDiscovery()
        discovery.onChange = {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            if let data = try? encoder.encode(discovery.state.services), let text = String(data: data, encoding: .utf8) {
                print(text); fflush(stdout)
            }
        }
        discovery.start()
        try? await Task.sleep(for: .seconds(12))
        let count = discovery.state.services.count
        discovery.stop()
        precondition(!discovery.state.running)
        discovery.clear()
        precondition(discovery.state.services.isEmpty)
        print("discovery-service-count=\(count); stopped-and-cleared=true")
        discovery.onChange = nil
    }
}
