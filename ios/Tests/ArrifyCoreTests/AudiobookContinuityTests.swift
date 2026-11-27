import XCTest
@testable import ArrifyCore

@MainActor final class AudiobookContinuityTests: XCTestCase {
    func testDownloadedResumeDeadlineDoesNotWaitForAnUncooperativeRead() async {
        let read = BookSyncBarrier(), timer = BookSyncBarrier()
        let task = Task {
            do {
                let _: Int = try await AudiobookReadDeadline.load(wait:{ _ in await timer.wait() }) { await read.wait(); return 7 }
                XCTFail("The deadline must win")
            } catch { XCTAssertEqual((error as? URLError)?.code,.timedOut) }
        }
        await read.entered(); await timer.entered(); timer.release()
        await task.value
        read.release()
    }
    func testDownloadedResumeReadCanWinAndCancellationDoesNotWaitForReads() async throws {
        let timer = BookSyncBarrier()
        let result = try await AudiobookReadDeadline.load(wait:{ _ in await timer.wait() }) { 7 }
        XCTAssertEqual(result,7)
        await timer.entered(); timer.release()
        let read = BookSyncBarrier(), cancelledTimer = BookSyncBarrier()
        let task = Task {
            do {
                let _: Int = try await AudiobookReadDeadline.load(wait:{ _ in await cancelledTimer.wait() }) { await read.wait(); return 8 }
                XCTFail("Cancellation must win")
            } catch { XCTAssertTrue(error is CancellationError) }
        }
        await read.entered(); await cancelledTimer.entered(); task.cancel(); await task.value
        read.release(); cancelledTimer.release()
    }
    func testUnchangedPausedCheckpointDoesNotCreateNewHistoryOrClearPendingHistory() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let book = try decoder.decode(Audiobook.self,from:Data(#"{"id":"book","library_id":"books","title":"Story","author":"Writer","narrator":"Reader","series":"","description":"","cover":"","duration":100}"#.utf8))
        var checkpoint = AudiobookCheckpoint(book:book,deviceId:"device")
        var entry = AudiobookJournalEntry(checkpoint:checkpoint,lease:nil,playing:false,dirty:false)
        XCTAssertTrue(entry.needsCheckpoint(position:0,playing:false,finished:false),"A new listening session must send its first intent")
        checkpoint.seq = 1; checkpoint.position = 30
        entry = AudiobookJournalEntry(checkpoint:checkpoint,lease:nil,playing:false,dirty:false)
        XCTAssertFalse(entry.needsCheckpoint(position:30,playing:false,finished:false))
        XCTAssertTrue(entry.needsCheckpoint(position:15,playing:false,finished:false))
        XCTAssertTrue(entry.needsCheckpoint(position:30,playing:true,finished:false))
        XCTAssertTrue(entry.needsCheckpoint(position:30,playing:false,finished:true))
        entry.dirty = true
        XCTAssertFalse(entry.needsCheckpoint(position:30,playing:false,finished:false))
        XCTAssertTrue(entry.dirty,"No-op checkpoints must retain an earlier unsynced write")
    }
    func testConcurrentSyncCallersWaitForTheSameWork() async {
        let gate = AudiobookSyncGate(), barrier = BookSyncBarrier()
        var starts = 0, completed = 0
        let first = Task { await gate.run { starts += 1; await barrier.wait() }; completed += 1 }
        await barrier.entered()
        var secondEntered = false
        let second = Task { secondEntered = true; await gate.run { starts += 1 }; completed += 1 }
        while !secondEntered { await Task.yield() }
        XCTAssertEqual(starts, 1); XCTAssertEqual(completed, 0)
        barrier.release(); await first.value; await second.value
        XCTAssertEqual(starts, 1); XCTAssertEqual(completed, 2)
    }
    func testOldAccountCompletionCannotReleaseNewAccountWork() async {
        let gate = AudiobookSyncGate(), old = BookSyncBarrier(), current = BookSyncBarrier()
        let first = Task { await gate.run { await old.wait() } }; await old.entered()
        gate.reset()
        let second = Task { await gate.run { await current.wait() } }; await current.entered()
        old.release(); _ = await first.value
        var unexpected = false, joinedFinished = false, joinedEntered = false
        let joined = Task { joinedEntered = true; await gate.run { unexpected = true }; joinedFinished = true }
        while !joinedEntered { await Task.yield() }
        XCTAssertFalse(unexpected); XCTAssertFalse(joinedFinished)
        current.release(); _ = await second.value; await joined.value
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
