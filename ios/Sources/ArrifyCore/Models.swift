import Foundation

public struct Capabilities: Codable, Sendable {
    public let serverId: String
    public let features: [String]
    public var serverPublicKey: String?
}
public struct TokenPair: Codable, Sendable {
    public let accessToken: String
    public let refreshToken: String
    public let expiresIn: Int
    public let refreshExpiresIn: Int
    public let sessionId: String
}
public struct Credential: Codable, Sendable {
    public var artworkAccount: String?
    public var endpoint: String
    public var homeEndpoint: String?
    public var serverPublicKey: String?
    public let serverId: String
    public let tokens: TokenPair
    public let accessExpires: Date
    public init(endpoint: String, serverId: String, tokens: TokenPair, now: Date = Date(), homeEndpoint: String? = nil, serverPublicKey: String? = nil) {
        self.endpoint = endpoint; self.serverId = serverId; self.tokens = tokens
        self.homeEndpoint = homeEndpoint; self.serverPublicKey = serverPublicKey
        accessExpires = now.addingTimeInterval(TimeInterval(tokens.expiresIn))
    }
}
public struct Track: Codable, Identifiable, Equatable, Sendable {
    public let id: Int
    public let title: String
    public let artist: String
    public let album: String
    public let artwork: String?
}
public struct CatalogPage: Codable, Sendable {
    public let total: Int?
    public let revision: String
    public let items: [Track]
    public let nextOffset: Int?
}
public struct PlaybackState: Codable, Sendable {
    public let partial: Bool
    public let currentTrackId: Int?
    public let positionSeconds: Double
    public let serverTime: Double
    public let queueRevision: Int
    public let ownerDeviceId: String?
    public let ownerEpoch: Int
    public let intent: Int
    public let playing: Bool
    public let observed: String
    public let seekTargetSeconds: Double
    public let seekIntent: Int
    public let playId: Int
    public var upcomingTrackIds: [Int]?
    public var repeatMode: String?
    public var queueControlsAvailable: Bool?
    public var context: PlaybackContext?
    public var durationSeconds: Double?
    public var output: String?

    public static func merging(_ update: Self, into current: Self?) -> Self {
        guard let current else { return update }
        guard update.ownerEpoch >= current.ownerEpoch, update.queueRevision >= current.queueRevision else { return current }
        guard update.intent >= current.intent, update.serverTime >= current.serverTime else { return current }
        var merged = update
        if update.partial {
            merged.context = update.context ?? current.context
            merged.repeatMode = update.repeatMode ?? current.repeatMode
            merged.queueControlsAvailable = update.queueControlsAvailable ?? current.queueControlsAvailable
        }
        if update.partial, update.currentTrackId == current.currentTrackId,
           update.ownerEpoch == current.ownerEpoch { merged.upcomingTrackIds = current.upcomingTrackIds }
        return merged
    }
}
public struct PlaybackCommand: Encodable, Sendable {
    public var action: String
    public var deviceId: String
    public var deviceName: String?
    public var expectedRevision: Int?
    public var ownerEpoch: Int?
    public var ids: [Int]?
    public var source: LibrarySource?
    public var trackId: Int?
    public var shuffle: Bool?
    public var mode: String?
    public var expectedCurrent: Int?
    public var expectedNext: Int?
    public var transitionId: String?
    public var automatic: Bool?
    public var position: Double?
    public var duration: Double?
    public var intent: Int?
    public var reportSeq: Int?
    public var observed: String?
    public var profile: String?
    public var quality: String?
    public var output: String?
    public var playing: Bool?
    public var index: Int?
    public var from: Int?
    public var to: Int?
    public var buffer: BufferReport?
    public init(action: String, deviceId: String) { self.action = action; self.deviceId = deviceId }
}
public struct MediaItem: Codable, Sendable {
    public let trackId: Int
    public let sourceRevision: String?
    public let profile: String
    // Strings deliberately tolerate new server states.
    public let state: String
    public let url: String?
    public let urlExpires: Int?
    public let sizeBytes: Int?
    public let mimeType: String?
}
public struct Manifest: Codable, Sendable {
    public let ownerEpoch: Int
    public let queueRevision: Int
    public let items: [MediaItem]
    public let retryAfterSeconds: Int
}
public enum APIError: Error, LocalizedError, Sendable {
    case invalidEndpoint, serverChanged, unsupportedServer, signInRequired, invalidResponse
    case rejected(status: Int, code: String, message: String)
    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint: "Enter a secure HTTPS server address."
        case .serverChanged: "This address identifies a different server. Sign in explicitly to enroll it."
        case .unsupportedServer: "This server needs the native playback update."
        case .signInRequired: "Please sign in again."
        case .invalidResponse: "The server returned an unexpected response."
        case let .rejected(_, _, message): message
        }
    }
}
public struct Endpoint: Sendable {
    public let url: URL
    public init(_ text: String) throws {
        guard let parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/", let url = parts.url else { throw APIError.invalidEndpoint }
        self.url = url
    }
    public func mediaURL(_ path: String) throws -> URL {
        guard path.hasPrefix("/api/v1/stream/"), let candidate = URL(string: path, relativeTo: url)?.absoluteURL,
              candidate.scheme == url.scheme, candidate.host == url.host, candidate.port == url.port,
              candidate.user == nil, candidate.password == nil, candidate.fragment == nil,
              candidate.standardized.path.hasPrefix("/api/v1/stream/") else { throw APIError.invalidEndpoint }
        return candidate
    }
}

public struct PlaybackContext: Codable, Sendable { public let kind: String; public let name: String }
public struct QueueEntry: Codable, Identifiable, Sendable {
    public let index: Int
    public let track: Track
    public var id: Int { index }
}
public struct QueuePage: Codable, Sendable {
    public let revision: Int
    public let total: Int
    public let items: [QueueEntry]
    public let nextOffset: Int?
}
public struct BufferEntry: Codable, Sendable, Identifiable {
    public let id: Int
    public var loadedBytes: Int
    public var totalBytes: Int
    public var state: String
    public init(id: Int, loadedBytes: Int, totalBytes: Int, state: String) {
        self.id = id; self.loadedBytes = loadedBytes; self.totalBytes = totalBytes; self.state = state
    }
}
public struct BufferReport: Encodable, Sendable {
    public let state = "active"
    public let storage = "disk"
    public let workerVersion = "ios-cache-v1"
    public let limitBytes = 512 * 1024 * 1024
    public var items: [BufferEntry]
    public var playerSeconds: Int
    public let sampleAgeSeconds = 0
    public init(items: [BufferEntry], playerSeconds: Int) { self.items = items; self.playerSeconds = playerSeconds }
}

/// URLSession uses NSURLErrorCancelled, while Swift tasks use CancellationError.
public enum RequestCancellation {
    public static func matches(_ error: any Error) -> Bool {
        if error is CancellationError { return true }
        let value = error as NSError
        return value.domain == NSURLErrorDomain && value.code == NSURLErrorCancelled
    }
}
