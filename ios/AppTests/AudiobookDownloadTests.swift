import XCTest
import ArrifyCore
@preconcurrency import AVFoundation
@testable import Arrify

final class AudiobookDownloadTests: XCTestCase {
    @MainActor func testPausingPartialDownloadKeepsItsProgressAndCanResume() async throws {
        let api = APIClient(transport:DownloadContractTransport(fail:false,holdTransfer:true),store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        defer { downloads.clear() }
        await downloads.download("book-one")
        for _ in 0..<300 where downloads.fraction("book-one") < 0.5 { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertEqual(downloads.fraction("book-one"),0.5,accuracy:0.01)
        XCTAssertTrue(downloads.working.contains("book-one")); XCTAssertFalse(downloads.complete("book-one"))
        await downloads.pause("book-one")
        for _ in 0..<300 where downloads.errors["book-one"] == nil { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertFalse(downloads.working.contains("book-one"))
        XCTAssertEqual(downloads.fraction("book-one"),0.5,accuracy:0.01,"Pausing must retain the displayed partial progress")
        await downloads.download("book-one")
        XCTAssertTrue(downloads.working.contains("book-one")); XCTAssertFalse(downloads.complete("book-one"))
    }
    @MainActor func testCompleteBookSurvivesStoreRecreationAndExplicitErasure() async throws {
        let api = try await makeAPI()
        let first = AudiobookDownloads(api:api, configuration:Self.configuration)
        await first.download("book-one")
        for _ in 0..<300 where !first.complete("book-one") && first.errors["book-one"] == nil { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(first.complete("book-one"), first.errors["book-one"] ?? "Download did not finish")
        let record = try XCTUnwrap(first.records["book-one"])
        let part = try XCTUnwrap(record.manifest.book.parts?.first)
        let file = try XCTUnwrap(first.file(record.manifest.book, part:part))
        let playable = try await AVURLAsset(url:file).load(.isPlayable)
        XCTAssertTrue(playable, "Completed files must be usable by native offline playback")
        first.checkpoint("book-one", position:0.75, remoteStamp:123)
        let restored = AudiobookDownloads(api:api, configuration:Self.configuration)
        await restored.configure()
        XCTAssertTrue(restored.complete("book-one"))
        XCTAssertEqual(restored.records["book-one"]?.lastPosition, 0.75)
        XCTAssertEqual(restored.records["book-one"]?.remoteStamp, 123)
        await restored.remove("book-one")
        XCTAssertFalse(restored.complete("book-one")); XCTAssertFalse(FileManager.default.fileExists(atPath:file.path))
        restored.clear(); first.clear(); await api.logout()
    }
    @MainActor func testFailedHTTPTransferNeverBecomesAnOfflineBook() async throws {
        let api = try await makeAPI(fail:true)
        let downloads = AudiobookDownloads(api:api, configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where downloads.working.contains("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertFalse(downloads.complete("book-one")); XCTAssertNotNil(downloads.errors["book-one"])
        downloads.clear(); XCTAssertTrue(downloads.records.isEmpty); await api.logout()
    }
    @MainActor func testEmptySuccessfulResponseNeverBecomesAnOfflineBook() async throws {
        let api = try await makeAPI(empty:true)
        let downloads = AudiobookDownloads(api:api, configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where downloads.working.contains("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertFalse(downloads.complete("book-one")); XCTAssertNotNil(downloads.errors["book-one"])
        downloads.clear(); await api.logout()
    }
    @MainActor func testCompletedDownloadStartsWhileProgressAndOwnershipRequestsAreBlocked() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport, store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        let downloads = AudiobookDownloads(api:api, configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(downloads.complete("book-one"))
        let music = PlaybackCoordinator(api:api, audioSession:UITestAudioSession())
        let audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api, music:music, audioSession:audio, downloads:downloads)
        await books.restore()
        let book = try XCTUnwrap(downloads.records["book-one"]?.manifest.book)
        await transport.blockProgress()
        books.play(book)
        // The fake transport stays blocked until releaseProgress below. Allow
        // the normal simulator media-readiness window without imposing a hardware
        // performance assertion: a network dependency would remain blocked forever.
        for _ in 0..<500 where audio.activations == 0 { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertGreaterThan(audio.activations,0,"Local playback must not wait for server reads; preparing=\(books.preparing), offline=\(books.offline), message=\(books.message ?? "none")")
        XCTAssertNil(books.message)
        books.clear(); await transport.releaseProgress(); music.stop(); await api.logout()
    }
    @MainActor func testTruncatedDownloadIsNeverReportedComplete() async throws {
        let api = try await makeAPI()
        let downloads = AudiobookDownloads(api:api, configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        let book = try XCTUnwrap(downloads.records["book-one"]?.manifest.book)
        let part = try XCTUnwrap(book.parts?.first)
        let file = try XCTUnwrap(downloads.file(book,part:part))
        try Data([1,2,3]).write(to:file)
        XCTAssertFalse(downloads.complete("book-one")); XCTAssertNil(downloads.file(book,part:part))
        downloads.clear(); await api.logout()
    }
    @MainActor func testCleanDownloadedCopyAdoptsNewerServerPositionBeforePlayback() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        downloads.checkpoint("book-one",position:0.2,remoteStamp:100)
        await transport.serverPosition(0.4,stamp:200)
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,downloads:downloads)
        defer { books.clear(); music.stop() }
        await books.restore(); let stale = try XCTUnwrap(downloads.records["book-one"]?.manifest.book)
        books.play(stale)
        for _ in 0..<500 where audio.activations == 0 { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertGreaterThan(audio.activations,0)
        XCTAssertEqual(books.position,0.4,accuracy:0.1)
        books.pause()
    }
    @MainActor func testListeningAutomaticallyDownloadsAfterAudioStarts() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        let file = try UITestAudiobookAudio.make()
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let books = AudiobookStore(api:api,music:music,audioSession:DownloadPlaybackAudio(),localMedia:{ _,_ in file },downloads:downloads)
        let previous = books.automaticDownloads
        defer { books.automaticDownloads = previous; books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        books.automaticDownloads = true
        await books.restore(); let book = try await api.audiobook("book-one")
        books.play(book)
        for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(books.playing)
        for _ in 0..<500 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(downloads.complete("book-one"),downloads.errors["book-one"] ?? "Automatic download did not finish")
        let count = await transport.downloadRequests
        XCTAssertEqual(count,1)
    }
    @MainActor func testMarkUnreadKeepsCompletedDownloadAndUpdatesItsDisplayedProgress() async throws {
        let api = try await makeAPI()
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        let record = try XCTUnwrap(downloads.records["book-one"])
        let part = try XCTUnwrap(record.manifest.book.parts?.first)
        let file = try XCTUnwrap(downloads.file(record.manifest.book,part:part))
        downloads.checkpoint("book-one",position:1,remoteStamp:100,finished:true)
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,downloads:downloads)
        defer { books.clear(); music.stop() }
        await books.restore()
        _ = try await books.markUnread(record.book)
        XCTAssertTrue(downloads.complete("book-one"))
        XCTAssertTrue(FileManager.default.fileExists(atPath:file.path))
        XCTAssertEqual(downloads.records["book-one"]?.book.progress?.currentTime,0)
        XCTAssertEqual(downloads.records["book-one"]?.book.progress?.isFinished,false)
        XCTAssertEqual(audio.activations,0)
    }
    @MainActor func testRemovingDownloadDuringResumeReadFallsThroughToStreaming() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        let book = try XCTUnwrap(downloads.records["book-one"]?.book)
        let file = try UITestAudiobookAudio.make()
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,localMedia:{ _,_ in file },downloads:downloads)
        defer { books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        await books.restore(); await transport.holdNextBookRead(); await transport.holdNextPlay()
        books.play(book)
        await transport.waitForBookRead()
        await books.removeDownload(book.id)
        await transport.releaseBookRead()
        await transport.waitForPlay()
        XCTAssertEqual(audio.activations,0,"Removed local audio must not start before a streaming grant arrives")
        XCTAssertFalse(books.active)
        await transport.releasePlay()
        for _ in 0..<500 where !books.playing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertGreaterThan(audio.activations,0)
        XCTAssertTrue(books.playing,books.message ?? "Streaming did not start")
        XCTAssertFalse(books.offline)
        XCTAssertNotNil(books.manifest)
        XCTAssertFalse(downloads.complete(book.id))
        XCTAssertNil(downloads.records[book.id])
    }
    @MainActor func testMarkUnreadCancelsDownloadedStartupWaitingForResumeRead() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        let book = try XCTUnwrap(downloads.records["book-one"]?.book)
        let file = try UITestAudiobookAudio.make()
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession())
        let audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,localMedia:{ _,_ in file },downloads:downloads)
        defer { books.clear(); music.stop(); try? FileManager.default.removeItem(at:file) }
        await books.restore(); await transport.holdNextBookRead()
        books.play(book)
        await transport.waitForBookRead()
        _ = try await books.markUnread(book)
        await transport.releaseBookRead()
        try await Task.sleep(for:.milliseconds(100))
        XCTAssertEqual(audio.activations,0)
        XCTAssertFalse(books.active)
        XCTAssertFalse(books.playing)
        XCTAssertFalse(books.preparing)
        XCTAssertEqual(books.position,0)
        XCTAssertTrue(downloads.complete(book.id))
    }
    @MainActor func testMetadataCorrectionKeepsOfflineAudioButNewMediaNeedsAnUpdate() async throws {
        let api = try await makeAPI()
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        defer { downloads.clear() }
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        let original = try XCTUnwrap(downloads.records["book-one"]?.book)
        let part = try XCTUnwrap(original.parts?.first)
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        var corrected = try XCTUnwrap(JSONSerialization.jsonObject(with:encoder.encode(original)) as? [String:Any])
        corrected["source_revision"] = "corrected-metadata"
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let same = try decoder.decode(Audiobook.self,from:JSONSerialization.data(withJSONObject:corrected))
        XCTAssertTrue(downloads.complete(same)); XCTAssertFalse(downloads.needsUpdate(same))
        XCTAssertNotNil(downloads.file(same,part:part))
        corrected["audio_revision"] = "replacement-audio"
        let changed = try decoder.decode(Audiobook.self,from:JSONSerialization.data(withJSONObject:corrected))
        XCTAssertTrue(downloads.needsUpdate(changed)); XCTAssertFalse(downloads.complete(changed))
        XCTAssertTrue(downloads.complete("book-one"),"The earlier copy remains intact until the user updates it")
        XCTAssertNil(downloads.file(changed,part:part),"Never play earlier audio as the new version")
    }

    @MainActor func testOfflineUnreadSurvivesStoreRecreationAndSynchronizesWithoutPlayback() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        downloads.checkpoint("book-one",position:0.5,remoteStamp:100,finished:false)
        let original = try XCTUnwrap(downloads.records["book-one"]?.book)
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession()), audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,downloads:downloads)
        await books.restore(); await transport.setOffline(true)
        _ = try await books.markUnread(original)
        XCTAssertTrue(books.hasPendingReadingState(original.id)); XCTAssertTrue(books.resumeBooks.isEmpty)
        XCTAssertTrue(downloads.complete(original.id)); XCTAssertEqual(audio.activations,0)
        let nextDownloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        let restored = AudiobookStore(api:api,music:music,audioSession:audio,downloads:nextDownloads)
        defer { restored.clear(); books.clear(); music.stop() }
        await restored.restore()
        XCTAssertTrue(restored.hasPendingReadingState(original.id)); XCTAssertTrue(restored.resumeBooks.isEmpty)
        XCTAssertFalse(restored.savedPlaces(for:original.id).isEmpty,"The earlier position is durable before resetting it")
        await transport.setOffline(false); await restored.networkAvailable()
        XCTAssertFalse(restored.hasPendingReadingState(original.id))
        XCTAssertEqual(nextDownloads.records[original.id]?.book.progress?.currentTime,0)
        XCTAssertEqual(audio.activations,0); XCTAssertFalse(restored.active)
        let accepted = await transport.acceptedOfflinePositions
        XCTAssertFalse(accepted.isEmpty); XCTAssertTrue(accepted.allSatisfy { $0 == 0 })
    }

