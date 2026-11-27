import Foundation

public struct AudiobookProgress: Codable, Sendable {
    public var currentTime: Double?
    public var lastUpdate: Double?
    public var isFinished: Bool?
    public init(currentTime: Double, lastUpdate: Double, isFinished: Bool) {
        self.currentTime = currentTime; self.lastUpdate = lastUpdate; self.isFinished = isFinished
    }
}
public struct AudiobookChapter: Codable, Sendable, Identifiable {
    public let title: String
    public let start: Double
    public let end: Double
    public var id: Double { start }
}
public struct AudiobookPart: Codable, Sendable, Identifiable {
    public let index: Int
    public let start: Double
    public let duration: Double
    public let url: String?
    public let sizeBytes: Int64?
    public let mime: String?
    public var id: Int { index }
}
public struct AudiobookBookmark: Codable, Sendable, Identifiable {
    public let time: Double
    public let title: String
    public var id: Double { time }
}
public struct Audiobook: Codable, Sendable, Identifiable {
    public let id: String
    public let libraryId: String
    public let title: String
    public let author: String
    public let narrator: String
    public let series: String
    public let authors: [AudiobookPerson]?
    public let seriesEntries: [AudiobookSeries]?
    public let publishedDate: String?
    public let publishedYear: String?
    public let addedAt: Double?
    public let duration: Double
    public let cover: String
    public let description: String
    public let sourceRevision: String?
    public let parts: [AudiobookPart]?
    public let chapters: [AudiobookChapter]?
    public let bookmarks: [AudiobookBookmark]?
    public var progress: AudiobookProgress?
    public var remaining: Double { max(0, duration - (progress?.currentTime ?? 0)) }
}
public struct AudiobookPreferences: Codable, Sendable, Equatable {
    public var visible = true
    public var continuous = false
    public var speed = 1.0
    public var backSeconds = 30
    public var forwardSeconds = 30
    public init() {}
}
public struct AudiobookStatus: Decodable, Sendable {
    public let enabled: Bool
    public let preferences: AudiobookPreferences
}
public struct AudiobookLibrary: Decodable, Sendable {
    public struct Library: Decodable, Sendable, Identifiable { public let id: String; public let name: String }
    public let libraries: [Library]
    public let libraryId: String?
    public let items: [Audiobook]
    public let total: Int
    public let nextPage: Int?
}
public struct AudiobookManifest: Codable, Sendable {
    public let book: Audiobook
    public let lease: String
    public let position: Double
    public let revision: Int
    public let preferences: AudiobookPreferences
    public let musicEpoch: Int?
    public let musicIntent: Int?
}
public struct AudiobookQueueEntry: Codable, Sendable, Identifiable {
    public let id: String
    public let title: String
    public let author: String
    public let cover: String
}
public struct AudiobookState: Decodable, Sendable {
    public let bookId: String?
    public let lease: String?
    public let deviceId: String?
    public let active: Bool
    public let position: Double?
    public let revision: Int?
    public let queue: [AudiobookQueueEntry]?
    public let preferences: AudiobookPreferences
}
public struct AudiobookReceipt: Decodable, Sendable {
    public let saved: Bool
    public let pending: Bool
    public let active: Bool?
    public let remoteStamp: Double?
}
/// Durable device journal. Sequence numbers order rewinds as well as forward seeks.
public struct AudiobookCheckpoint: Codable, Sendable {
    public let bookId: String
    public let sourceRevision: String
    public var sessionId: String
    public let deviceId: String
    public var baseRemoteStamp: Double
    public var seq: Int
    public var position: Double
    public var finished: Bool
    public init(book: Audiobook, deviceId: String) {
        bookId = book.id; sourceRevision = book.sourceRevision ?? ""
        sessionId = UUID().uuidString.lowercased(); self.deviceId = deviceId
        baseRemoteStamp = book.progress?.lastUpdate ?? 0; seq = 0
        position = book.progress?.currentTime ?? 0; finished = false
    }
}
public struct AudiobookJournalEntry: Codable, Sendable {
    public var checkpoint: AudiobookCheckpoint
    public var lease: String?
    public var playing: Bool
    public var dirty: Bool
    public init(checkpoint: AudiobookCheckpoint, lease: String?, playing: Bool, dirty: Bool) {
        self.checkpoint = checkpoint; self.lease = lease; self.playing = playing; self.dirty = dirty
    }
    public func needsCheckpoint(position: Double, playing: Bool, finished: Bool) -> Bool {
        checkpoint.seq == 0 || abs(position-checkpoint.position) > 0.001 || self.playing != playing || checkpoint.finished != finished
    }
    /// A delayed response cannot erase a later seek or a newly opened session.
    @discardableResult public mutating func acknowledge(session: String, sequence: Int, remoteStamp: Double?) -> Bool {
        guard checkpoint.sessionId == session, sequence <= checkpoint.seq else { return false }
        if let remoteStamp { checkpoint.baseRemoteStamp = remoteStamp }
        if checkpoint.seq == sequence { dirty = false }
        return !dirty
    }
}
public enum AudiobookTimeline {
    public static func chapter(_ chapters: [AudiobookChapter], at position: Double) -> AudiobookChapter? {
        chapters.last { $0.start <= position }
    }
    public static func part(_ parts: [AudiobookPart], at position: Double) -> AudiobookPart? {
        parts.last { $0.start <= position } ?? parts.first
    }
    public static func time(_ seconds: Double) -> String {
        guard seconds.isFinite else { return "0:00" }
        let value = max(0, Int(seconds)), hours = value / 3600
        return hours > 0 ? String(format: "%d:%02d:%02d", hours, value / 60 % 60, value % 60) : String(format: "%d:%02d", value / 60, value % 60)
    }
}

