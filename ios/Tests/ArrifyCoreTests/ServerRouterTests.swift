import XCTest
import CryptoKit
@testable import ArrifyCore

final class ServerRouterTests: XCTestCase {
    func testPrivateFirstAndWrongServerNeverGetsCredentials() async throws {
        for wrong in [false, true] {
            let transport = ProofTransport(wrongHome: wrong)
            let client = APIClient(transport: transport, store: MemoryStore())
            try await client.login(endpoint: "https://public.test", homeEndpoint: "https://home.test", username: "test", password: "test", deviceName: "test")
            let hosts = await transport.credentialHosts
            XCTAssertEqual(hosts, [wrong ? "public.test" : "home.test"])
            let status = await client.connection()
            XCTAssertEqual(status.home, !wrong)
            XCTAssertTrue(status.connected)
        }
    }
    func testPinnedHomeRestoresWithoutPublicInternet() async throws {
        let transport = ProofTransport()
        let store = MemoryStore()
        let client = APIClient(transport: transport, store: store)
        try await client.login(endpoint: "https://public.test", homeEndpoint: "https://home.test", username: "test", password: "test", deviceName: "test")
        await transport.disablePublic()
        let restored = APIClient(transport: transport, store: store)
        let address = try await restored.restore()
        XCTAssertEqual(address, "https://public.test")
        let status = await restored.connection()
        XCTAssertTrue(status.home)
    }
    func testInvalidNonceAndSignatureFallBackToPublic() async throws {
        for replay in [false, true] {
            let transport = ProofTransport(wrongHome: !replay, replayHome: replay)
            let router = ServerRouter(transport: transport)
            await router.configure(primary: try Endpoint("https://public.test"), home: try Endpoint("https://home.test"), id: "test", key: transport.key.publicKey.derRepresentation.base64EncodedString())
            let selected = try await router.selected()
            XCTAssertEqual(selected.url.host, "public.test")
            let hosts = await transport.credentialHosts
            XCTAssertTrue(hosts.isEmpty)
        }
    }
    func testOldCredentialDecodesWithoutNewFields() throws {
        let saved = Credential(endpoint: "https://public.test", serverId: "test", tokens: TokenPair(accessToken: "a", refreshToken: "r", expiresIn: 900, refreshExpiresIn: 9000, sessionId: "s"))
        var fields = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(saved)) as? [String: Any])
        fields.removeValue(forKey: "homeEndpoint"); fields.removeValue(forKey: "serverPublicKey")
        let restored = try JSONDecoder().decode(Credential.self, from: JSONSerialization.data(withJSONObject: fields))
        XCTAssertNil(restored.homeEndpoint)
        XCTAssertNil(restored.serverPublicKey)
        XCTAssertEqual(restored.accessExpires, saved.accessExpires)
    }
}

actor ProofTransport: HTTPTransport {
    nonisolated let key = P256.Signing.PrivateKey()
    let wrongHome: Bool
    let replayHome: Bool
    private var publicOffline = false
    var credentialHosts: [String] = []
    init(wrongHome: Bool = false, replayHome: Bool = false) { self.wrongHome = wrongHome; self.replayHome = replayHome }
    func disablePublic() { publicOffline = true }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let host = request.url!.host!
        if publicOffline, host == "public.test" { throw URLError(.notConnectedToInternet) }
        if request.url!.path.hasSuffix("capabilities") {
            return try result(["server_id": "test", "server_public_key": key.publicKey.derRepresentation.base64EncodedString(), "features": ["rotating_refresh", "native_media_manifest_v1", "native_catalog_v1", "native_playback_v1"]])
        }
        if request.url!.path.hasSuffix("server-proof") {
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            let input = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: String])
            let nonce = host == "home.test" && replayHome ? String(repeating: "0", count: 64) : input["nonce"]!
            let signer = host == "home.test" && wrongHome ? P256.Signing.PrivateKey() : key
            let signature = try signer.signature(for: Data("arrify-server-proof-v1\ntest\n\(nonce)".utf8))
            return try result(["server_id": "test", "nonce": nonce, "signature": signature.derRepresentation.base64EncodedString()])
        }
        credentialHosts.append(host)
        return try result(["access_token": "access", "refresh_token": "refresh", "expires_in": 900, "refresh_expires_in": 9000, "session_id": "session"])
    }
    func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws {}
    private func result(_ value: [String: Any]) throws -> HTTPResult { HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: value)) }
}
