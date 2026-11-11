import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

private struct RouteUnavailable: Error { let cause: any Error }
private struct ErrorBody: Decodable { let code: String; let message: String }
private struct ErrorEnvelope: Decodable { let error: ErrorBody }

public actor APIClient {
    private let transport: any HTTPTransport
    private let store: any CredentialStore
    private let router: ServerRouter
    private var credential: Credential?
    private var refreshTask: Task<Credential, Error>?
    private var generation = UUID()
    public init(transport: any HTTPTransport = SessionTransport(), store: any CredentialStore) {
        self.transport = transport; self.store = store; router = ServerRouter(transport: transport)
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
        guard var saved = try await store.load() else { return nil }
        let endpoint = try Endpoint(saved.endpoint)
        if saved.serverPublicKey == nil {
            let caps = try await capabilities(endpoint)
            guard caps.serverId == saved.serverId else { throw APIError.serverChanged }
            saved.serverPublicKey = caps.serverPublicKey
            try await store.save(saved)
        }
        guard epoch == generation else { throw CancellationError() }
        try await configure(saved)
        _ = try await router.selected()
        guard epoch == generation else { throw CancellationError() }
        credential = saved
        _ = try await access()
        return saved.endpoint
    }
    public func login(endpoint text: String, homeEndpoint homeText: String? = nil, username: String, password: String, deviceName: String) async throws {
        generation = UUID(); let epoch = generation
        refreshTask?.cancel(); refreshTask = nil; credential = nil
        let endpoint = try Endpoint(text); let caps = try await capabilities(endpoint)
        let home = try Self.homeAddress(homeText, publicEndpoint: endpoint)
        await router.configure(primary: endpoint, home: home, id: caps.serverId, key: caps.serverPublicKey)
        let selected = try await router.selected()
        guard generation == epoch else { throw CancellationError() }
        struct Login: Encodable { let username: String; let password: String; let deviceName: String }
        let body = try Self.encoder().encode(Login(username: username, password: password, deviceName: deviceName))
        let response = try await transport.send(Self.request(endpoint: selected, path: "/api/v1/native/sessions", method: "POST", body: body))
        let pair = try Self.decode(response, as: TokenPair.self)
        var value = Credential(endpoint: endpoint.url.absoluteString, serverId: caps.serverId, tokens: pair, homeEndpoint: home?.url.absoluteString, serverPublicKey: caps.serverPublicKey)
        guard generation == epoch else { throw CancellationError() }
        value.artworkAccount = username.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
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
            let endpoint: Endpoint
            do { endpoint = try await router.selected() }
            catch { throw RouteUnavailable(cause: error) }
            struct Refresh: Encodable { let refreshToken: String }
            let body = try Self.encoder().encode(Refresh(refreshToken: saved.tokens.refreshToken))
            let response = try await transport.send(Self.request(endpoint: endpoint, path: "/api/v1/native/refresh", method: "POST", body: body))
            let pair = try Self.decode(response, as: TokenPair.self)
            var renewed = Credential(endpoint: saved.endpoint, serverId: saved.serverId, tokens: pair, homeEndpoint: saved.homeEndpoint, serverPublicKey: saved.serverPublicKey)
            guard generation == epoch else { throw CancellationError() }
            renewed.artworkAccount = saved.artworkAccount
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
            if let unavailable = error as? RouteUnavailable {
                if generation == epoch { refreshTask = nil }
                throw unavailable.cause
            }
            if generation == epoch {
                credential = nil; refreshTask = nil
                try? await store.clear()
            }
            // A refresh response may have been lost after rotation. Never replay it.
            throw APIError.signInRequired
        }
    }
    private func measuredSend(_ path: String, method: String, body: Data?, saved: Credential, endpoint: Endpoint) async throws -> HTTPResult {
        let started = Date()
        do {
            let result = try await transport.send(Self.request(endpoint: endpoint, path: path, method: method, body: body, token: saved.tokens.accessToken))
            await router.observed(endpoint, seconds: Date().timeIntervalSince(started), statusCode: result.status)
            return result
        } catch {
            guard !RequestCancellation.matches(error), !Task.isCancelled else { throw CancellationError() }
            await router.failed(endpoint)
            throw error
        }
    }
    private func sendAuthorized(_ path: String, method: String, body: Data?, saved: Credential) async throws -> HTTPResult {
        let endpoint = try await router.selected()
        let result: HTTPResult
        do {
            result = try await measuredSend(path, method: method, body: body, saved: saved, endpoint: endpoint)
        } catch {
            guard !RequestCancellation.matches(error), !Task.isCancelled else { throw CancellationError() }
            // Mutations and rotating refresh tokens must never be replayed after an ambiguous failure.
            guard method == "GET" else { throw error }
            let alternate = try await router.selected()
            guard alternate.url != endpoint.url else {
                // Identity probe succeeded, but the actual API request did not.
                await router.observed(endpoint, seconds: 0, statusCode: 503)
                throw error
            }
            return try await measuredSend(path, method: "GET", body: nil, saved: saved, endpoint: alternate)
        }
        if [502, 503, 504].contains(result.status) {
            await router.failed(endpoint)
            if method == "GET", let alternate = try? await router.selected(), alternate.url != endpoint.url {
                return try await measuredSend(path, method: "GET", body: nil, saved: saved, endpoint: alternate)
            }
            await router.observed(endpoint, seconds: 0, statusCode: result.status)
        }
        return result
    }
    private func authorizedResponse(_ path: String, method: String = "GET", body: Data? = nil) async throws -> HTTPResult {
        var saved = try await access()
        var response = try await sendAuthorized(path, method: method, body: body, saved: saved)
        if response.status == 401 {
            saved = try await access(force: credential?.tokens.accessToken == saved.tokens.accessToken)
            response = try await sendAuthorized(path, method: method, body: body, saved: saved)
        }
        return response
    }
    private static func homeAddress(_ value: String?, publicEndpoint: Endpoint) throws -> Endpoint? {
        guard let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        let home = try Endpoint(value.trimmingCharacters(in: .whitespacesAndNewlines))
        return home.url == publicEndpoint.url ? nil : home
    }
    private func configure(_ saved: Credential) async throws {
        let primary = try Endpoint(saved.endpoint)
        let home = try Self.homeAddress(saved.homeEndpoint, publicEndpoint: primary)
        await router.configure(primary: primary, home: home, id: saved.serverId, key: saved.serverPublicKey)
    }
    public func artworkCacheScope() -> String? {
        guard let credential, let account = credential.artworkAccount else { return nil }
        return credential.serverId + "\n" + (credential.serverPublicKey ?? credential.endpoint) + "\n" + account
    }
    public func connection() async -> ServerConnection { await router.snapshot() }
    public func connectionAddresses() -> (publicAddress: String, homeAddress: String?)? {
        credential.map { ($0.endpoint, $0.homeEndpoint) }
    }
    public func networkChanged() async { await router.invalidate() }
    public func updateAddresses(publicAddress: String, homeAddress: String?) async throws {
        if let task = refreshTask { _ = try await task.value }
        guard var saved = credential else { throw APIError.signInRequired }
        let epoch = generation
        let primary = try Endpoint(publicAddress)
        let home = try Self.homeAddress(homeAddress, publicEndpoint: primary)
        try await ServerRouter.verify(primary, id: saved.serverId, key: saved.serverPublicKey, transport: transport)
        let caps = try await capabilities(primary)
        guard caps.serverId == saved.serverId, saved.serverPublicKey == nil || saved.serverPublicKey == caps.serverPublicKey else { throw APIError.serverChanged }
        guard generation == epoch, credential?.tokens.accessToken == saved.tokens.accessToken else { throw CancellationError() }
        saved.endpoint = primary.url.absoluteString; saved.homeEndpoint = home?.url.absoluteString
        saved.serverPublicKey = caps.serverPublicKey
        try await store.save(saved)
        guard generation == epoch else { throw CancellationError() }
        credential = saved
        try await configure(saved)
        _ = try await router.selected()
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
    public func originalAlbum(trackId: Int) async throws -> OriginalAlbum {
        try await call("/api/v1/discover/album?track=\(trackId)")
    }
    public func favorites() async throws -> FavoriteSongs { try await call("/api/v1/native/favorites") }
    public func setFavorite(_ id: Int, liked: Bool) async throws -> FavoriteSongs {
        struct Input: Encodable { let trackId: Int; let liked: Bool }
        return try await call("/api/v1/native/favorites", method: "POST", body: Self.encoder().encode(Input(trackId: id, liked: liked)))
    }
    public func sessionScope() async throws -> String {
        let saved = try await access()
        return saved.serverId + ":" + saved.tokens.sessionId
    }
    public func liveActivityControlsAvailable() async throws -> Bool {
        struct Status: Decodable { let controlsAvailable: Bool? }
        let status: Status = try await call("/api/v1/native/live-activity")
        return status.controlsAvailable == true
    }
    public func liveActivityCommand(id: String, owner: String, epoch: Int, command: String, nonce: String, proof: String) async throws -> PlaybackState {
        struct Input: Encodable { let action = "control"; let activityId: String; let ownerDeviceId: String; let ownerEpoch: Int; let command: String; let nonce: String; let proof: String }
        return try await call("/api/v1/native/live-activity", method: "POST", body: Self.encoder().encode(Input(activityId: id, ownerDeviceId: owner, ownerEpoch: epoch, command: command, nonce: nonce, proof: proof)))
    }
    public struct LocalChallenge: Decodable, Sendable { public let nonce: String; public let service: String }
    public func publishLocalPresence(owner: String, epoch: Int, service: String, secret: String) async throws {
        struct Input: Encodable { let action = "publish"; let ownerDeviceId: String; let ownerEpoch: Int; let service: String; let secret: String }
        struct Result: Decodable { let ok: Bool }
        let _: Result = try await call("/api/v1/native/local-presence", method: "POST", body: Self.encoder().encode(Input(ownerDeviceId: owner, ownerEpoch: epoch, service: service, secret: secret)))
    }
    public func localChallenge(owner: String, epoch: Int, purpose: String) async throws -> LocalChallenge {
        struct Input: Encodable { let action = "challenge"; let ownerDeviceId: String; let ownerEpoch: Int; let purpose: String }
        return try await call("/api/v1/native/local-presence", method: "POST", body: Self.encoder().encode(Input(ownerDeviceId: owner, ownerEpoch: epoch, purpose: purpose)))
    }
    public func verifyLocalPresence(owner: String, epoch: Int, nonce: String, proof: String) async throws -> Bool {
        struct Input: Encodable { let action = "verify"; let activityId = "local-check"; let ownerDeviceId: String; let ownerEpoch: Int; let nonce: String; let proof: String }
        struct Result: Decodable { let controlsAvailable: Bool }
        let result: Result = try await call("/api/v1/native/live-activity", method: "POST", body: Self.encoder().encode(Input(ownerDeviceId: owner, ownerEpoch: epoch, nonce: nonce, proof: proof)))
        return result.controlsAvailable
    }
    public func liveActivityAvailable() async throws -> Bool {
        struct Status: Decodable { let available: Bool }
        let status: Status = try await call("/api/v1/native/live-activity")
        return status.available
    }
    public func registerLiveActivity(id: String, token: String, owner: String) async throws {
        struct Input: Encodable { let action = "register"; let activityId: String; let token: String; let ownerDeviceId: String }
        struct Result: Decodable { let ok: Bool }
        let _: Result = try await call("/api/v1/native/live-activity", method: "POST", body: Self.encoder().encode(Input(activityId: id, token: token, ownerDeviceId: owner)))
    }
    public func stopLiveActivity(id: String) async throws {
        struct Input: Encodable { let action = "stop"; let activityId: String }
        struct Result: Decodable { let ok: Bool }
        let _: Result = try await call("/api/v1/native/live-activity", method: "POST", body: Self.encoder().encode(Input(activityId: id)))
    }
    public func createPlaylist(name: String, shared: Bool, trackId: Int? = nil) async throws -> LibraryEntry {
        struct Input: Encodable { let name: String; let visibility: String; let addTrackId: Int? }
        return try await call("/api/v1/native/playlists", method: "POST", body: Self.encoder().encode(Input(name: name, visibility: shared ? "shared" : "private", addTrackId: trackId)))
    }
    public func addToPlaylist(id: Int, trackId: Int) async throws -> LibraryEntry {
        struct Input: Encodable { let id: Int; let addTrackId: Int }
        return try await call("/api/v1/native/playlists", method: "POST", body: Self.encoder().encode(Input(id: id, addTrackId: trackId)))
    }
    public func register(endpoint text: String, username: String, email: String, password: String) async throws -> AccountRegistration {
        let endpoint = try Endpoint(text)
        struct Signup: Encodable { let username: String; let email: String; let password: String }
        let body = try Self.encoder().encode(Signup(username: username, email: email, password: password))
        let response = try await transport.send(Self.request(endpoint: endpoint, path: "/api/v1/native/register", method: "POST", body: body))
        return try Self.decode(response, as: AccountRegistration.self)
    }
    public func browse() async throws -> BrowseOverview { try await call("/api/v1/browse") }
    public func browsePage(_ filters: BrowseFilters, offset: Int = 0) async throws -> BrowsePage {
        var q = URLComponents()
        q.queryItems = [URLQueryItem(name: "page", value: "1"), URLQueryItem(name: "genre", value: filters.genre), URLQueryItem(name: "decade", value: filters.decade), URLQueryItem(name: "kind", value: filters.kind), URLQueryItem(name: "offset", value: String(offset)), URLQueryItem(name: "limit", value: "50")]
        return try await call("/api/v1/browse?" + (q.percentEncodedQuery ?? ""))
    }
    public func browseChart(genre: String, recent: Bool) async throws -> BrowseChart {
        var q = URLComponents(); q.queryItems = [URLQueryItem(name: "genre", value: genre), URLQueryItem(name: "new", value: recent ? "1" : "0")]
        return try await call("/api/v1/browse/charts?" + (q.percentEncodedQuery ?? ""))
    }
    public func requestChartSong(genre: String, entry: String) async throws -> BrowseRequestResult {
        struct Request: Encodable { let genre: String; let entry: String }
        return try await call("/api/v1/browse/request", method: "POST", body: Self.encoder().encode(Request(genre: genre, entry: entry)))
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
            var request = try Self.request(endpoint: try await router.selected(), path: "/api/v1/listening/events", token: saved.tokens.accessToken)
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
    public func mediaURL(_ item: MediaItem) async throws -> URL {
        guard credential != nil, let path = item.url else { throw APIError.signInRequired }
        return try await router.selected().mediaURL(path)
    }
    public func alternateMediaURL(_ url: URL) async throws -> URL? {
        guard let saved = credential, url.scheme == "https", let host = url.host else { return nil }
        var origin = URLComponents(); origin.scheme = "https"; origin.host = host; origin.port = url.port
        let failed = try Endpoint(origin.string!)
        let primary = try Endpoint(saved.endpoint)
        let home = try Self.homeAddress(saved.homeEndpoint, publicEndpoint: primary)
        guard failed.url == primary.url || failed.url == home?.url else { return nil }
        await router.failed(failed)
        let selected = try await router.selected()
        guard selected.url != failed.url else { return nil }
        var path = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        path.scheme = nil; path.host = nil; path.port = nil
        return try selected.mediaURL(path.string!)
    }
    public func logout() async {
        let address = await router.snapshot().address
        let saved = credential
        generation = UUID(); refreshTask?.cancel(); refreshTask = nil; credential = nil
        try? await store.clear()
        await router.reset()
        if let saved {
            struct Revoke: Encodable { let sessionId: String }
            if let body = try? Self.encoder().encode(Revoke(sessionId: saved.tokens.sessionId)),
               let request = try? Self.request(endpoint: Endpoint(address ?? saved.endpoint), path: "/api/v1/native/sessions/revoke", method: "POST", body: body, token: saved.tokens.accessToken) {
                _ = try? await transport.send(request)
            }
        }
    }
}
