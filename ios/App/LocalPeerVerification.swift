@preconcurrency import Network
import CryptoKit
import Foundation
import ArrifyCore

/// No account, track, speaker name, or credential is advertised on Bonjour.
/// A proof is useful only for the server-issued nonce and expires after 12 seconds.
@MainActor final class LocalPeerVerification {
    static let serviceType = "_arrify-local._tcp"
    static func parameters() -> NWParameters {
        let p = NWParameters.tcp
        p.includePeerToPeer = false
        p.prohibitedInterfaceTypes = [.cellular, .loopback, .other]
        return p
    }
    static func proof(api: APIClient, owner: String, epoch: Int, purpose: String) async throws -> (String, String) {
        let challenge = try await api.localChallenge(owner: owner, epoch: epoch, purpose: purpose)
        let exchange = LocalProofExchange()
        let response = try await exchange.run(service: challenge.service, nonce: challenge.nonce)
        return (challenge.nonce, response)
    }
    private var listener: NWListener?
    private var ownerKey: String?
    private var secret = SymmetricKey(size: .bits256)
    private var service = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    private var lastPublish = Date.distantPast
    private var publishing = false
    private var responders: [UUID: NWConnection] = [:]
    private let monitor = NWPathMonitor()
    private var localPath = false
    init() {
        guard ProcessInfo.processInfo.environment["ARRIFY_UI_TESTING"] != "1" else { return }
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.stop()
                self.localPath = path.status == .satisfied && (path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet))
            }
        }
        monitor.start(queue: .main)
    }
    func update(api: APIClient, state: PlaybackState?, ownsOutput: Bool) async {
        guard ownsOutput, localPath, let state, let owner = state.ownerDeviceId else { stop(); return }
        let key = "\(owner):\(state.ownerEpoch)"
        if ownerKey != key {
            stop(); ownerKey = key
            secret = SymmetricKey(size: .bits256)
            service = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            do {
                let server = try NWListener(using: Self.parameters())
                server.service = .init(name: service, type: Self.serviceType)
                server.newConnectionHandler = { [weak self] connection in
                    Task { @MainActor [weak self] in self?.answer(connection) }
                }
                server.stateUpdateHandler = { [weak self] state in
                    if case .failed = state { Task { @MainActor [weak self] in self?.stop() } }
                }
                listener = server; server.start(queue: .main)
            } catch { stop(); return }
        }
        guard !publishing, Date().timeIntervalSince(lastPublish) >= 10 else { return }
        publishing = true
        defer { publishing = false }
        let advertised = service
        do {
            let hex = secret.withUnsafeBytes { $0.map { String(format: "%02x", $0) }.joined() }
            try await api.publishLocalPresence(owner: owner, epoch: state.ownerEpoch, service: advertised, secret: hex)
            if service == advertised { lastPublish = Date() }
        } catch { /* Ordinary playback continues when local verification is unavailable. */ }
    }
    func stop() {
        listener?.cancel(); listener = nil; ownerKey = nil; lastPublish = .distantPast
        for connection in responders.values { connection.cancel() }; responders.removeAll()
    }
    private func answer(_ connection: NWConnection) {
        guard listener != nil, responders.count < 8 else { connection.cancel(); return }
        let id = UUID(); responders[id] = connection
        let key = secret
        connection.start(queue: .main)
        connection.receive(minimumIncompleteLength: 64, maximumLength: 64) { [weak self] bytes, _, _, error in
            Task { @MainActor [weak self] in
                guard let self, self.responders[id] != nil, error == nil, let bytes, bytes.count == 64,
                    let nonce = String(data: bytes, encoding: .ascii), nonce.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
                    connection.cancel(); self?.responders.removeValue(forKey: id); return
                }
                let digest = HMAC<SHA256>.authenticationCode(for: bytes, using: key).map { String(format: "%02x", $0) }.joined()
                connection.send(content: Data(digest.utf8), completion: .contentProcessed { _ in
                    connection.cancel()
                    Task { @MainActor [weak self] in self?.responders.removeValue(forKey: id) }
                })
            }
        }
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(3))
            self?.responders.removeValue(forKey: id)?.cancel()
        }
    }
}

/// One bounded browse/connect operation; never keeps the controller awake.
@MainActor private final class LocalProofExchange {
    private var browser: NWBrowser?
    private var connection: NWConnection?
    private var completion: CheckedContinuation<String, Error>?
    private var timeout: Task<Void, Never>?
    func run(service: String, nonce: String) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            completion = continuation
            let browser = NWBrowser(for: .bonjour(type: LocalPeerVerification.serviceType, domain: "local."), using: LocalPeerVerification.parameters())
            self.browser = browser
            browser.browseResultsChangedHandler = { [weak self] results, _ in
                Task { @MainActor [weak self] in
                    guard let self, self.connection == nil, self.completion != nil else { return }
                    for result in results {
                        guard case let .service(name, _, _, _) = result.endpoint, name == service else { continue }
                        let connection = NWConnection(to: result.endpoint, using: LocalPeerVerification.parameters())
                        self.connection = connection
                        connection.stateUpdateHandler = { [weak self] state in
                            Task { @MainActor [weak self] in
                                guard let self else { return }
                                if case .failed = state { self.finish(.failure(URLError(.cannotConnectToHost))) }
                                if case .ready = state {
                                    connection.send(content: Data(nonce.utf8), completion: .contentProcessed { error in
                                        if error != nil { Task { @MainActor [weak self] in self?.finish(.failure(URLError(.networkConnectionLost))) } }
                                    })
                                    connection.receive(minimumIncompleteLength: 64, maximumLength: 64) { bytes, _, _, error in
                                        Task { @MainActor [weak self] in
                                            guard error == nil, let bytes, bytes.count == 64, let value = String(data: bytes, encoding: .ascii), value.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else {
                                                self?.finish(.failure(URLError(.badServerResponse))); return
                                            }
                                            self?.finish(.success(value))
                                        }
                                    }
                                }
                            }
                        }
                        connection.start(queue: .main); break
                    }
                }
            }
            browser.stateUpdateHandler = { [weak self] state in
                if case .failed = state { Task { @MainActor [weak self] in self?.finish(.failure(URLError(.cannotFindHost))) } }
            }
            browser.start(queue: .main)
            timeout = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(6)) } catch { return }
                self?.finish(.failure(URLError(.timedOut)))
            }
        }
    }
    private func finish(_ result: Result<String, Error>) {
        guard let continuation = completion else { return }; completion = nil
        timeout?.cancel(); timeout = nil; browser?.cancel(); connection?.cancel()
        continuation.resume(with: result)
    }
}
