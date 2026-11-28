import Foundation
import Observation
@preconcurrency import Network

/// Advertised receivers are an inventory, never evidence of this device's selected route.
struct AirPlayReceiverService: Codable, Equatable, Sendable {
    let name: String
    let type: String
    let domain: String
    let properties: [String: String]

    init(name: String, type: String, domain: String, properties: [String: String]) {
        self.name = name; self.type = type; self.domain = domain
        // Keep only routing identity/model fields; no URLs, credentials or media metadata.
        let allowed = ["deviceid", "pi", "psi", "gid", "pgid", "tsid", "model", "am", "manufacturer"]
        self.properties = properties.filter { allowed.contains($0.key) }
    }

    fileprivate init?(_ result: NWBrowser.Result) {
        guard case let .service(name, type, domain, _) = result.endpoint else { return nil }
        var properties: [String: String] = [:]
        if case let .bonjour(txt) = result.metadata {
            for key in ["deviceid", "pi", "psi", "gid", "pgid", "tsid", "model", "am", "manufacturer"] {
                if let value = txt[key] { properties[key] = value }
            }
        }
        self.init(name: name, type: type, domain: domain, properties: properties)
    }
}

/// Generation fencing discards late browser callbacks after stop, restart or sign-out.
struct AirPlayDiscoveryState {
    private(set) var generation = UUID()
    private(set) var running = false
    private(set) var statuses: [String: String] = [:]
    private var servicesByType: [String: [AirPlayReceiverService]] = [:]
    var services: [AirPlayReceiverService] {
        servicesByType.keys.sorted().flatMap { servicesByType[$0] ?? [] }
    }
    mutating func begin() -> UUID {
        generation = UUID(); running = true; statuses = [:]; servicesByType = [:]
        return generation
    }
    mutating func stop() { running = false; generation = UUID() }
    mutating func clear() { stop(); statuses = [:]; servicesByType = [:] }
    @discardableResult mutating func update(type: String, generation token: UUID,
                                            services: [AirPlayReceiverService]? = nil, status: String? = nil) -> Bool {
        guard running, token == generation else { return false }
        let beforeServices = self.services, beforeStatuses = statuses
        if let services {
            // Preserve same-name receivers and AirPlay/RAOP records independently.
            // A group ID is not a receiver identity or proof of sender ownership.
            servicesByType[type] = Array(services.sorted {
                if $0.domain != $1.domain { return $0.domain < $1.domain }
                if $0.name != $1.name { return $0.name < $1.name }
                return ($0.properties["deviceid"] ?? "") < ($1.properties["deviceid"] ?? "")
            }.prefix(128))
        }
        if let status { statuses[type] = status }
        return beforeServices != self.services || beforeStatuses != statuses
    }
}

/// Runs only during explicit local diagnostics. Browses advertisements; never connects,
/// pairs, changes a speaker, or changes the audio session. The capture owns its lifetime.
@MainActor @Observable final class AirPlayReceiverDiscovery {
    private(set) var state = AirPlayDiscoveryState()
    @ObservationIgnored var onChange: (@MainActor () -> Void)?
    @ObservationIgnored private var browsers: [NWBrowser] = []

    func start() {
        stop()
        let token = state.begin()
        for type in ["_airplay._tcp", "_raop._tcp"] {
            let parameters = NWParameters.tcp
            parameters.includePeerToPeer = false
            let browser = NWBrowser(for: .bonjourWithTXTRecord(type: type, domain: "local."), using: parameters)
            browser.browseResultsChangedHandler = { [weak self] results, _ in
                let services = results.compactMap(AirPlayReceiverService.init)
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if self.state.update(type: type, generation: token, services: services) { self.onChange?() }
                }
            }
            browser.stateUpdateHandler = { [weak self] status in
                let value: String
                switch status {
                case .setup: value = "setup"
                case .ready: value = "ready"
                case .waiting(let error): value = "waiting: \(error)"
                case .failed(let error): value = "failed: \(error)"
                case .cancelled: value = "cancelled"
                @unknown default: value = "unknown"
                }
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    if self.state.update(type: type, generation: token, status: value) { self.onChange?() }
                }
            }
            browsers.append(browser)
            browser.start(queue: .main)
        }
    }
    func stop() {
        state.stop()
        for browser in browsers {
            browser.browseResultsChangedHandler = nil; browser.stateUpdateHandler = nil
            browser.cancel()
        }
        browsers = []
    }
    func clear() { stop(); state.clear() }
}
