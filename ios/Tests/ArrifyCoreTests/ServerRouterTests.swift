import XCTest
import CryptoKit
@testable import ArrifyCore

final class ServerRouterTests: XCTestCase {
    func testIdentityRateLimitCooldownPreventsRepeatedProbes() async throws {
        let transport = ProofTransport(), router = ServerRouter(transport:transport)
        await router.configure(primary:try Endpoint("https://public.test"),home:nil,id:"test",key:transport.key.publicKey.derRepresentation.base64EncodedString())
        await transport.setProofFailure(429)
        for _ in 0..<2 {
            do { _ = try await router.selected(); XCTFail("Rate-limited proof must defer verification") }
            catch APIError.retryDeferred(let until) { XCTAssertGreaterThan(until,Date()) }
        }
        let requests = await transport.publicProofs; XCTAssertEqual(requests,1)
    }
    func testGatewayFailureDuringIdentityVerificationRemainsRetryable() async throws {
        let transport = ProofTransport()
        let router = ServerRouter(transport:transport)
        await router.configure(primary:try Endpoint("https://public.test"),home:nil,id:"test",key:transport.key.publicKey.derRepresentation.base64EncodedString())
        await transport.setProofFailure(503)
        do { _ = try await router.selected(); XCTFail("Unavailable proof must not verify the server") }
        catch APIError.rejected(let status,let code,_) { XCTAssertEqual(status,503); XCTAssertEqual(code,"server_verification_unavailable") }
        await transport.setProofFailure(nil)
        let endpoint = try await router.selected(); XCTAssertEqual(endpoint.url.host,"public.test")
        let hosts = await transport.credentialHosts; XCTAssertTrue(hosts.isEmpty)
    }
    func testSlowArtworkDoesNotInvalidateTheVerifiedControlRoute() async throws {
        let transport = ProofTransport(), client = APIClient(transport:transport,store:MemoryStore())
        try await client.login(endpoint:"https://public.test",homeEndpoint:"https://home.test",username:"test",password:"test",deviceName:"test")
        let before = await transport.homeProofs
        await transport.setArtworkFailure()
        do { _ = try await client.artwork("1"); XCTFail("Image timeout expected") } catch is URLError {}
        _ = try await client.catalog(query:"")
        let after = await transport.homeProofs; XCTAssertEqual(after,before)
        let connection = await client.connection(); XCTAssertTrue(connection.connected); XCTAssertTrue(connection.home); XCTAssertFalse(connection.degraded)
    }
    func testConnectionHealthRequiresRepeatedSlowResponsesAndRecovers() async throws {
        let transport = ProofTransport()
        let router = ServerRouter(transport: transport)
        let endpoint = try Endpoint("https://public.test")
        await router.configure(primary: endpoint, home: nil, id: "test", key: transport.key.publicKey.derRepresentation.base64EncodedString())
        _ = try await router.selected()
        await router.observed(endpoint, seconds: 3, statusCode: 200)
        var status = await router.snapshot()
        XCTAssertFalse(status.degraded)
        await router.observed(endpoint, seconds: 3, statusCode: 200)
        status = await router.snapshot()
        XCTAssertTrue(status.degraded)
        XCTAssertTrue(status.connected)
        await router.observed(endpoint, seconds: 0.1, statusCode: 200)
        status = await router.snapshot()
        XCTAssertFalse(status.degraded)
        await router.failed(endpoint)
        status = await router.snapshot()
        XCTAssertFalse(status.connected)
        await router.observed(endpoint, seconds: 0.1, statusCode: 200)
        status = await router.snapshot()
        XCTAssertTrue(status.connected)
    }
    func testArtworkScopeSurvivesRestoreButNotSignOut() async throws {
        let transport = ProofTransport()
        let store = MemoryStore()
        let client = APIClient(transport: transport, store: store)
        try await client.login(endpoint: "https://public.test", username: "Test", password: "test", deviceName: "test")
        let initial = await client.artworkCacheScope()
        XCTAssertNotNil(initial)
        let restored = APIClient(transport: transport, store: store)
        _ = try await restored.restore()
        let scope = await restored.artworkCacheScope()
        XCTAssertEqual(scope, initial)
        await restored.logout()
        let signedOut = await restored.artworkCacheScope()
        XCTAssertNil(signedOut)
    }
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
        // Restore is offline-safe; routing is verified on the first actual request.
        _ = try await restored.catalog(query: "")
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
    func testHomeTransportFailureRetriesReadButNeverMutation() async throws {
        for mutation in [false, true] {
            let transport = ProofTransport()
            let client = APIClient(transport: transport, store: MemoryStore())
            try await client.login(endpoint: "https://public.test", homeEndpoint: "https://home.test", username: "test", password: "test", deviceName: "test")
            await transport.failAuthenticatedHome()
            if mutation {
                do { _ = try await client.setFavorite(1, liked: true); XCTFail("Expected ambiguous failure") } catch {}
                let hosts = await transport.credentialHosts
                XCTAssertEqual(hosts, ["home.test", "home.test"])
            } else {
                _ = try await client.catalog(query: "")
                let hosts = await transport.credentialHosts
                XCTAssertEqual(hosts, ["home.test", "home.test", "public.test"])
            }
        }
    }
    func testUnavailableHomeUsesPublicAndCoolsDownLocalProbes() async throws {
        let transport = ProofTransport()
        await transport.setHomeOffline(true)
        let router = ServerRouter(transport: transport)
        let publicAddress = try Endpoint("https://public.test")
        await router.configure(primary: publicAddress, home: try Endpoint("https://home.test"), id: "test", key: transport.key.publicKey.derRepresentation.base64EncodedString())
        let first = try await router.selected()
        XCTAssertEqual(first.url.host, "public.test")
        await router.failed(publicAddress)
        _ = try await router.selected()
        let probes = await transport.homeProofs
        XCTAssertEqual(probes, 1, "Unavailable LAN probes should not delay every read")
        await transport.setHomeOffline(false)
        await router.invalidate()
        let recovered = try await router.selected()
        XCTAssertEqual(recovered.url.host, "home.test")
    }
    func testOfflineRelaunchRecoversThroughPublicWithoutLosingSession() async throws {
        let transport = ProofTransport(), store = MemoryStore()
        let first = APIClient(transport: transport, store: store)
        try await first.login(endpoint: "https://public.test", homeEndpoint: "https://home.test", username: "test", password: "test", deviceName: "test")
        await transport.setHomeOffline(true); await transport.setPublicOffline(true)
        let restored = APIClient(transport: transport, store: store)
        _ = try await restored.restore()
        do { _ = try await restored.catalog(query: ""); XCTFail("Expected both addresses unavailable") } catch {}
        let saved = try await store.load(); XCTAssertNotNil(saved)
        await transport.setPublicOffline(false)
        await restored.networkChanged()
        _ = try await restored.catalog(query: "")
        let status = await restored.connection()
        XCTAssertTrue(status.connected); XCTAssertFalse(status.home)
    }
    func testRefreshFailureOnHomeRecoversOnPublicWithSameAttemptID() async throws {
        let transport = ProofTransport(), store = MemoryStore()
        let first = APIClient(transport: transport, store: store)
        try await first.login(endpoint: "https://public.test", homeEndpoint: "https://home.test", username: "test", password: "test", deviceName: "test")
        let stored = try await store.load()
        let saved = try XCTUnwrap(stored)
        await store.save(Credential(endpoint: saved.endpoint, serverId: saved.serverId, tokens: saved.tokens, now: Date().addingTimeInterval(-1000), homeEndpoint: saved.homeEndpoint, serverPublicKey: saved.serverPublicKey))
        let restored = APIClient(transport: transport, store: store)
        _ = try await restored.restore()
        await transport.failAuthenticatedHome()
        do { _ = try await restored.catalog(query: ""); XCTFail("Expected interrupted refresh") } catch {}
        do { _ = try await restored.catalog(query: ""); XCTFail("Expected backoff") }
        catch APIError.retryDeferred(let until) { XCTAssertGreaterThan(until, Date()) }
        let attemptsBefore = await transport.refreshAttempts
        XCTAssertEqual(attemptsBefore.count, 1)
        try await Task.sleep(for: .seconds(2.6))
        _ = try await restored.catalog(query: "")
        let attempts = await transport.refreshAttempts
        XCTAssertEqual(attempts.map { $0.0 }, ["home.test", "public.test"])
        XCTAssertEqual(attempts[0].1, attempts[1].1, "Lost responses must retain the durable attempt identifier across origins")
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
    private var failHome = false
    private var homeOffline = false
    private var proofFailure: Int?
    private var artworkFailure = false
    func setProofFailure(_ status: Int?) { proofFailure = status }
    func setArtworkFailure() { artworkFailure = true }
    var homeProofs = 0
    var publicProofs = 0
    var refreshAttempts: [(String, String)] = []
    func setHomeOffline(_ offline: Bool) { homeOffline = offline }
    func setPublicOffline(_ offline: Bool) { publicOffline = offline }
    func failAuthenticatedHome() { failHome = true }
    var credentialHosts: [String] = []
    init(wrongHome: Bool = false, replayHome: Bool = false) { self.wrongHome = wrongHome; self.replayHome = replayHome }
    func disablePublic() { publicOffline = true }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let host = request.url!.host!
        if host == "home.test", request.url!.path.hasSuffix("server-proof") { homeProofs += 1 }
        if homeOffline, host == "home.test" { throw URLError(.cannotConnectToHost) }
        if publicOffline, host == "public.test" { throw URLError(.notConnectedToInternet) }
        if request.url!.path.hasSuffix("capabilities") {
            return try result(["server_id": "test", "server_public_key": key.publicKey.derRepresentation.base64EncodedString(), "features": ["refresh_recovery_v1", "rotating_refresh", "native_media_manifest_v1", "native_catalog_v1", "native_playback_v1"]])
        }
        if request.url!.path.hasSuffix("server-proof") {
            if host == "public.test" { publicProofs += 1 }
            XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"))
            if let proofFailure { return HTTPResult(status:proofFailure,data:Data("<html>temporarily unavailable</html>".utf8)) }
            let input = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: String])
            let nonce = host == "home.test" && replayHome ? String(repeating: "0", count: 64) : input["nonce"]!
            let signer = host == "home.test" && wrongHome ? P256.Signing.PrivateKey() : key
            let signature = try signer.signature(for: Data("arrify-server-proof-v1\ntest\n\(nonce)".utf8))
            return try result(["server_id": "test", "nonce": nonce, "signature": signature.derRepresentation.base64EncodedString()])
        }
        credentialHosts.append(host)
        if artworkFailure, request.url!.path.hasPrefix("/api/v1/artwork/") { throw URLError(.timedOut) }
        if request.url!.path.hasSuffix("refresh") {
            let body = try XCTUnwrap(JSONSerialization.jsonObject(with: request.httpBody!) as? [String: String])
            refreshAttempts.append((host, body["attempt_id"]!))
        }
        if failHome, host == "home.test" { throw URLError(.networkConnectionLost) }
        if request.url!.path.hasSuffix("catalog") { return try result(["revision": "v1", "items": []]) }
        return try result(["access_token": "access", "refresh_token": "refresh", "expires_in": 900, "refresh_expires_in": 9000, "session_id": "session"])
    }
    func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws {}
    private func result(_ value: [String: Any]) throws -> HTTPResult { HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: value)) }
}
