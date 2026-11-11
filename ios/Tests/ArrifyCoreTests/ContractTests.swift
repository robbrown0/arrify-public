import XCTest
@testable import ArrifyCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class ContractTests: XCTestCase {
    func testBrowseChartHandlesRequestedObjectsAndMissingLocalSongs() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let chart = try decoder.decode(BrowseChart.self, from: Data(#"{"items":[{"title":"Song","artist":"Artist","rank":1,"request_key":"key","requested":{"id":42},"track":null}],"pending":false,"stale":true}"#.utf8))
        XCTAssertTrue(chart.items[0].requested)
        XCTAssertNil(chart.items[0].track)
        XCTAssertTrue(chart.stale)
        let filters = BrowseFilters(genre: "r&b", decade: "1990", kind: "songs")
        let decoded = try JSONDecoder().decode(BrowseFilters.self, from: Data(filters.source.id.utf8))
        XCTAssertEqual(decoded, filters)
    }

    func testTrackPopularityAndNavigationRemainBackwardCompatible() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let legacy = try decoder.decode(Track.self, from: Data(#"{"id":1,"title":"Song","artist":"Artist","album":"Album"}"#.utf8))
        XCTAssertNil(legacy.popularity)
        let enriched = try decoder.decode(Track.self, from: Data(#"{"id":1,"title":"Song","artist":"Artist","album":"Album","artist_id":"artist","album_id":"album","popularity":{"doubloons":2,"rising":true,"evidence":[{"source":"Chart","url":"https://example.com"}]}}"#.utf8))
        XCTAssertEqual(enriched.popularity?.doubloons, 2)
        XCTAssertEqual(enriched.artistId, "artist")
        XCTAssertEqual(enriched.albumId, "album")
    }

    func testEndpointRejectsCredentialAndCrossOriginPaths() throws {
        for address in ["http://home.local", "https://user:pass@example.com", "https://example.com/path", "https://example.com?token=secret"] {
            XCTAssertThrowsError(try Endpoint(address))
        }
        let endpoint = try Endpoint("https://arrify-home.robnets.com")
        XCTAssertEqual(try endpoint.mediaURL("/api/v1/stream/12?native_grant=test").host, "arrify-home.robnets.com")
        for path in ["https://evil.test/api/v1/stream/1", "//evil.test/api/v1/stream/1", "/api/v1/native/sessions"] {
            XCTAssertThrowsError(try endpoint.mediaURL(path))
        }
    }
    func testManifestToleratesFutureStatesAndFields() throws {
        let data = Data(#"{"owner_epoch":3,"queue_revision":8,"retry_after_seconds":2,"future":true,"items":[{"track_id":5,"profile":"original","state":"new-state"}]}"#.utf8)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: data)
        XCTAssertEqual(manifest.items.first?.trackId, 5)
        XCTAssertEqual(manifest.items.first?.state, "new-state")
    }
    func testConcurrentRefreshIsSingleFlight() async throws {
        let transport = FakeTransport()
        let store = MemoryStore()
        let client = APIClient(transport: transport, store: store)
        try await client.login(endpoint: "https://example.com", username: "test", password: "test", deviceName: "test")
        async let one = client.catalog(query: "")
        async let two = client.catalog(query: "Pink")
        _ = try await (one, two)
        let count = await transport.refreshes
        XCTAssertEqual(count, 1)
        let saved = try await store.load()
        XCTAssertEqual(saved?.tokens.refreshToken, "rotated")
    }
    func testFailedRefreshRequiresSignInWithoutReplay() async throws {
        let transport = FakeTransport(failRefresh: true)
        let client = APIClient(transport: transport, store: MemoryStore())
        try await client.login(endpoint: "https://example.com", username: "test", password: "test", deviceName: "test")
        do { _ = try await client.catalog(query: ""); XCTFail("Expected refresh failure") } catch {}
        do { _ = try await client.catalog(query: ""); XCTFail("Expected sign-in requirement") } catch {}
        let count = await transport.refreshes
        XCTAssertEqual(count, 1)
    }
}
actor MemoryStore: CredentialStore {
    var value: Credential?
    func load() throws -> Credential? { value }
    func save(_ credential: Credential) { value = credential }
    func clear() { value = nil }
}
actor FakeTransport: HTTPTransport {
    var refreshes = 0
    let failRefresh: Bool
    init(failRefresh: Bool = false) { self.failRefresh = failRefresh }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("capabilities") {
            return result(#"{"server_id":"test","features":["rotating_refresh","native_media_manifest_v1","native_catalog_v1","native_playback_v1"]}"#)
        }
        if path.hasSuffix("sessions") {
            return result(#"{"access_token":"first","refresh_token":"first","expires_in":1,"refresh_expires_in":1000,"session_id":"test"}"#)
        }
        if path.hasSuffix("refresh") {
            refreshes += 1
            try await Task.sleep(for: .milliseconds(40))
            if failRefresh { throw URLError(.networkConnectionLost) }
            return result(#"{"access_token":"rotated","refresh_token":"rotated","expires_in":900,"refresh_expires_in":1000,"session_id":"test"}"#)
        }
        return result(#"{"revision":"v1","items":[],"next_offset":null}"#)
    }
    private func result(_ text: String) -> HTTPResult { HTTPResult(status: 200, data: Data(text.utf8)) }
}

extension ContractTests {
    private func snapshot(epoch: Int = 2, revision: Int = 4, intent: Int = 3, time: Double = 100, partial: Bool = false, upcoming: [Int]? = [8, 9]) -> PlaybackState {
        PlaybackState(partial: partial, currentTrackId: 7, positionSeconds: 20, serverTime: time,
                      queueRevision: revision, ownerDeviceId: "device", ownerEpoch: epoch, intent: intent,
                      playing: true, observed: "playing", seekTargetSeconds: 0, seekIntent: 0, playId: 1,
                      upcomingTrackIds: upcoming)
    }
    func testPartialHeartbeatPreservesUpcomingQueue() {
        let merged = PlaybackState.merging(snapshot(time: 101, partial: true, upcoming: nil), into: snapshot())
        XCTAssertEqual(merged.upcomingTrackIds, [8, 9])
    }
    func testStaleSnapshotCannotRestoreOldOwnerOrIntent() {
        let current = snapshot()
        for stale in [snapshot(epoch: 1), snapshot(revision: 3), snapshot(intent: 2), snapshot(time: 99)] {
            let merged = PlaybackState.merging(stale, into: current)
            XCTAssertEqual(merged.ownerEpoch, 2)
            XCTAssertEqual(merged.queueRevision, 4)
            XCTAssertEqual(merged.intent, 3)
            XCTAssertEqual(merged.serverTime, 100)
        }
    }
    func testLogoutDuringRefreshCannotResurrectCredentials() async throws {
        let transport = FakeTransport()
        let store = MemoryStore()
        let client = APIClient(transport: transport, store: store)
        try await client.login(endpoint: "https://example.com", username: "test", password: "test", deviceName: "test")
        let work = Task { try await client.catalog(query: "") }
        while await transport.refreshes == 0 { await Task.yield() }
        await client.logout()
        _ = try? await work.value
        let saved = try await store.load()
        XCTAssertNil(saved)
    }
}


extension ContractTests {
    func testListeningInvalidationsIgnoreKeepalivesAndDuplicateVersions() {
        var parser = ListeningEventParser()
        XCTAssertFalse(parser.line("retry: 2000")); XCTAssertFalse(parser.line(""))
        XCTAssertFalse(parser.line("event: session")); XCTAssertFalse(parser.line("data: 1"))
        XCTAssertTrue(parser.line(""))
        XCTAssertFalse(parser.line(": keepalive")); XCTAssertFalse(parser.line(""))
        XCTAssertFalse(parser.line("event: session")); XCTAssertFalse(parser.line("data: 1"))
        XCTAssertFalse(parser.line(""))
        XCTAssertFalse(parser.line("event: session")); XCTAssertFalse(parser.line("data: 2"))
        XCTAssertTrue(parser.line(""))
        XCTAssertFalse(parser.line("event: other")); XCTAssertFalse(parser.line("data: 3"))
        XCTAssertFalse(parser.line(""))
    }
    func testLiveUpdatesUseBearerHeaderAndRenewBeforeConnecting() async throws {
        let transport = EventTransport()
        let api = APIClient(transport: transport, store: MemoryStore())
        try await api.login(endpoint: "https://example.com", username: "test", password: "test", deviceName: "test")
        try await api.listeningEvents(changed: {})
        let request = await transport.eventRequest
        XCTAssertEqual(request?.url?.path, "/api/v1/listening/events")
        XCTAssertNil(request?.url?.query)
        XCTAssertEqual(request?.value(forHTTPHeaderField: "Authorization"), "Bearer rotated")
        XCTAssertEqual(request?.value(forHTTPHeaderField: "Accept"), "text/event-stream")
    }
}
private actor EventTransport: HTTPTransport {
    let base = FakeTransport()
    var eventRequest: URLRequest?
    func send(_ request: URLRequest) async throws -> HTTPResult { try await base.send(request) }
    func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws {
        eventRequest = request; await changed()
    }
}

extension ContractTests {
    func testDeletionUsesPasswordInBodyAndOnlyCurrentAuthenticatedAccount() async throws {
        let transport = DeletionTransport()
        let api = APIClient(transport: transport, store: MemoryStore())
        try await api.login(endpoint: "https://example.com", username: "test", password: "test", deviceName: "test")
        _ = try await api.deleteAccount(password: "current-secret")
        let request = await transport.deletion!
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertNil(request.url?.query)
        XCTAssertNotNil(request.value(forHTTPHeaderField: "Authorization"))
        let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
        XCTAssertEqual(Set(body.keys), Set(["password", "confirm"]))
        XCTAssertEqual(body["password"] as? String, "current-secret")
        XCTAssertEqual(body["confirm"] as? Bool, true)
        _ = try await api.deleteAccount(endpoint: "https://example.com", username: "pending", password: "pending-secret")
        let pending = await transport.deletion!
        XCTAssertNil(pending.value(forHTTPHeaderField: "Authorization"))
        let pendingBody = try JSONSerialization.jsonObject(with: pending.httpBody!) as! [String: Any]
        XCTAssertEqual(pendingBody["username"] as? String, "pending")
        XCTAssertEqual(Set(pendingBody.keys), Set(["username", "password", "confirm"]))
    }
}
private actor DeletionTransport: HTTPTransport {
    let base = FakeTransport()
    var deletion: URLRequest?
    func send(_ request: URLRequest) async throws -> HTTPResult {
        if request.url?.path == "/api/v1/native/account/delete" {
            deletion = request
            return HTTPResult(status: 200, data: Data(#"{"message":"Account deleted."}"#.utf8))
        }
        return try await base.send(request)
    }
}
