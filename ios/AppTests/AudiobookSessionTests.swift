import XCTest
import ArrifyCore
@testable import Arrify

final class AudiobookSessionTests: XCTestCase {
    @MainActor private func pagingShelf(_ transport: BookPageTransport) async throws -> AudiobookShelfStore {
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        return AudiobookShelfStore(api:api,wait:{ _ in })
    }
    @MainActor func testBookPagingCoalescesRapidBoundaryRequestsAndStopsAtTheLastPage() async throws {
        let transport = BookPageTransport(), shelf = try await pagingShelf(transport)
        await shelf.load(); await transport.holdSecondPage()
        let next = Task { await shelf.loadNextPage(1) }
        for _ in 0..<100 { if await transport.waiting { break }; try await Task.sleep(for:.milliseconds(10)) }
        let waiting = await transport.waiting; XCTAssertTrue(waiting)
        await shelf.loadNextPage(1); await shelf.load(more:true)
        await transport.release(); await next.value
        XCTAssertEqual(shelf.items.map(\.id),["one","two","three"],"Overlapping page rows retain stable identities and order")
        await shelf.loadNextPage(1) // Old footer callback after the cursor moved.
        await shelf.loadNextPage(2); await shelf.loadNextPage(2)
        XCTAssertEqual(shelf.items.map(\.id),["one","two","three","four"])
        XCTAssertNil(shelf.next)
        let pages = await transport.pages; XCTAssertEqual(pages,[0,1,2])
    }
    @MainActor func testBookPageFailureKeepsLoadedRowsAndRetryUsesTheFailedPage() async throws {
        let transport = BookPageTransport(), shelf = try await pagingShelf(transport)
        await shelf.load(); await transport.failSecondPage()
        await shelf.loadNextPage(1)
        XCTAssertEqual(shelf.items.map(\.id),["one","two"])
        XCTAssertEqual(shelf.next,1); XCTAssertTrue(shelf.failedNextPage)
        let failedPages = await transport.pages
        await shelf.loadNextPage(1)
        let repeatedPages = await transport.pages
        XCTAssertEqual(repeatedPages,failedPages,"A stale footer callback must not bypass the failed-page Retry action")
        XCTAssertTrue(shelf.failedNextPage)
        await shelf.retry()
        XCTAssertEqual(shelf.items.map(\.id),["one","two","three"])
        XCTAssertFalse(shelf.failedNextPage); XCTAssertNil(shelf.message)
        let pages = await transport.pages; XCTAssertEqual(pages,[0,1,1,1,1,1])
    }
    @MainActor func testOldBookPageCannotAppendAfterChangingSearchOrSigningOut() async throws {
        for clear in [false,true] {
            let transport = BookPageTransport(), shelf = try await pagingShelf(transport)
            await shelf.load(); await transport.holdSecondPage()
            let old = Task { await shelf.loadNextPage(1) }
            for _ in 0..<100 { if await transport.waiting { break }; try await Task.sleep(for:.milliseconds(10)) }
            if clear { shelf.clear() } else { shelf.query = "New"; await shelf.load() }
            await transport.release(); await old.value
            XCTAssertEqual(shelf.items.map(\.id),clear ? [] : ["new-one","new-two"])
            XCTAssertFalse(shelf.loading); XCTAssertNil(shelf.message)
        }
    }
    @MainActor func testMultipartBookPlaysShortFinalChapterFromItsExactStart() async throws {
        let transport = BookSessionTransport(multipart:true)
        let api = APIClient(transport:transport, store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let files = [try UITestAudiobookAudio.make(seconds:1), try UITestAudiobookAudio.make(seconds:0.5)]
        let requests = BookPartRequests()
        let books = AudiobookStore(api:api, music:music, audioSession:BookSessionAudio(), localMedia:{ _,part in
            requests.indices.append(part.index); requests.positions.append(requests.store?.position ?? -1)
            return files[part.index]
        })
        requests.store = books
        defer { books.clear(); music.stop(); for file in files { try? FileManager.default.removeItem(at:file) } }
        await books.restore(); books.play(try await api.audiobook("book-one"))
        for _ in 0..<1000 {
            if books.message?.hasPrefix("You’ve finished") == true { break }
            try await Task.sleep(for:.milliseconds(20))
        }
        XCTAssertEqual(requests.indices,[0,1], "A short final file must play before the book is marked finished")
        XCTAssertEqual(try XCTUnwrap(requests.positions.last),1,accuracy:0.001,"Do not skip the chapter’s opening audio")
        XCTAssertTrue(books.message?.hasPrefix("You’ve finished") == true)
    }
    @MainActor func testEndOfChapterSleepDoesNotOpenTheNextAudioFile() async throws {
        let transport = BookSessionTransport(multipart:true)
        let api = APIClient(transport:transport, store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let audio = try UITestAudiobookAudio.make(seconds:1)
        let requests = BookPartRequests()
        let books = AudiobookStore(api:api, music:music, audioSession:BookSessionAudio(), localMedia:{ _,part in
            requests.indices.append(part.index)
            if part.index == 0 { requests.store?.setSleep("chapter") }
            return audio
        })
        requests.store = books
        defer { books.clear(); music.stop(); try? FileManager.default.removeItem(at:audio) }
        await books.restore(); books.play(try await api.audiobook("book-one"))
        for _ in 0..<1000 {
            if !requests.indices.isEmpty, books.sleepChoice == "off", !books.preparing { break }
            try await Task.sleep(for:.milliseconds(20))
        }
        XCTAssertEqual(books.sleepChoice,"off"); XCTAssertFalse(books.playing)
        XCTAssertEqual(requests.indices,[0]); XCTAssertEqual(books.position,1,accuracy:0.02)
    }
    @MainActor func testLateBookReadCannotRestartAfterSignOutOrMusicSelection() async throws {
        for signOut in [true, false] {
            let transport = BookSessionTransport()
            let api = APIClient(transport:transport, store:UITestStore())
            try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
            let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
            let books = AudiobookStore(api:api, music:music, audioSession:BookSessionAudio())
            await transport.holdBookRead()
            let pending = Task { await books.playQueued("book-one") }
            for _ in 0..<100 {
                if await transport.readWaiting() { break }
                try await Task.sleep(for:.milliseconds(20))
            }
            let waiting = await transport.readWaiting(); XCTAssertTrue(waiting)
            if signOut { books.clear() } else { books.stopForMusic() }
            await transport.releaseBookRead(); await pending.value
            XCTAssertFalse(books.active); XCTAssertFalse(books.preparing)
            XCTAssertNil(books.book, "A response from the earlier selection must be discarded")
            books.clear(); music.stop(); await api.logout()
        }
    }
    @MainActor func testSigningOutThenSigningInReactivatesBookAudio() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport, store:UITestStore())
        let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let audio = try UITestAudiobookAudio.make()
        defer { try? FileManager.default.removeItem(at:audio); music.stop() }
        let session = BookSessionAudio()
        let books = AudiobookStore(api:api, music:music, audioSession:session, localMedia:{ _,_ in audio })
        for expected in 1...2 {
            try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
            let book = try await api.audiobook("book-one")
            await books.restore()
            books.play(book)
            for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
            XCTAssertTrue(books.playing, books.message ?? "Book did not start")
            XCTAssertEqual(session.activations, expected)
            books.clear()
            XCTAssertFalse(books.active); XCTAssertFalse(books.playing)
            await api.logout()
        }
    }
    @MainActor func testExplicitServerPositionResolvesAnEarlierProgressConflict() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport, store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let file = try UITestAudiobookAudio.make()
        let books = AudiobookStore(api:api, music:music, audioSession:BookSessionAudio(), localMedia:{ _,_ in file })
        defer { books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        await books.restore(); let book = try await api.audiobook("book-one")
        books.play(book)
        for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.playing)
        books.pause(); await books.synchronize(force:true, bookID:book.id)
        await transport.conflictProgress()
        books.seek(30)
        for _ in 0..<500 where !books.conflict { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.conflict)
        books.play(book, useRemote:true)
        for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.playing, books.message ?? "Explicit server position must open a new session")
        XCTAssertFalse(books.conflict); XCTAssertLessThan(books.position,10)
    }
    @MainActor func testUnchangedPausedPositionDoesNotWriteOrConflictWithAnotherDevice() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let file = try UITestAudiobookAudio.make()
        let books = AudiobookStore(api:api,music:music,audioSession:BookSessionAudio(),localMedia:{ _,_ in file })
        let previousAutomatic = books.automaticDownloads; books.automaticDownloads = false
        defer { books.automaticDownloads = previousAutomatic; books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        await books.restore(); let book = try await api.audiobook("book-one")
        books.play(book,startingAt:20)
        for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.playing)
        books.receivePlaybackTime(0) // An old notice must not undo the completed seek.
        XCTAssertGreaterThanOrEqual(books.position,19.99)
        books.pause(); await books.synchronize(force:true,bookID:book.id)
        let frozen = books.position
        let before = await transport.progressRequests
        await transport.conflictProgress()
        for _ in 0..<10 { books.receivePlaybackTime(90); await Task.yield() }
        XCTAssertEqual(books.position,frozen,accuracy:0.0001)
        books.seek(books.position)
        for _ in 0..<500 where books.preparing { try await Task.sleep(for:.milliseconds(20)) }
        await books.synchronize(force:true,bookID:book.id)
        let after = await transport.progressRequests
        XCTAssertEqual(after,before)
        XCTAssertFalse(books.conflict)
        XCTAssertFalse(books.playing)
    }
    @MainActor func testFailedActivationShowsPlayAndOneTapRetries() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        await transport.owner(music.deviceId)
        let file = try UITestAudiobookAudio.make()
        let audio = FailingBookSessionAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,localMedia:{ _,_ in file })
        let previousAutomatic = books.automaticDownloads; books.automaticDownloads = false
        defer { books.automaticDownloads = previousAutomatic; books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        await books.restore(); books.play(try await api.audiobook("book-one"))
        for _ in 0..<1000 where books.message == nil || books.preparing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertEqual(audio.activations,1)
        XCTAssertFalse(books.showsPauseControl)
        XCTAssertEqual(books.playbackControlLabel,"Play audiobook")
        books.toggle()
        for _ in 0..<1000 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.playing,books.message ?? "One Play tap must retry the failed open")
        XCTAssertEqual(audio.activations,2)
        XCTAssertTrue(books.showsPauseControl)
        books.pause(); XCTAssertFalse(books.showsPauseControl)
    }
    @MainActor func testMarkUnreadResetsProgressWithoutStartingPlayback() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let audio = BookSessionAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio)
        defer { books.clear(); music.stop() }
        await books.restore()
        let original = try await api.audiobook("book-one")
        let result = try await books.markUnread(original)
        XCTAssertEqual(result.progress?.currentTime,0)
        XCTAssertEqual(result.progress?.isFinished,false)
        XCTAssertEqual(audio.activations,0)
        XCTAssertFalse(books.active)
        let resets = await transport.offlinePositions
        XCTAssertEqual(resets,[0])
    }
    @MainActor func testDelayedShelfReadCannotUndoAcknowledgedUnreadProgress() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let shelf = AudiobookShelfStore(api:api)
        await shelf.load()
        XCTAssertEqual(shelf.items.first?.progress?.currentTime,40)
        await transport.holdLibraryRead()
        let refresh = Task { await shelf.load() }
        await transport.waitForLibraryRead()
        var reset = try await api.audiobook("book-one")
        reset.progress = AudiobookProgress(currentTime:0,lastUpdate:1,isFinished:false)
        shelf.applyProgress(reset)
        await transport.releaseLibraryRead(); await refresh.value
        XCTAssertEqual(shelf.items.first?.progress?.currentTime,0)
        XCTAssertFalse(shelf.loading)
    }

    @MainActor func testUnreadStaysOutOfProgressAfterStaleReloadsAndFilterChanges() async throws {
        let transport = BookSessionTransport()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let shelf = AudiobookShelfStore(api:api)
        shelf.filter = "started"; await shelf.load()
        XCTAssertEqual(shelf.items.count,1)
        var reset = try await api.audiobook("book-one")
        reset.progress = AudiobookProgress(currentTime:0,lastUpdate:10,isFinished:false)
        shelf.applyProgress(reset)
        for filter in ["started","all","unstarted","finished","started"] {
            shelf.filter = filter; await shelf.load()
            if ["started","finished"].contains(filter) { XCTAssertTrue(shelf.items.isEmpty,filter) }
            else { XCTAssertEqual(shelf.items.first?.progress?.currentTime,0,filter) }
        }
        shelf.clear(); await shelf.load()
        XCTAssertEqual(shelf.items.first?.progress?.currentTime,40,"A new account must not inherit the last account’s overrides")
    }

    @MainActor func testMarkFinishedAndUnreadUpdateEveryShelfWithoutActivatingAudio() async throws {
        let transport = BookSessionTransport(), audio = BookSessionAudio()
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let books = AudiobookStore(api:api,music:music,audioSession:audio)
        defer { books.clear(); music.stop() }
        await books.restore(); books.shelf.filter = "started"; await books.shelf.load()
        let original = try await api.audiobook("book-one")
        let finished = try await books.markFinished(original)
        XCTAssertEqual(finished.readingState,.finished)
        await books.shelf.load(); XCTAssertTrue(books.shelf.items.isEmpty)
        books.shelf.filter = "finished"; await books.shelf.load()
        XCTAssertEqual(books.shelf.items.first?.readingState,.finished)
        _ = try await books.markUnread(finished)
        await books.shelf.load(); XCTAssertTrue(books.shelf.items.isEmpty)
        XCTAssertTrue(books.resumeBooks.isEmpty)
        XCTAssertEqual(audio.activations,0); XCTAssertFalse(books.active)
        let positions = await transport.offlinePositions
        XCTAssertEqual(positions,[120,0])
    }

    @MainActor func testEditionRecoveryHydratesChaptersAndLateReadCannotRestoreOldSelection() async throws {
        for signOut in [false,true] {
            let transport = BookSessionTransport()
            let api = APIClient(transport:transport,store:UITestStore())
            try await api.login(endpoint:"https://fixture.invalid",username:"fixture",password:"fixture",deviceName:"Test")
            let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession()), audio = BookSessionAudio()
            let books = AudiobookStore(api:api,music:music,audioSession:audio)
            let previous = books.automaticDownloads; books.automaticDownloads = false
            await books.restore()
            let original = try await api.audiobook("book-one")
            await transport.rejectEditionOnBegin(); books.play(original)
            for _ in 0..<500 where books.recoveryBook == nil { try await Task.sleep(for:.milliseconds(10)) }
            XCTAssertEqual(books.recovery,.edition)
            XCTAssertFalse(try XCTUnwrap(books.book).chapters?.isEmpty ?? true)
            XCTAssertEqual(audio.activations,0)
            await transport.holdBookRead()
            let late = Task { await books.refreshRecovery() }
            for _ in 0..<200 { if await transport.readWaiting() { break }; try await Task.sleep(for:.milliseconds(10)) }
            if signOut { books.clear() } else { books.stopForMusic() }
            await transport.releaseBookRead(); await late.value
            XCTAssertNil(books.book); XCTAssertFalse(books.active)
            books.automaticDownloads = previous; books.clear(); music.stop()
        }
    }

}

