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
