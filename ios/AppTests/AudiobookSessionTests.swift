import XCTest
import ArrifyCore
@testable import Arrify

final class AudiobookSessionTests: XCTestCase {
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
}
@MainActor private final class BookSessionAudio: PlaybackAudioSession {
    private var active = false
    var activations = 0
    func activate() async throws { if !active { active = true; activations += 1 } }
    func deactivate() async throws { active = false }
    func invalidate() { active = false }
}
private actor BookSessionTransport: HTTPTransport {
    let base = CallResumeTransitionTransport()
    let session = UUID().uuidString.lowercased()
    private var holdRead = false
    private var waiter: CheckedContinuation<Void, Never>?
    func holdBookRead() { holdRead = true }
    func readWaiting() -> Bool { waiter != nil }
    func releaseBookRead() { holdRead = false; waiter?.resume(); waiter = nil }
    func owner(_ id: String) async { await base.setOwner(id) }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/sessions") || path.hasSuffix("/refresh") {
            return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:["access_token":"fixture","refresh_token":"fixture","expires_in":3600,"refresh_expires_in":86400,"session_id":session]))
        }
        guard path.hasPrefix("/api/v1/audiobooks/") else { return try await base.send(request) }
        let preferences: [String:Any] = ["visible":true,"continuous":false,"speed":1,"back_seconds":30,"forward_seconds":30]
        let book: [String:Any] = ["id":"book-one","library_id":"books","title":"A Story","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book-one","source_revision":"fixture","duration":120,
            "parts":[["index":0,"start":0,"duration":120,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+String(repeating:"a",count:43)+"/0"]],"chapters":[["title":"Chapter","start":0,"end":120]],"progress":["currentTime":0,"lastUpdate":0,"isFinished":false]]
        let object: [String:Any]
        if path.contains("/book/") {
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
