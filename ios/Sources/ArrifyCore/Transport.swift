import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct HTTPResult: Sendable {
    public let status: Int
    public let data: Data
    public init(status: Int, data: Data) { self.status = status; self.data = data }
}
public protocol HTTPTransport: Sendable {
    func send(_ request: URLRequest) async throws -> HTTPResult
    func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws
}
private final class RejectRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
public final class SessionTransport: HTTPTransport, Sendable {
    private let session: URLSession
    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 20; config.timeoutIntervalForResource = 30
        session = URLSession(configuration: config, delegate: RejectRedirects(), delegateQueue: nil)
    }
    public func send(_ request: URLRequest) async throws -> HTTPResult {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        return HTTPResult(status: response.statusCode, data: data)
    }
    public func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws {
        #if canImport(FoundationNetworking)
        throw APIError.unsupportedServer
        #else
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = 25; config.timeoutIntervalForResource = 120
        let events = URLSession(configuration: config, delegate: RejectRedirects(), delegateQueue: nil)
        defer { events.invalidateAndCancel() }
        let (bytes, response) = try await events.bytes(for: request)
        guard let response = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard response.statusCode == 200 else {
            throw APIError.rejected(status: response.statusCode, code: "events_unavailable", message: "Live updates unavailable.")
        }
        guard response.mimeType == "text/event-stream" else { throw APIError.invalidResponse }
        var parser = ListeningEventParser(); var line: [UInt8] = []
        for try await byte in bytes {
            try Task.checkCancellation()
            if byte == 10 {
                if parser.line(String(decoding: line, as: UTF8.self)) { await changed() }
                line.removeAll(keepingCapacity: true)
            } else if byte != 13 {
                guard line.count < 1024 else { throw APIError.invalidResponse }
                line.append(byte)
            }
        }
        #endif
    }
    deinit { session.invalidateAndCancel() }
}
public protocol CredentialStore: Sendable {
    func load() async throws -> Credential?
    func save(_ credential: Credential) async throws
    func clear() async throws
}

public extension HTTPTransport {
    func listeningEvents(_ request: URLRequest, changed: @escaping @Sendable () async -> Void) async throws {
        throw APIError.unsupportedServer
    }
}

/// The server emits account-scoped invalidation versions, never playback commands.
public struct ListeningEventParser: Sendable {
    private var event = ""
    private var data = ""
    private var previous: String?
    public init() {}
    public mutating func line(_ line: String) -> Bool {
        if line.isEmpty {
            defer { event = ""; data = "" }
            guard event == "session", !data.isEmpty, data.allSatisfy(\.isNumber), data != previous else { return false }
            previous = data; return true
        }
        if line.hasPrefix("event:") { event = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces) }
        if line.hasPrefix("data:") { data = String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces) }
        return false
    }
}
