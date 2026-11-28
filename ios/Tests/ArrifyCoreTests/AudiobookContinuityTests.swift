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
    func testJoinedSyncCanImmediatelyDrainNewWorkAfterTheOwnerIsCancelled() async {
        // Pause can cancel the original caller while its shared request is in
        // flight. The higher-priority joiner must get a fresh slot afterward,
        // even when it resumes before that cancelled caller does.
        for _ in 0..<50 {
            let gate = AudiobookSyncGate(), barrier = BookSyncBarrier()
            let owner = Task(priority:.background) { await gate.run { await barrier.wait() } }
            await barrier.entered()
            var joined = false, followups = 0
            let follower = Task(priority:.userInitiated) {
                joined = true
                let duplicated = await gate.run { XCTFail("Joined work must remain single-flight") }
                XCTAssertFalse(duplicated)
                return await gate.run { followups += 1 }
            }
            while !joined { await Task.yield() }
            owner.cancel(); barrier.release()
            let drained = await follower.value; _ = await owner.value
            XCTAssertTrue(drained,"A completed request cannot occupy the next save's slot")
            XCTAssertEqual(followups,1)
        }
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
    func testShelfStatesTreatUnreadAndFinishedConsistently() {
        XCTAssertEqual(AudiobookReadingState(nil),.unstarted)
        XCTAssertEqual(AudiobookReadingState(.init(currentTime:0,lastUpdate:10,isFinished:false)),.unstarted)
        XCTAssertEqual(AudiobookReadingState(.init(currentTime:30,lastUpdate:10,isFinished:false)),.started)
        XCTAssertEqual(AudiobookReadingState(.init(currentTime:0,lastUpdate:10,isFinished:true)),.finished)
        XCTAssertEqual(AudiobookReadingState(.init(currentTime:.nan,lastUpdate:10,isFinished:false)),.unstarted)
        XCTAssertFalse(AudiobookReadingState.unstarted.matches("started"))
        XCTAssertTrue(AudiobookReadingState.unstarted.matches("unstarted"))
        XCTAssertTrue(AudiobookReadingState.finished.matches("all"))
    }
    func testMediaIdentityAllowsMetadataCorrectionsButNeverDifferentAudioOrBooks() throws {
        let original = try identityBook(source:"old-metadata",audio:"same-audio")
        let corrected = try identityBook(source:"new-metadata",audio:"same-audio")
        XCTAssertTrue(AudiobookEdition.matches(original,corrected))
        XCTAssertTrue(AudiobookEdition.matches(AudiobookCheckpoint(book:original,deviceId:"device"),corrected))
        XCTAssertFalse(AudiobookEdition.matches(original,try identityBook(source:"old-metadata",audio:"replacement-audio")))
        XCTAssertFalse(AudiobookEdition.matches(original,try identityBook(source:"old-metadata",audio:"same-audio",id:"different-book")))
        XCTAssertFalse(AudiobookEdition.matches(try identityBook(source:"old-metadata"),corrected),"Legacy metadata alone cannot prove that audio is unchanged")
        XCTAssertTrue(AudiobookEdition.matches(try identityBook(source:"old-metadata"),original),"Unchanged legacy downloads remain compatible")
    }
    func testOldProgressJournalsDecodeWithoutNewMediaIdentityOrStatusFields() throws {
        let data = Data(#"{"bookId":"book","sourceRevision":"legacy","sessionId":"session","deviceId":"device","baseRemoteStamp":4,"seq":1,"position":30,"finished":false}"#.utf8)
        let value = try JSONDecoder().decode(AudiobookCheckpoint.self,from:data)
        XCTAssertNil(value.audioRevision); XCTAssertNil(value.isStatusChange)
        XCTAssertEqual(value.position,30)
    }
    func testEditionConflictsHaveDistinctRecoveryFromPositionConflicts() {
        let edition = AudiobookPlaybackIssue.classify(APIError.rejected(status:409,code:"audiobook_edition_changed",message:"Fixture"))
        XCTAssertEqual(edition,.editionChanged); XCTAssertTrue(edition.isConflict)
        let legacy = AudiobookPlaybackIssue.classify(APIError.rejected(status:409,code:"audiobook_conflict",message:"This audiobook changed. Your offline place is saved; download the current edition before syncing it."))
        XCTAssertEqual(legacy,.editionChanged)
        let position = AudiobookPlaybackIssue.classify(APIError.rejected(status:409,code:"audiobook_conflict",message:"Your place changed on another device."))
        XCTAssertEqual(position,.conflict("Your place changed on another device."))
        XCTAssertEqual(AudiobookPlaybackIssue.classify(AudiobookPlaybackIssue.storage),.storage)
    }
    private func identityBook(source:String,audio:String? = nil,id:String = "book") throws -> Audiobook {
        var value: [String:Any] = ["id":id,"library_id":"books","title":"Story","author":"Writer","narrator":"Reader","series":"","description":"","cover":"","duration":120,"source_revision":source]
        value["audio_revision"] = audio
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(Audiobook.self,from:JSONSerialization.data(withJSONObject:value))
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
