import XCTest
import ArrifyCore
import Network
@testable import Arrify

final class CatalogRecoveryTests: XCTestCase {
    @MainActor private func client(_ transport: RecoveryTransport) async throws -> APIClient {
        let api = APIClient(transport:transport, store:UITestStore())
        try await api.login(endpoint:"https://fixture.invalid", username:"fixture", password:"fixture", deviceName:"Test")
        return api
    }
    @MainActor func testEveryCatalogKindRecoversBeforeShowingAnError() async throws {
        for kind in ["tracks","albums","artists"] {
            let transport = RecoveryTransport(); await transport.fail(1)
            let browser = LibraryBrowser(api:try await client(transport), kind:kind, retryWait:{ _ in })
            await browser.load()
            XCTAssertNil(browser.error); XCTAssertFalse(browser.needsRecovery)
            XCTAssertEqual(kind == "tracks" ? browser.tracks.count : browser.entries.count,1)
            let calls = await transport.reads; XCTAssertEqual(calls,2)
        }
    }
    @MainActor func testFailedCatalogRefreshKeepsRowsAndRecoversWithoutResettingThePage() async throws {
        let transport = RecoveryTransport(), api = try await client(transport)
        let browser = LibraryBrowser(api:api, retryWait:{ _ in })
        await browser.load(); await transport.fail(4)
        await browser.load(more:true)
        XCTAssertEqual(browser.tracks.map(\.id),[1]); XCTAssertTrue(browser.needsRecovery)
        await browser.retry()
        XCTAssertEqual(browser.tracks.map(\.id),[1,2]); XCTAssertFalse(browser.needsRecovery)
        let offsets = await transport.offsets; XCTAssertEqual(offsets,[0,1,1,1,1,1])
    }
    @MainActor func testShelfDoesNotBecomeEmptyOnTimeoutAndRetainsLastSuccessfulBooks() async throws {
        let transport = RecoveryTransport(), api = try await client(transport)
        let shelf = AudiobookShelfStore(api:api,wait:{ _ in })
        XCTAssertFalse(shelf.hasLoaded)
        await transport.fail(4); await shelf.load()
        XCTAssertFalse(shelf.hasLoaded); XCTAssertTrue(shelf.needsRecovery)
        await shelf.reconnect()
        XCTAssertTrue(shelf.hasLoaded); XCTAssertEqual(shelf.items.map(\.id),["book-one"])
        XCTAssertNil(shelf.message)
        await transport.fail(4); await shelf.load()
        XCTAssertEqual(shelf.items.map(\.id),["book-one"]); XCTAssertTrue(shelf.hasLoaded)
        XCTAssertTrue(shelf.needsRecovery)
        await shelf.reconnect(); XCTAssertFalse(shelf.needsRecovery); XCTAssertNil(shelf.message)
    }
    @MainActor func testOnlySuccessfulEmptyShelfResponsesShowAnEmptyLibrary() async throws {
        let transport = RecoveryTransport(), api = try await client(transport)
        let shelf = AudiobookShelfStore(api:api,wait:{ _ in })
        await transport.emptyShelf(); await shelf.load()
        XCTAssertTrue(shelf.hasLoaded); XCTAssertTrue(shelf.items.isEmpty)
        XCTAssertFalse(shelf.needsRecovery); XCTAssertNil(shelf.message)
    }
    @MainActor func testLateShelfResponseCannotRestoreBooksAfterAccountClear() async throws {
        let transport = RecoveryTransport(), api = try await client(transport)
        let shelf = AudiobookShelfStore(api:api,wait:{ _ in })
        await transport.hold()
        let request = Task { await shelf.load() }
        for _ in 0..<100 {
            if await transport.waiting { break }
            try await Task.sleep(for:.milliseconds(10))
        }
        let waiting = await transport.waiting; XCTAssertTrue(waiting)
        shelf.clear(); await transport.release(); await request.value
        XCTAssertFalse(shelf.hasLoaded); XCTAssertTrue(shelf.items.isEmpty); XCTAssertFalse(shelf.loading)
    }
    @MainActor func testReopenedShelfRetriesAnInterruptedFirstLoad() async throws {
        let transport = RecoveryTransport(), api = try await client(transport)
        let shelf = AudiobookShelfStore(api:api,wait:{ _ in })
        await transport.hold()
        let old = Task { await shelf.load() }
        for _ in 0..<100 {
            if await transport.waiting { break }
            try await Task.sleep(for:.milliseconds(10))
        }
        old.cancel()
        let reopened = Task { await shelf.load() }
        await transport.release(); await old.value; await reopened.value
        XCTAssertTrue(shelf.hasLoaded); XCTAssertEqual(shelf.items.map(\.id),["book-one"])
        XCTAssertFalse(shelf.loading); XCTAssertNil(shelf.message)
    }
    func testBlockedArtworkConnectionsDoNotQueueCatalogRequests() async throws {
        let fixture = try ArtworkConnectionFixture()
        defer { fixture.stop() }
        for _ in 0..<500 where fixture.port == nil { try await Task.sleep(for:.milliseconds(10)) }
        let port = try XCTUnwrap(fixture.port)
        let transport = SessionTransport()
        let images = (0..<6).map { index in Task { try? await transport.send(URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/v1/artwork/\(index)")!)) } }
        defer { images.forEach { $0.cancel() } }
        for _ in 0..<500 where fixture.imageCount < 3 { try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertGreaterThanOrEqual(fixture.imageCount,3)
        try await Task.sleep(for:.milliseconds(200))
        var request = URLRequest(url:URL(string:"http://127.0.0.1:\(port)/api/v1/native/catalog")!)
        request.timeoutInterval = 2
        let started = Date()
        let response = try await transport.send(request)
        XCTAssertEqual(response.status,200)
        XCTAssertLessThan(Date().timeIntervalSince(started),1.5,"Images must not hold up interactive catalog reads")
    }
}

private actor RecoveryTransport: HTTPTransport {
    private let fallback = UITestTransport()
    private var failures = 0, empty = false, held = false
    private var continuation: CheckedContinuation<Void,Never>?
    private(set) var reads = 0
    private(set) var offsets: [Int] = []
    var waiting: Bool { continuation != nil }
    func fail(_ count: Int) { failures = count }
    func emptyShelf() { empty = true }
    func hold() { held = true }
    func release() { held = false; continuation?.resume(); continuation = nil }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        guard path.hasSuffix("/catalog") || path == "/api/v1/audiobooks/library" else { return try await fallback.send(request) }
        let query = URLComponents(url:request.url!,resolvingAgainstBaseURL:false)?.queryItems ?? []
        func value(_ key: String) -> String? { query.first { $0.name == key }?.value }
        let offset = Int(value("offset") ?? "0") ?? 0
        reads += 1; offsets.append(offset)
        if held { await withCheckedContinuation { continuation = $0 } }
        if failures > 0 { failures -= 1; throw URLError(.timedOut) }
        let object: [String:Any]
        if path.hasSuffix("/catalog") {
            let kind = value("kind") ?? "tracks"
            let row: [String:Any] = kind == "tracks" ? ["id":offset+1,"title":"Song","artist":"Artist","album":"Album"] : ["id":String(offset+1),"kind":kind,"name":"Album","subtitle":"Artist","section":"","track_count":1]
            object = ["items":[row],"total":2,"offset":offset,"revision":"fixture","next_offset":offset == 0 ? 1 as Any : NSNull()]
        } else {
            let book: [String:Any] = ["id":"book-one","library_id":"books","title":"Story","author":"Author","narrator":"Reader","series":"","description":"","cover":"/api/v1/audiobooks/cover/book-one","duration":120,"source_revision":"fixture"]
            object = ["libraries":[["id":"books","name":"Books"]],"library_id":"books","items":empty ? [] : [book],"total":empty ? 0 : 1,"next_page":NSNull()]
        }
        return HTTPResult(status:200,data:try JSONSerialization.data(withJSONObject:object))
    }
}

private final class ArtworkConnectionFixture: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label:"artwork-stall-test")
    private let lock = NSLock()
    private var connections: [NWConnection] = []
    private var count = 0
    private var readyPort: UInt16?
    var port: UInt16? { lock.lock(); defer { lock.unlock() }; return readyPort }
    var imageCount: Int { lock.lock(); defer { lock.unlock() }; return count }
    init() throws {
        listener = try NWListener(using:.tcp,on:.any)
        listener.stateUpdateHandler = { [weak self] state in
            guard let self, case .ready = state,
                  let port = self.listener.port?.rawValue, port != 0 else { return }
            self.lock.lock(); self.readyPort = port; self.lock.unlock()
        }
        listener.newConnectionHandler = { [weak self] connection in
            guard let self else { connection.cancel(); return }
            self.lock.lock(); self.connections.append(connection); self.lock.unlock()
            connection.start(queue:self.queue)
            self.read(connection)
        }
        listener.start(queue:queue)
    }
    private func read(_ connection: NWConnection, bytes: Data = Data()) {
        connection.receive(minimumIncompleteLength:1,maximumLength:8192) { [weak self] data,_,done,error in
            guard let self, let data, error == nil else { connection.cancel(); return }
            let bytes = bytes + data
            guard bytes.count <= 8192 else { connection.cancel(); return }
            let request = String(decoding:bytes,as:UTF8.self)
            guard request.contains("\r\n\r\n") else { if !done { self.read(connection,bytes:bytes) }; return }
            if request.contains("/api/v1/artwork/") {
                self.lock.lock(); self.count += 1; self.lock.unlock(); return
            }
            connection.send(content:Data("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}".utf8), completion:.contentProcessed { _ in connection.cancel() })
        }
    }
    func stop() { listener.cancel(); lock.lock(); let all = connections; lock.unlock(); all.forEach { $0.cancel() } }
}
