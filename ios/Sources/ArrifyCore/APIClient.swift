import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private struct ErrorBody: Decodable { let code: String; let message: String }
private struct ErrorEnvelope: Decodable { let error: ErrorBody }

public actor APIClient {
    private let transport: any HTTPTransport
    private let store: any CredentialStore
    private var credential: Credential?
    private var refreshTask: Task<Credential, Error>?
    private var generation = UUID()
    public init(transport: any HTTPTransport = SessionTransport(), store: any CredentialStore) {
        self.transport = transport; self.store = store
    }
    private static func encoder() -> JSONEncoder { let e = JSONEncoder(); e.keyEncodingStrategy = .convertToSnakeCase; return e }
    private static func decoder() -> JSONDecoder { let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d }
    private static func request(endpoint: Endpoint, path: String, method: String = "GET", body: Data? = nil, token: String? = nil) throws -> URLRequest {
        guard path.hasPrefix("/api/v1/"), let url = URL(string: path, relativeTo: endpoint.url)?.absoluteURL,
              url.host == endpoint.url.host, url.scheme == endpoint.url.scheme, url.port == endpoint.url.port else { throw APIError.invalidEndpoint }
        var request = URLRequest(url: url); request.httpMethod = method; request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return request
    }
    private static func decode<T: Decodable>(_ result: HTTPResult, as: T.Type) throws -> T {
        guard (200..<300).contains(result.status) else {
            let error = try? decoder().decode(ErrorEnvelope.self, from: result.data)
            throw APIError.rejected(status: result.status, code: error?.error.code ?? "request_failed", message: error?.error.message ?? "The request could not be completed.")
        }
        return try decoder().decode(T.self, from: result.data)
    }
    private func capabilities(_ endpoint: Endpoint) async throws -> Capabilities {
        let response = try await transport.send(Self.request(endpoint: endpoint, path: "/api/v1/native/capabilities"))
        let result = try Self.decode(response, as: Capabilities.self)
        guard ["rotating_refresh", "native_media_manifest_v1", "native_catalog_v1", "native_playback_v1"].allSatisfy(result.features.contains) else { throw APIError.unsupportedServer }
        return result
    }
    public func restore() async throws -> String? {
        let epoch = generation
        guard let saved = try await store.load() else { return nil }
        let endpoint = try Endpoint(saved.endpoint)
        let caps = try await capabilities(endpoint)
        guard caps.serverId == saved.serverId else { throw APIError.serverChanged }
        guard epoch == generation else { throw CancellationError() }
        credential = saved
        _ = try await access()
        return saved.endpoint
    }
    public func login(endpoint text: String, username: String, password: String, deviceName: String) async throws {
        generation = UUID(); let epoch = generation
        refreshTask?.cancel(); refreshTask = nil; credential = nil
        let endpoint = try Endpoint(text); let caps = try await capabilities(endpoint)
        struct Login: Encodable { let username: String; let password: String; let deviceName: String }
        let body = try Self.encoder().encode(Login(username: username, password: password, deviceName: deviceName))
        let response = try await transport.send(Self.request(endpoint: endpoint, path: "/api/v1/native/sessions", method: "POST", body: body))
        let pair = try Self.decode(response, as: TokenPair.self)
        let value = Credential(endpoint: endpoint.url.absoluteString, serverId: caps.serverId, tokens: pair)
        guard generation == epoch else { throw CancellationError() }
        try await store.save(value)
        guard generation == epoch else { throw CancellationError() }
        credential = value
    }
    private func access(force: Bool = false) async throws -> Credential {
        if let task = refreshTask { return try await task.value }
        guard let saved = credential else { throw APIError.signInRequired }
        if !force && saved.accessExpires.timeIntervalSinceNow > 60 { return saved }
        let epoch = generation
        let task = Task<Credential, Error> {
            struct Refresh: Encodable { let refreshToken: String }
            let body = try Self.encoder().encode(Refresh(refreshToken: saved.tokens.refreshToken))
            let response = try await transport.send(Self.request(endpoint: Endpoint(saved.endpoint), path: "/api/v1/native/refresh", method: "POST", body: body))
            let pair = try Self.decode(response, as: TokenPair.self)
            let renewed = Credential(endpoint: saved.endpoint, serverId: saved.serverId, tokens: pair)
            guard generation == epoch else { throw CancellationError() }
            try await store.save(renewed)
            guard generation == epoch else { throw CancellationError() }
            credential = renewed
            return renewed
        }
        refreshTask = task
        do {
            let value = try await task.value
            if generation == epoch { refreshTask = nil }
            return value
        } catch {
            if generation == epoch {
                credential = nil; refreshTask = nil
                try? await store.clear()
            }
            // A refresh response may have been lost after rotation. Never replay it.
            throw APIError.signInRequired
        }
    }
    private func authorizedResponse(_ path: String, method: String = "GET", body: Data? = nil) async throws -> HTTPResult {
        var saved = try await access()
        var response = try await transport.send(Self.request(endpoint: Endpoint(saved.endpoint), path: path, method: method, body: body, token: saved.tokens.accessToken))
        if response.status == 401 {
            // A concurrent request may already have rotated this access token.
            saved = try await access(force: credential?.tokens.accessToken == saved.tokens.accessToken)
            response = try await transport.send(Self.request(endpoint: Endpoint(saved.endpoint), path: path, method: method, body: body, token: saved.tokens.accessToken))
        }
        return response
    }
    private func call<T: Decodable & Sendable>(_ path: String, method: String = "GET", body: Data? = nil) async throws -> T {
        try Self.decode(await authorizedResponse(path, method: method, body: body), as: T.self)
    }
    public func artwork(_ id: Int) async throws -> Data? {
        let response = try await authorizedResponse("/api/v1/artwork/\(id)")
        guard response.status == 200, response.data.count <= 10 * 1024 * 1024 else { return nil }
        return response.data
    }
    public func catalog(query: String, offset: Int = 0, revision: String? = nil) async throws -> CatalogPage {
        var c = URLComponents(); c.path = "/api/v1/native/catalog"
        c.queryItems = [URLQueryItem(name: "q", value: query), .init(name: "limit", value: "50"), .init(name: "offset", value: String(offset))]
        if let revision { c.queryItems?.append(.init(name: "revision", value: revision)) }
        return try await call(c.string!)
    }
    public func library() async throws -> LibraryOverview { try await call("/api/v1/native/library") }
    public func entities(kind: String, query: String, offset: Int = 0, revision: String? = nil) async throws -> EntityPage {
        guard ["albums", "artists"].contains(kind) else { throw APIError.invalidResponse }
        var c = URLComponents(); c.path = "/api/v1/native/catalog"
        c.queryItems = [.init(name: "kind", value: kind), .init(name: "q", value: query), .init(name: "limit", value: "50"), .init(name: "offset", value: String(offset))]
        if let revision { c.queryItems?.append(.init(name: "revision", value: revision)) }
        return try await call(c.string!)
    }
    public func detail(_ source: LibrarySource, offset: Int = 0, revision: String? = nil) async throws -> LibraryDetail {
        var c = URLComponents(); c.path = "/api/v1/native/library/detail"
        c.queryItems = [.init(name: "kind", value: source.kind), .init(name: "id", value: source.id), .init(name: "limit", value: "50"), .init(name: "offset", value: String(offset))]
        if let revision { c.queryItems?.append(.init(name: "revision", value: revision)) }
        return try await call(c.string!)
    }
    public func artwork(path: String) async throws -> Data? {
        // Only our authenticated image endpoints; never fetch arbitrary catalog URLs.
        guard let c = URLComponents(string: path), c.scheme == nil, c.host == nil,
              ["/api/v1/artwork/", "/api/v1/artist-artwork/", "/api/v1/stations/artwork/", "/api/v1/branding/icon"].contains(where: c.path.hasPrefix) else { return nil }
        let response = try await authorizedResponse(path)
        guard response.status == 200, response.data.count <= 10 * 1024 * 1024 else { return nil }
        return response.data
    }
    public func queue(offset: Int = 0, revision: Int? = nil) async throws -> QueuePage {
        var path = "/api/v1/native/playback/queue?offset=\(offset)&limit=100"
        if let revision { path += "&revision=\(revision)" }
        return try await call(path)
    }
    public func listeningEvents(changed: @escaping @Sendable () async -> Void) async throws {
        var saved = try await access()
        for attempt in 0..<2 {
            var request = try Self.request(endpoint: Endpoint(saved.endpoint), path: "/api/v1/listening/events", token: saved.tokens.accessToken)
            request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            do { try await transport.listeningEvents(request, changed: changed); return }
            catch APIError.rejected(let status, _, _) where status == 401 || status == 403 {
                guard attempt == 0 else { throw APIError.signInRequired }
                saved = try await access(force: credential?.tokens.accessToken == saved.tokens.accessToken)
            }
        }
    }
    public func state() async throws -> PlaybackState { try await call("/api/v1/native/playback") }
    public func command(_ command: PlaybackCommand) async throws -> PlaybackState {
        try await call("/api/v1/native/playback", method: "POST", body: Self.encoder().encode(command))
    }
    public func manifest(deviceId: String, epoch: Int, profile: String, retry: Bool = false) async throws -> Manifest {
        struct Input: Encodable { let deviceId: String; let ownerEpoch: Int; let profile: String; let retry: Bool }
        return try await call("/api/v1/native/playback/manifest", method: "POST", body: Self.encoder().encode(Input(deviceId: deviceId, ownerEpoch: epoch, profile: profile, retry: retry)))
    }
    public func track(_ id: Int) async throws -> Track {
        struct Result: Decodable, Sendable { let track: Track }
        let result: Result = try await call("/api/v1/tracks/\(id)")
        return result.track
    }
    public func mediaURL(_ item: MediaItem) throws -> URL {
        guard let saved = credential, let path = item.url else { throw APIError.signInRequired }
        return try Endpoint(saved.endpoint).mediaURL(path)
    }
    public func logout() async {
        let saved = credential
        generation = UUID(); refreshTask?.cancel(); refreshTask = nil; credential = nil
        try? await store.clear()
        if let saved {
            struct Revoke: Encodable { let sessionId: String }
            if let body = try? Self.encoder().encode(Revoke(sessionId: saved.tokens.sessionId)),
               let request = try? Self.request(endpoint: Endpoint(saved.endpoint), path: "/api/v1/native/sessions/revoke", method: "POST", body: body, token: saved.tokens.accessToken) {
                _ = try? await transport.send(request)
            }
        }
    }
}
