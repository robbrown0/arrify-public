import XCTest
@testable import ArrifyCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class AudiobookTests: XCTestCase {
    func testCombinedReadingOrderKeepsPositionsDistinctFromOfficialBookNumbers() throws {
        let decoder = JSONDecoder()
        func series(_ name: String, _ sequence: String?) throws -> AudiobookSeries {
            var payload: [String:Any] = ["id":"series", "name":name]
            payload["sequence"] = sequence
            return try decoder.decode(AudiobookSeries.self, from: JSONSerialization.data(withJSONObject:payload))
        }
        let main = try series("Expeditionary Force", "10")
        let universe = try series("Expeditionary Force Universe Reading Order", "14")
        XCTAssertEqual(main.label, "Book 10 · Expeditionary Force")
        XCTAssertEqual(universe.label, "Reading position 14 · Expeditionary Force Universe Reading Order")
        XCTAssertEqual(try series("Expeditionary Force: Mavericks", "1").label, "Book 1 · Expeditionary Force: Mavericks")
        XCTAssertEqual(try series("Expeditionary Force", "3.5").label, "Book 3.5 · Expeditionary Force")
        XCTAssertEqual(try series("Expeditionary Force: Listening Order", "4").label,
                       "Reading position 4 · Expeditionary Force: Listening Order")
        XCTAssertEqual(try series("Universe Reading Order", nil).label, "Universe Reading Order")
    }
    func testUnavailableBookProviderDoesNotInvalidateLoginOrMusicServerRouting() async throws {
        let store = MemoryStore(); let api = APIClient(transport:BookProviderFailureTransport(), store:store)
        try await api.login(endpoint:"https://example.com", username:"test", password:"test", deviceName:"test")
        do { _ = try await api.audiobookLibrary(); XCTFail("Expected an optional provider failure") } catch {}
        let connection = await api.connection()
        XCTAssertTrue(connection.connected); XCTAssertFalse(connection.degraded)
        let saved = try await store.load(); XCTAssertNotNil(saved)
        _ = try await api.catalog(query:"")
    }
    func testLongBooksRetainHoursAndChapterPartBoundaries() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let book = try decoder.decode(Audiobook.self, from:Data(#"{"id":"book","library_id":"books","title":"Long book","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book","duration":100000,"source_revision":"revision","parts":[{"index":0,"start":0,"duration":50000},{"index":1,"start":50000,"duration":50000}],"chapters":[{"title":"One","start":0,"end":50000},{"title":"Two","start":50000,"end":100000}],"progress":{"currentTime":90000,"lastUpdate":123,"isFinished":false}}"#.utf8))
        XCTAssertEqual(book.remaining, 10000)
        XCTAssertEqual(AudiobookTimeline.time(90000), "25:00:00")
        XCTAssertEqual(AudiobookTimeline.chapter(book.chapters!, at:50000)?.title, "Two")
        XCTAssertEqual(AudiobookTimeline.part(book.parts!, at:50000)?.index, 1)
        var journal = AudiobookCheckpoint(book:book, deviceId:"device")
        journal.seq = 2; journal.position = 20000 // An intentional rewind is preserved.
        let restored = try JSONDecoder().decode(AudiobookCheckpoint.self, from:JSONEncoder().encode(journal))
        XCTAssertEqual(restored.position, 20000); XCTAssertEqual(restored.seq, 2)
        XCTAssertEqual(restored.baseRemoteStamp, 123); XCTAssertEqual(restored.sessionId, journal.sessionId)
        var entry = AudiobookJournalEntry(checkpoint:restored, lease:"first", playing:false, dirty:true)
        XCTAssertFalse(entry.acknowledge(session:journal.sessionId, sequence:1, remoteStamp:124))
        XCTAssertTrue(entry.dirty); XCTAssertEqual(entry.checkpoint.position, 20000)
        XCTAssertEqual(entry.checkpoint.seq, 2)
        XCTAssertTrue(entry.acknowledge(session:journal.sessionId, sequence:2, remoteStamp:125))
        XCTAssertEqual(entry.checkpoint.seq, 2, "Acknowledgement must never reset the monotonic sequence")
        XCTAssertFalse(entry.acknowledge(session:"a-new-opened-session", sequence:2, remoteStamp:999))
        XCTAssertEqual(entry.checkpoint.baseRemoteStamp, 125)
    }
    func testAudiobookCapabilitiesCannotBecomeArbitraryAuthenticatedURLs() throws {
        let endpoint = try Endpoint("https://arrify.example")
        let grant = String(repeating:"a", count:43)
        XCTAssertEqual(try endpoint.audiobookURL("/api/v1/audiobooks/stream/" + grant + "/12").host, "arrify.example")
        for path in ["https://evil.example/api/v1/audiobooks/stream/"+grant+"/0", "/api/v1/audiobooks/stream/"+grant+"/0?token=secret", "/api/v1/audiobooks/stream/"+grant+"/../admin", "/api/v1/audiobooks/cover/book", "/api/v1/audiobooks/stream/short/0"] {
            XCTAssertThrowsError(try endpoint.audiobookURL(path))
        }
    }
    func testMusicManifestDoesNotAcceptBookGrants() throws {
        let endpoint = try Endpoint("https://arrify.example")
        XCTAssertThrowsError(try endpoint.mediaURL("/api/v1/audiobooks/stream/" + String(repeating:"a", count:43) + "/0"))
    }
}

private actor BookProviderFailureTransport: HTTPTransport {
    let fallback = FakeTransport()
    func send(_ request: URLRequest) async throws -> HTTPResult {
        if request.url?.path.hasPrefix("/api/v1/audiobooks/") == true {
            return HTTPResult(status:503, data:Data(#"{"error":{"code":"audiobooks_unavailable","message":"Your book library is reconnecting."}}"#.utf8))
        }
        return try await fallback.send(request)
    }
}
