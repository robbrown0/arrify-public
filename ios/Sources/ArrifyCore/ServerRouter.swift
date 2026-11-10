import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct ServerConnection: Equatable, Sendable {
    public var address: String?
    public var home: Bool
    public var connected: Bool
    public var homeNotice: String?
    public init(address: String? = nil, home: Bool = false, connected: Bool = false, homeNotice: String? = nil) {
        self.address = address; self.home = home; self.connected = connected; self.homeNotice = homeNotice
    }
}

/// Identity is enrolled from the public HTTPS origin. An alternate origin never
/// receives account credentials until it proves possession of that signing key.
actor ServerRouter {
    private let transport: any HTTPTransport
    private var primary: Endpoint?
    private var home: Endpoint?
    private var serverID = ""
    private var publicKey: String?
    private var status = ServerConnection()
    private var lastCheck = Date.distantPast
    private var homeRetry = Date.distantPast
    private var selection: Task<Endpoint, Error>?
    private var generation = UUID()
    init(transport: any HTTPTransport) { self.transport = transport }
    func configure(primary: Endpoint, home: Endpoint?, id: String, key: String?) {
        generation = UUID(); selection?.cancel(); selection = nil
        self.primary = primary; self.home = home; serverID = id; publicKey = key
        status = ServerConnection(); lastCheck = .distantPast; homeRetry = .distantPast
    }
    func reset() {
        generation = UUID(); selection?.cancel(); selection = nil
        primary = nil; home = nil; status = ServerConnection(); lastCheck = .distantPast
    }
    func snapshot() -> ServerConnection { status }
    func invalidate() { generation = UUID(); selection?.cancel(); selection = nil; lastCheck = .distantPast; homeRetry = .distantPast }
    func failed(_ endpoint: Endpoint) {
        if endpoint.url == home?.url { homeRetry = Date().addingTimeInterval(30) }
        if status.address == endpoint.url.absoluteString { status.connected = false; lastCheck = .distantPast }
    }
    func selected() async throws -> Endpoint {
        if let selection { return try await selection.value }
        if status.connected, Date().timeIntervalSince(lastCheck) < 30, let address = status.address { return try Endpoint(address) }
        guard let primary else { throw APIError.signInRequired }
        let token = generation
        let home = home; let serverID = serverID; let publicKey = publicKey; let retry = homeRetry
        let task = Task<Endpoint, Error> {
            var notice: String?
            if let home, Date() >= retry {
                do {
                    guard publicKey != nil else { throw APIError.unsupportedServer }
                    try await Self.verify(home, id: serverID, key: publicKey, transport: transport)
                    guard token == generation, !Task.isCancelled else { throw CancellationError() }
                    status = ServerConnection(address: home.url.absoluteString, home: true, connected: true)
                    lastCheck = Date(); return home
                } catch is CancellationError { throw CancellationError() }
                catch APIError.serverChanged { notice = "Home address did not verify as this server. Using public." }
                catch APIError.unsupportedServer { notice = "Update your server to enable verified home connections." }
                catch { notice = "Home address unavailable. Using public." }
            } else if home != nil { notice = "Home address unavailable. Using public." }
            try await Self.verify(primary, id: serverID, key: publicKey, transport: transport)
            guard token == generation, !Task.isCancelled else { throw CancellationError() }
            status = ServerConnection(address: primary.url.absoluteString, home: false, connected: true, homeNotice: notice)
            lastCheck = Date(); return primary
        }
        selection = task
        do {
            let endpoint = try await task.value
            if token == generation { selection = nil }
            return endpoint
        } catch {
            if token == generation { selection = nil; status.connected = false }
            throw error
        }
    }
    static func verify(_ endpoint: Endpoint, id: String, key: String?, transport: any HTTPTransport) async throws {
        var request: URLRequest
        if let key {
            let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            request = URLRequest(url: endpoint.url.appendingPathComponent("api/v1/native/server-proof"))
            request.httpMethod = "POST"; request.httpBody = try JSONSerialization.data(withJSONObject: ["nonce": nonce])
            request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.timeoutInterval = 2
            let result = try await transport.send(request)
            guard result.status == 200, result.data.count < 4096 else { throw APIError.invalidResponse }
            struct Proof: Decodable { let server_id: String; let nonce: String; let signature: String }
            let proof = try JSONDecoder().decode(Proof.self, from: result.data)
            guard proof.server_id == id, proof.nonce == nonce,
                  let bytes = Data(base64Encoded: key), let signature = Data(base64Encoded: proof.signature) else { throw APIError.serverChanged }
            #if canImport(CryptoKit)
            let signer = try P256.Signing.PublicKey(derRepresentation: bytes)
            let signed = try P256.Signing.ECDSASignature(derRepresentation: signature)
            let message = Data("arrify-server-proof-v1\n\(id)\n\(nonce)".utf8)
            guard signer.isValidSignature(signed, for: message) else { throw APIError.serverChanged }
            #else
            throw APIError.unsupportedServer
            #endif
        } else {
            // Compatibility for an existing single-origin server. Never used for home.
            request = URLRequest(url: endpoint.url.appendingPathComponent("api/v1/native/capabilities")); request.timeoutInterval = 2
            let result = try await transport.send(request)
            struct Identity: Decodable { let server_id: String }
            guard result.status == 200, result.data.count < 16384,
                  try JSONDecoder().decode(Identity.self, from: result.data).server_id == id else { throw APIError.serverChanged }
        }
    }
}
