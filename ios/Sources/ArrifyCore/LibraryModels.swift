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
    public init(id: String, kind: String, name: String, subtitle: String = "", section: String = "", artwork: String? = nil, trackCount: Int = 0) {
        self.id = id; self.kind = kind; self.name = name; self.subtitle = subtitle
        self.section = section; self.artwork = artwork; self.trackCount = trackCount
    }
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
    public let homeAlbums: [LibraryEntry]?
    public let recentTracks: [Track]?
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
    public var popularityPending: Bool?
    public let item: LibraryEntry
    public let items: [Track]
    public let revision: String
    public let total: Int
    public let nextOffset: Int?
}

public struct FavoriteSongs: Codable, Sendable { public let trackIds: [Int] }

public struct OriginalAlbum: Decodable, Sendable {
    public struct Album: Decodable, Sendable { public let title: String?; public let name: String?; public let artist: String? }
    public struct Song: Decodable, Sendable { public let title: String; public let artist: String; public let localTrackId: Int? }
    public let album: Album
    public let items: [Song]
}
