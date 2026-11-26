import XCTest
@testable import ArrifyCore

final class AudiobookTests: XCTestCase {
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