    @MainActor func testExplicitRemoteChoiceNeverFallsBackToTheOldDownloadedPosition() async throws {
        let transport = DownloadContractTransport(fail:false)
        let api = APIClient(transport:transport,store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid",username:"fixture",password:"fixture",deviceName:"Test")
        let downloads = AudiobookDownloads(api:api,configuration:Self.configuration)
        await downloads.download("book-one")
        for _ in 0..<300 where !downloads.complete("book-one") { try await Task.sleep(for:.milliseconds(20)) }
        downloads.checkpoint("book-one",position:0.5)
        let book = try XCTUnwrap(downloads.records["book-one"]?.book)
        let music = PlaybackCoordinator(api:api,audioSession:UITestAudioSession()), audio = DownloadPlaybackAudio()
        let books = AudiobookStore(api:api,music:music,audioSession:audio,downloads:downloads)
        defer { books.clear(); music.stop() }
        await books.restore(); await transport.setOffline(true)
        books.play(book,useRemote:true)
        for _ in 0..<300 where books.preparing { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertFalse(books.playing); XCTAssertFalse(books.active); XCTAssertEqual(audio.activations,0)
        XCTAssertTrue(downloads.complete(book.id)); XCTAssertNotNil(books.message)
        XCTAssertEqual(books.savedPlaces(for:book.id).first?.checkpoint.position,0.5)
    }

    @MainActor private func makeAPI(fail: Bool = false, empty: Bool = false) async throws -> APIClient {
        let api = APIClient(transport:DownloadContractTransport(fail:fail, empty:empty), store:UITestStore())
        try await api.login(endpoint:"https://book-download.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        return api
    }
    private static let configuration: @MainActor @Sendable (String) -> URLSessionConfiguration = { _ in
        let value = URLSessionConfiguration.ephemeral; value.protocolClasses = [BookAudioProtocol.self]; return value
    }
}

private actor DownloadContractTransport: HTTPTransport {
    private let base = CallResumeTransitionTransport()
    private var remotePosition = 0.0
    private var remoteStamp = 0.0
    private(set) var downloadRequests = 0
    private var offline = false
    private(set) var acceptedOfflinePositions: [Double] = []
    func setOffline(_ value: Bool) { offline = value }
    func serverPosition(_ position: Double,stamp: Double) { remotePosition = position; remoteStamp = stamp }
    private var holdBookRead = false
    private var bookReadWaiting = false
    private var bookReadWaiter: CheckedContinuation<Void,Never>?
    private var bookReadObserver: CheckedContinuation<Void,Never>?
    func holdNextBookRead() { holdBookRead = true }
    func waitForBookRead() async {
        if bookReadWaiting { return }
        await withCheckedContinuation { bookReadObserver = $0 }
    }
    func releaseBookRead() { bookReadWaiter?.resume(); bookReadWaiter = nil }
    private var holdPlay = false
    private var playWaiting = false
    private var playWaiter: CheckedContinuation<Void,Never>?
    private var playObserver: CheckedContinuation<Void,Never>?
    func holdNextPlay() { holdPlay = true }
    func waitForPlay() async {
        if playWaiting { return }
        await withCheckedContinuation { playObserver = $0 }
    }
    func releasePlay() { playWaiter?.resume(); playWaiter = nil }
    private var blocked = false
    private var waiter: CheckedContinuation<Void,Never>?
    func blockProgress() { blocked = true }
    func releaseProgress() { blocked = false; waiter?.resume(); waiter = nil }
    let fail: Bool
    let empty: Bool
    let holdTransfer: Bool
    let session = UUID().uuidString.lowercased()
    init(fail: Bool, empty: Bool = false, holdTransfer: Bool = false) { self.fail = fail; self.empty = empty; self.holdTransfer = holdTransfer }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if offline { throw URLError(.notConnectedToInternet) }
        if path.hasSuffix("/offline-progress"), let data = request.httpBody,
           let value = try JSONSerialization.jsonObject(with:data) as? [String:Any], let position = value["position"] as? Double { acceptedOfflinePositions.append(position) }
        if blocked && (path.hasSuffix("/offline-progress") || path.hasSuffix("/native/playback")) { await withCheckedContinuation { waiter = $0 } }
        let object: [String:Any]
        if path.hasSuffix("capabilities") {
            object = ["server_id":"book-download-test", "features":["rotating_refresh","refresh_recovery_v1","native_media_manifest_v1","native_catalog_v1","native_playback_v1"]]
        } else if path.hasSuffix("sessions") || path.hasSuffix("refresh") {
            object = ["access_token":"fixture", "refresh_token":"fixture", "expires_in":3600,"refresh_expires_in":86400,"session_id":session]
        } else if path.hasSuffix("/audiobooks/download") || path.hasSuffix("/audiobooks/play") || path.contains("/audiobooks/book/") {
            if path.hasSuffix("/download") { downloadRequests += 1 }
            if path.hasSuffix("/play"), holdPlay {
                holdPlay = false
                await withCheckedContinuation { playWaiter = $0; playWaiting = true; playObserver?.resume(); playObserver = nil }
                playWaiting = false
            }
            let lease = String(repeating:fail ? "b" : empty ? "c" : holdTransfer ? "d" : "a", count:43)
            let book: [String:Any] = ["id":"book-one","library_id":"books","title":"Downloaded story","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book-one","source_revision":"fixture","audio_revision":"fixture-audio","duration":1,
                "parts":[["index":0,"start":0,"duration":1,"size_bytes":BookAudioProtocol.audio.count,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+lease+"/0"]],"chapters":[["title":"Chapter","start":0,"end":1]],"progress":["currentTime":remotePosition,"lastUpdate":remoteStamp,"isFinished":false]]
            if path.contains("/book/"), holdBookRead {
                holdBookRead = false
                await withCheckedContinuation { bookReadWaiter = $0; bookReadWaiting = true; bookReadObserver?.resume(); bookReadObserver = nil }
                bookReadWaiting = false
            }
            object = path.contains("/book/") ? book : ["book":book,"position":remotePosition,"lease":lease,"revision":0,"preferences":["visible":true,"continuous":false,"speed":1,"back_seconds":30,"forward_seconds":30]]
        } else if path.hasSuffix("/audiobooks/status") {
            object = ["enabled":true,"preferences":["visible":true,"continuous":false,"speed":1,"back_seconds":30,"forward_seconds":30]]
        } else if path.hasSuffix("/offline-progress") || path.hasSuffix("/audiobooks/progress") { object = ["saved":true,"pending":false,"active":true,"remote_stamp":remoteStamp] }
        else if !path.contains("/audiobooks/") { return try await base.send(request) }
        else { object = ["ok":true] }
        return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:object))
    }
}

