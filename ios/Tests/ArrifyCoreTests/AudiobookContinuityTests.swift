import XCTest
@testable import ArrifyCore

@MainActor final class AudiobookContinuityTests: XCTestCase {
    func testConcurrentSyncCallersWaitForTheSameWork() async {
        let gate = AudiobookSyncGate(), barrier = BookSyncBarrier()
        var starts = 0, completed = 0
        let first = Task { await gate.run { starts += 1; await barrier.wait() }; completed += 1 }
        await barrier.entered()
        let second = Task { await gate.run { starts += 1 }; completed += 1 }
        await Task.yield()
        XCTAssertEqual(starts, 1); XCTAssertEqual(completed, 0)
        barrier.release(); await first.value; await second.value
        XCTAssertEqual(starts, 1); XCTAssertEqual(completed, 2)
    }
    func testOldAccountCompletionCannotReleaseNewAccountWork() async {
        let gate = AudiobookSyncGate(), old = BookSyncBarrier(), current = BookSyncBarrier()
        let first = Task { await gate.run { await old.wait() } }; await old.entered()
        gate.reset()
        let second = Task { await gate.run { await current.wait() } }; await current.entered()
        old.release(); await first.value
        var unexpected = false, joinedFinished = false
        let joined = Task { await gate.run { unexpected = true }; joinedFinished = true }
        await Task.yield()
        XCTAssertFalse(unexpected); XCTAssertFalse(joinedFinished)
        current.release(); await second.value; await joined.value
        XCTAssertFalse(unexpected); XCTAssertTrue(joinedFinished)
    }
    func testLatestAcknowledgedRemoteRewindWinsOverOlderDownloadPosition() {
        let remote = AudiobookProgress(currentTime:15, lastUpdate:200, isFinished:false)
        XCTAssertEqual(AudiobookResume.position(remote:remote, local:900, localStamp:100, pending:nil, restart:false, finished:false),15)
        XCTAssertEqual(AudiobookResume.position(remote:remote, local:900, localStamp:300, pending:nil, restart:false, finished:false),900)
        XCTAssertEqual(AudiobookResume.position(remote:remote, local:900, localStamp:300, pending:nil, restart:true, finished:false),0)
    }
    func testErrorCategoriesDoNotTurnServerAndDecodeFailuresIntoOffline() {
        XCTAssertEqual(AudiobookPlaybackIssue.classify(APIError.rejected(status:503, code:"audiobooks_unavailable", message:"unavailable")), .temporary)
        XCTAssertEqual(AudiobookPlaybackIssue.classify(URLError(.notConnectedToInternet)), .offline)
        XCTAssertEqual(AudiobookPlaybackIssue.classify(APIError.rejected(status:404, code:"missing", message:"missing")), .missing)
        XCTAssertEqual(AudiobookPlaybackIssue.classify(APIError.signInRequired), .authentication)
        XCTAssertEqual(AudiobookPlaybackIssue.classify(APIError.rejected(status:409, code:"conflict", message:"Keep your place")), .conflict("Keep your place"))
        XCTAssertEqual(AudiobookPlaybackIssue.classify(DecodingError.dataCorrupted(.init(codingPath:[],debugDescription:"fixture"))), .invalidResponse)
    }
    func testStructuredMetadataKeepsFractionalSeriesAndUnknownDates() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let data = Data(#"{"id":"book","library_id":"books","title":"Story","author":"Writer","narrator":"Reader","series":"Stories","description":"","cover":"","duration":100,"authors":[{"id":"author","name":"Writer"}],"series_entries":[{"id":"series","name":"Stories","sequence":"2.5"}],"published_year":"2024"}"#.utf8)
        let book = try decoder.decode(Audiobook.self,from:data)
        XCTAssertEqual(book.seriesEntries?.first?.label,"Book 2.5 · Stories")
        XCTAssertEqual(book.authors?.first?.id,"author"); XCTAssertNil(book.publishedDate)
        XCTAssertEqual(book.publishedYear,"2024")
    }
}
@MainActor private final class BookSyncBarrier {
    private var work: CheckedContinuation<Void,Never>?
    private var observer: CheckedContinuation<Void,Never>?
    private var started = false
    func wait() async { await withCheckedContinuation { work = $0; started = true; observer?.resume(); observer = nil } }
    func entered() async { if !started { await withCheckedContinuation { observer = $0 } } }
    func release() { work?.resume(); work = nil }
}
