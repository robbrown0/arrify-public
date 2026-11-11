import Foundation

public struct BrowseCategory: Decodable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let count: Int
}
public struct BrowseOverview: Decodable, Sendable {
    public let genres: [BrowseCategory]
    public let decades: [BrowseCategory]
    public let indexing: Bool
    public let error: Bool?
}
public struct BrowseFilters: Codable, Hashable, Sendable {
    public var genre: String
    public var decade: String
    public var kind: String
    public init(genre: String = "", decade: String = "", kind: String = "hits") { self.genre = genre; self.decade = decade; self.kind = kind }
    public var source: LibrarySource {
        let data = try! JSONEncoder().encode(self)
        return LibrarySource(kind: "browse", id: String(decoding: data, as: UTF8.self))
    }
}
public struct BrowsePage: Decodable, Sendable {
    public struct Entry: Decodable, Identifiable, Sendable {
        public let id: String
        public let name: String
        public let artist: String?
        public let artwork: String?
        public let tracks: Int
        public func libraryEntry(kind: String) -> LibraryEntry { LibraryEntry(id: id, kind: kind, name: name, subtitle: artist ?? "", artwork: artwork, trackCount: tracks) }
    }
    public let name: String
    public let total: Int
    public let nextOffset: Int
    public let indexing: Bool
    public let genreCounts: [BrowseCategory]
    public var tracks: [Track]?
    public var items: [Entry]?
}
public struct BrowseChart: Decodable, Sendable {
    public struct Entry: Decodable, Identifiable, Sendable {
        public let title: String
        public let artist: String
        public let rank: Int
        public let track: Track?
        public let requestKey: String
        public let requested: Bool
        public var id: String { requestKey }
        enum CodingKeys: String, CodingKey { case title, artist, rank, track, requestKey, requested }
        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            title = try c.decode(String.self, forKey: .title); artist = try c.decode(String.self, forKey: .artist)
            rank = try c.decode(Int.self, forKey: .rank); track = try c.decodeIfPresent(Track.self, forKey: .track)
            requestKey = try c.decode(String.self, forKey: .requestKey)
            if c.contains(.requested), try !c.decodeNil(forKey: .requested) { requested = (try? c.decode(Bool.self, forKey: .requested)) ?? true } else { requested = false }
        }
    }
    public let items: [Entry]
    public let source: String?
    public let fetched: Double?
    public let pending: Bool
    public let stale: Bool
}
public struct AccountRegistration: Decodable, Sendable { public let message: String }
public struct BrowseRequestResult: Decodable, Sendable { public let available: Bool? }