/// Coalesce concurrent callers without allowing a previous account's completion
/// to release a newer operation. The owner must still fence its result writes.
@MainActor public final class AudiobookSyncGate {
    private var task: Task<Void, Never>?
    private var identity = UUID()
    public init() {}
    public func reset() { identity = UUID(); task?.cancel(); task = nil }
    @discardableResult public func run(_ operation: @escaping @MainActor () async -> Void) async -> Bool {
        if let task { await task.value; return false }
        let token = UUID(); identity = token
        let current = Task { @MainActor in await operation() }
        task = current
        await current.value
        if identity == token { task = nil }
        return true
    }
}

/// A clean downloaded checkpoint can briefly consult the server without making
/// local playback depend on an unresponsive read or its cancellation handler.
@MainActor public enum AudiobookReadDeadline {
    public static func load<Value: Sendable>(timeout: Duration = .seconds(1),
        wait: @escaping @MainActor (Duration) async throws -> Void = { try await Task.sleep(for:$0) },
        operation: @escaping @Sendable () async throws -> Value) async throws -> Value {
        let race = AudiobookReadRace<Value>()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                race.install(continuation)
                race.attach(Task { @MainActor in
                    do { try Task.checkCancellation(); race.resolve(.success(try await operation())) }
                    catch { race.resolve(.failure(error)) }
                })
                race.attach(Task { @MainActor in
                    do { try await wait(timeout); race.resolve(.failure(URLError(.timedOut))) }
                    catch { race.resolve(.failure(error)) }
                })
            }
        } onCancel: {
            Task { @MainActor in race.resolve(.failure(CancellationError())) }
        }
    }
}
@MainActor private final class AudiobookReadRace<Value: Sendable> {
    private var continuation: CheckedContinuation<Value, any Error>?
    private var result: Result<Value, any Error>?
    private var workers: [Task<Void,Never>] = []
    func install(_ continuation: CheckedContinuation<Value, any Error>) {
        if let result { continuation.resume(with:result) } else { self.continuation = continuation }
    }
    func attach(_ task: Task<Void,Never>) {
        if result != nil { task.cancel() } else { workers.append(task) }
    }
    func resolve(_ result: Result<Value, any Error>) {
        guard self.result == nil else { return }
        self.result = result; continuation?.resume(with:result); continuation = nil
        let tasks = workers; workers = []
        for task in tasks { task.cancel() }
    }
}

public enum AudiobookPlaybackIssue: Error, Equatable, Sendable {
    case offline, temporary, authentication, missing, invalidResponse, progressPending, conflict(String), media
    public static func classify(_ error: any Error) -> Self {
        if let issue = error as? Self { return issue }
        if case APIError.signInRequired = error { return .authentication }
        if case APIError.retryDeferred = error { return .temporary }
        if case APIError.rejected(let status, _, let message) = error {
            switch status {
            case 401, 403: return .authentication
            case 404, 410: return .missing
            case 409: return .conflict(message)
            case 429, 500...599: return .temporary
            default: return .invalidResponse
            }
        }
        if let url = error as? URLError {
            switch url.code {
            case .notConnectedToInternet, .networkConnectionLost, .cannotConnectToHost, .cannotFindHost, .timedOut: return .offline
            default: return .media
            }
        }
        return .invalidResponse
    }
    public var category: String {
        switch self { case .offline: "network"; case .temporary: "server"; case .authentication: "authentication"
        case .missing: "unavailable"; case .invalidResponse: "invalid-response"; case .progressPending: "progress-pending"
        case .conflict: "conflict"; case .media: "media" }
    }
    public var message: String {
        switch self {
        case .offline: "The server couldn’t be reached. Your place is saved. Try again, or play a downloaded copy."
        case .temporary: "The audiobook server is temporarily unavailable. Your place is saved. Try again shortly."
        case .authentication: "This book needs access to the audiobook library. Check your connection or library access."
        case .missing: "This edition is no longer available on the server. Your listening position is saved."
        case .invalidResponse: "The server returned an unexpected audiobook response. Your place is saved. Try again."
        case .progressPending: "Your listening position is still syncing. Your place is saved. Try again shortly."
        case .conflict(let text): text
        case .media: "This audio could not be opened. Your place is saved. Try again or download the book."
        }
    }
}

public enum AudiobookResume {
    /// Rewinds are intentional: choose a history, never the greatest position.
    public static func position(remote: AudiobookProgress?, local: Double?, localStamp: Double?, pending: AudiobookJournalEntry?, restart: Bool, finished: Bool) -> Double {
        if restart { return 0 }
        if let pending, pending.dirty { return pending.checkpoint.finished ? 0 : pending.checkpoint.position }
        if let remote, (remote.lastUpdate ?? 0) > (localStamp ?? 0) { return remote.isFinished == true ? 0 : remote.currentTime ?? 0 }
        if finished { return 0 }
        return local ?? remote?.currentTime ?? 0
    }
}

public struct AudiobookPerson: Codable, Sendable, Identifiable { public let id: String; public let name: String }
public struct AudiobookSeries: Codable, Sendable, Identifiable {
    public let id: String
    public let name: String
    public let sequence: String?
    public var label: String { sequence.map { "Book " + $0 + " · " + name } ?? name }
}
public struct AudiobookGroups: Decodable, Sendable {
    public let items: [AudiobookPerson]
    public let libraryId: String
    public let total: Int
    public let nextPage: Int?
}