private actor BookPageTransport: HTTPTransport {
    private let fallback = UITestTransport()
    private var hold = false, failures = 0
    private var continuation: CheckedContinuation<Void,Never>?
    private(set) var waiting = false
    private(set) var pages: [Int] = []
    func holdSecondPage() { hold = true }
    func failSecondPage() { failures = 4 }
    func release() { hold = false; continuation?.resume(); continuation = nil }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        guard request.url?.path == "/api/v1/audiobooks/library" else { return try await fallback.send(request) }
        let values = URLComponents(url:request.url!,resolvingAgainstBaseURL:false)?.queryItems ?? []
        let page = Int(values.first { $0.name == "page" }?.value ?? "0") ?? 0
        let query = values.first { $0.name == "q" }?.value ?? ""
        pages.append(page)
        if page == 1 && hold { waiting = true; await withCheckedContinuation { continuation = $0 } }
        if page == 1 && failures > 0 { failures -= 1; throw URLError(.timedOut) }
        let ids = !query.isEmpty ? ["new-one","new-two"] : page == 0 ? ["one","two"] : page == 1 ? ["two","three"] : ["four"]
        let books = ids.map { ["id":$0,"library_id":"books","title":$0,"author":"Writer","narrator":"Reader","series":"","description":"","cover":"","duration":120] as [String:Any] }
        return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:["items":books,"libraries":[["id":"books","name":"Books"]],"library_id":"books","total":4,"next_page":page < 2 ? page+1 as Any : NSNull()]))
    }
}
@MainActor private final class BookPartRequests {
    weak var store: AudiobookStore?
    var indices: [Int] = []
    var positions: [Double] = []
}
@MainActor private final class BookSessionAudio: PlaybackAudioSession {
    private var active = false
    var activations = 0
    func activate() async throws { if !active { active = true; activations += 1 } }
    func deactivate() async throws { active = false }
    func invalidate() { active = false }
}
@MainActor private final class FailingBookSessionAudio: PlaybackAudioSession {
    var activations = 0
    func activate() async throws { activations += 1; if activations == 1 { throw URLError(.cannotConnectToHost) } }
    func deactivate() async throws {}
    func invalidate() {}
}
private actor BookSessionTransport: HTTPTransport {
    private(set) var progressRequests = 0
    private(set) var offlinePositions: [Double] = []
    private var holdLibrary = false
    private var libraryWaiting = false
    private var libraryWaiter: CheckedContinuation<Void,Never>?
    private var libraryObserver: CheckedContinuation<Void,Never>?
    func holdLibraryRead() { holdLibrary = true }
    func waitForLibraryRead() async { if !libraryWaiting { await withCheckedContinuation { libraryObserver = $0 } } }
    func releaseLibraryRead() { holdLibrary = false; libraryWaiter?.resume(); libraryWaiter = nil }
    let base = CallResumeTransitionTransport()
    let session = UUID().uuidString.lowercased()
    let multipart: Bool
    init(multipart: Bool = false) { self.multipart = multipart }
    private var rejectProgress = false
    private var rejectEdition = false
    private var source = "fixture"
    func rejectEditionOnBegin() { rejectEdition = true }
    func conflictProgress() { rejectProgress = true }
    private var holdRead = false
    private var waiter: CheckedContinuation<Void, Never>?
    func holdBookRead() { holdRead = true }
    func readWaiting() -> Bool { waiter != nil }
    func releaseBookRead() { holdRead = false; waiter?.resume(); waiter = nil }
    func owner(_ id: String) async { await base.setOwner(id) }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/play"), let body = request.httpBody,
           let object = try? JSONSerialization.jsonObject(with:body) as? [String:Any], object["use_remote"] as? Bool == true { rejectProgress = false }
        if path.hasSuffix("/offline-progress") || path.hasSuffix("/progress") { progressRequests += 1 }
        if rejectProgress && (path.hasSuffix("/offline-progress") || path.hasSuffix("/progress")) {
            return HTTPResult(status:409,data:Data(#"{"error":{"code":"audiobook_conflict","message":"Your place changed on another device."}}"#.utf8))
        }
        if path.hasSuffix("/sessions") || path.hasSuffix("/refresh") {
            return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:["access_token":"fixture","refresh_token":"fixture","expires_in":3600,"refresh_expires_in":86400,"session_id":session]))
        }
        guard path.hasPrefix("/api/v1/audiobooks/") else { return try await base.send(request) }
        if path.hasSuffix("/play"), rejectEdition {
            rejectEdition = false; source = "new-edition"
            return HTTPResult(status:409,data:Data(#"{"error":{"code":"audiobook_edition_changed","message":"This audiobook changed."}}"#.utf8))
        }
        let preferences: [String:Any] = ["visible":true,"continuous":false,"speed":1,"back_seconds":30,"forward_seconds":30]
        var book: [String:Any] = ["id":"book-one","library_id":"books","title":"A Story","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book-one","source_revision":source,"audio_revision":source,"duration":120,
            "parts":[["index":0,"start":0,"duration":120,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+String(repeating:"a",count:43)+"/0"]],"chapters":[["title":"Chapter","start":0,"end":120]],"progress":["currentTime":0,"lastUpdate":0,"isFinished":false]]
        if multipart {
            book["duration"] = 1.5
            book["parts"] = [
                ["index":0,"start":0,"duration":1,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+String(repeating:"a",count:43)+"/0"],
                ["index":1,"start":1,"duration":0.5,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+String(repeating:"a",count:43)+"/1"]]
            book["chapters"] = [["title":"Prologue","start":0,"end":1],["title":"Chapter One","start":1,"end":1.5]]
        }
        let object: [String:Any]
        if path.hasSuffix("/offline-progress"), let data = request.httpBody,
           let payload = try JSONSerialization.jsonObject(with:data) as? [String:Any], let position = payload["position"] as? Double { offlinePositions.append(position) }
        if path.hasSuffix("/library") {
            var stale = book; stale["progress"] = ["currentTime":40,"lastUpdate":0,"isFinished":false]
            if holdLibrary {
                libraryWaiting = true; libraryObserver?.resume(); libraryObserver = nil
                await withCheckedContinuation { libraryWaiter = $0 }
            }
            object = ["libraries":[["id":"books","name":"Books"]],"library_id":"books","items":[stale],"total":1]
        }
        else if path.contains("/book/") {
            if holdRead { await withCheckedContinuation { waiter = $0 } }
            object = book
        }
        else if path.hasSuffix("/status") { object = ["enabled":true,"preferences":preferences] }
        else if path.hasSuffix("/continue") { object = ["items":[]] }
        else if path.hasSuffix("/play") { object = ["book":book,"position":0,"lease":String(repeating:"a",count:43),"revision":1,"preferences":preferences,"music_epoch":1,"music_intent":1] }
        else if path.hasSuffix("/state") { object = ["active":true,"preferences":preferences,"queue":[]] }
        else { object = ["saved":true,"pending":false,"active":true,"remote_stamp":0] }
        return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:object))
    }
}
