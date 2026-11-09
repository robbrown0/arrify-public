import Foundation

public struct LibrarySource: Codable, Hashable, Sendable {
    public let kind: String
    public let id: String
    public init(kind: String, id: String) { self.kind = kind; self.id = id }
}
public struct LibraryEntry: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let kind: String
    public let name: String
    public let subtitle: String
    public let section: String
    public let artwork: String?
    public let trackCount: Int
    public var source: LibrarySource { LibrarySource(kind: kind, id: id) }
    public var identity: String { kind + ":" + id }
}
public struct MoodGroup: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let description: String
    public let color: String
    public let slugs: [String]
}
public struct LibraryOverview: Codable, Sendable {
    public let items: [LibraryEntry]
    public let moods: [MoodGroup]
    public let tracks: Int
    public let albums: Int
    public let artists: Int
}
public struct EntityPage: Codable, Sendable {
    public let items: [LibraryEntry]
    public let revision: String
    public let total: Int
    public let nextOffset: Int?
}
public struct LibraryDetail: Codable, Sendable {
    public let item: LibraryEntry
    public let items: [Track]
    public let revision: String
    public let total: Int
    public let nextOffset: Int?
}

public struct FavoriteSongs: Codable, Sendable { public let trackIds: [Int] }