/// Real URLSession download delegates receive deterministic audio and HTTP errors.
/// The application still validates the enrolled HTTPS origin and opaque grant path.
private final class BookAudioProtocol: URLProtocol, @unchecked Sendable {
    static let audio: Data = {
        var data = Data()
        func text(_ value: String) { data.append(contentsOf:value.utf8) }
        func u16(_ value: UInt16) { var n = value.littleEndian; withUnsafeBytes(of:&n) { data.append(contentsOf:$0) } }
        func u32(_ value: UInt32) { var n = value.littleEndian; withUnsafeBytes(of:&n) { data.append(contentsOf:$0) } }
        text("RIFF"); u32(16036); text("WAVEfmt "); u32(16); u16(1); u16(1); u32(8000); u32(16000); u16(2); u16(16); text("data"); u32(16000)
        data.append(Data(repeating:0,count:16000)); return data
    }()
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "book-download.invalid" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard let url = request.url else { return }
        let failed = url.path.contains(String(repeating:"b",count:43))
        let bytes = failed || url.path.contains(String(repeating:"c",count:43)) ? Data() : Self.audio
        let response = HTTPURLResponse(url:url,statusCode:failed ? 503 : 200,httpVersion:"HTTP/1.1",headerFields:["Content-Type":"audio/wav","Content-Length":String(bytes.count)])!
        client?.urlProtocol(self,didReceive:response,cacheStoragePolicy:.notAllowed)
        if url.path.contains(String(repeating:"d",count:43)) {
            client?.urlProtocol(self,didLoad:Data(bytes.prefix(bytes.count/2))); return
        }
        client?.urlProtocol(self,didLoad:bytes); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@MainActor private final class DownloadPlaybackAudio: PlaybackAudioSession {
    var activations = 0
    func activate() async throws { activations += 1 }
    func deactivate() async throws {}
    func invalidate() {}
}
