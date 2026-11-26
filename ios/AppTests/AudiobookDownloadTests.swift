import XCTest
import ArrifyCore
@preconcurrency import AVFoundation
@testable import Arrify

final class AudiobookDownloadTests: XCTestCase {
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
    let fail: Bool
    let empty: Bool
    let session = UUID().uuidString.lowercased()
    init(fail: Bool, empty: Bool = false) { self.fail = fail; self.empty = empty }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        let object: [String:Any]
        if path.hasSuffix("capabilities") {
            object = ["server_id":"book-download-test", "features":["rotating_refresh","refresh_recovery_v1","native_media_manifest_v1","native_catalog_v1","native_playback_v1"]]
        } else if path.hasSuffix("sessions") || path.hasSuffix("refresh") {
            object = ["access_token":"fixture", "refresh_token":"fixture", "expires_in":3600,"refresh_expires_in":86400,"session_id":session]
        } else if path.hasSuffix("/audiobooks/download") {
            let lease = String(repeating:fail ? "b" : empty ? "c" : "a", count:43)
            let book: [String:Any] = ["id":"book-one","library_id":"books","title":"Downloaded story","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book-one","source_revision":"fixture","duration":1,
                "parts":[["index":0,"start":0,"duration":1,"size_bytes":BookAudioProtocol.audio.count,"mime":"audio/wav","url":"/api/v1/audiobooks/stream/"+lease+"/0"]],"chapters":[["title":"Chapter","start":0,"end":1]],"progress":["currentTime":0,"lastUpdate":0,"isFinished":false]]
            object = ["book":book,"position":0,"lease":lease,"revision":0,"preferences":["visible":true,"continuous":false,"speed":1,"back_seconds":30,"forward_seconds":30]]
        } else { object = ["ok":true] }
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
        client?.urlProtocol(self,didLoad:bytes); client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}
