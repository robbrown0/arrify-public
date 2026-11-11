#if DEBUG
import Foundation
import ArrifyCore

/// Deterministic simulator-only fixtures, excluded from Release/TestFlight builds.
@MainActor struct UITestAudioSession: PlaybackAudioSession {
    func activate() throws {}
    func deactivate() throws {}
}

actor UITestStore: CredentialStore {
    var credential: Credential?
    func load() -> Credential? { credential }
    func save(_ credential: Credential) { self.credential = credential }
    func clear() { credential = nil }
}
actor UITestTransport: HTTPTransport {
    private var owner: String? = ProcessInfo.processInfo.environment["ARRIFY_UI_REMOTE_OWNER"] == "1" ? "other-device" : nil
    private var playing = false
    private var repeatMode = "off"
    private var playlists: [[String: Any]] = []
    private var favoriteIds: Set<Int> = []
    private let album = #"{"id":"aaaaaaaaaaaaaaaaaaaaaaaa","kind":"albums","name":"M!ssundaztood","subtitle":"P!nk","section":"","artwork":null,"track_count":2}"#
    private let station = #"{"id":"sirius-80son8","kind":"channels","name":"80s on 8","subtitle":"","section":"channels","artwork":null,"track_count":2}"#
    private let mix = #"{"id":"mood-feel-good","kind":"collections","name":"Feel Good Favorites","subtitle":"Familiar favorites","section":"moods","artwork":null,"track_count":2}"#
    private let songs = #"[{"id":1,"title":"Just Like a Pill","artist":"P!nk","album":"M!ssundaztood"},{"id":2,"title":"Try","artist":"P!nk","album":"The Truth About Love"}]"#
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func parameter(_ name: String) -> String { query.first { $0.name == name }?.value ?? "" }
        let value: String
        if path.hasSuffix("/local-presence") {
            return HTTPResult(status: 403, data: Data(#"{"error":{"code":"local_network_required","message":"The playing device is not available for local verification."}}"#.utf8))
        } else if path.hasSuffix("capabilities") {
            value = #"{"server_id":"fixture","features":["rotating_refresh","native_media_manifest_v1","native_catalog_v1","native_playback_v1","native_library_v1"]}"#
        } else if path.hasSuffix("sessions") {
            value = #"{"access_token":"fixture","refresh_token":"fixture","expires_in":900,"refresh_expires_in":3600,"session_id":"fixture"}"#
        } else if path.hasSuffix("/favorites") {
            if request.httpMethod == "POST", let body = request.httpBody,
               let values = try JSONSerialization.jsonObject(with: body) as? [String: Any],
               let id = values["track_id"] as? Int, let liked = values["liked"] as? Bool {
                if liked { favoriteIds.insert(id) } else { favoriteIds.remove(id) }
            }
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: ["track_ids": favoriteIds.sorted()]))
        } else if path.hasSuffix("/playlists"), let body = request.httpBody,
                  let input = try JSONSerialization.jsonObject(with: body) as? [String: Any] {
            let item: [String: Any] = ["id": "1", "kind": "playlists", "name": input["name"] ?? "My mix", "subtitle": "", "section": "personal", "track_count": input["add_track_id"] == nil ? 0 : 1]
            playlists = [item]
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: item))
        } else if path.hasSuffix("/library") {
            let stations = (1...12).map { n in station.replacingOccurrences(of: "sirius-80son8", with: "station-\(n)").replacingOccurrences(of: "80s on 8", with: n == 1 ? "80s on 8" : "Station \(n)") }.joined(separator: ",")
            let featured = mix.replacingOccurrences(of: "mood-feel-good", with: "featured-drive").replacingOccurrences(of: "Feel Good Favorites", with: "Driving favourites").replacingOccurrences(of: "\"section\":\"moods\"", with: "\"section\":\"playlists\"")
            let personal = playlists.isEmpty ? "" : "," + String(data: try JSONSerialization.data(withJSONObject: playlists[0]), encoding: .utf8)!
            value = "{\"home_albums\":[\(album)],\"items\":[\(stations),\(mix),\(featured)\(personal)],\"moods\":[{\"id\":\"feel-good\",\"name\":\"Feel Good\",\"description\":\"Sunshine, soul, and familiar favorites.\",\"color\":\"#244632\",\"slugs\":[\"mood-feel-good\"]}],\"tracks\":2,\"albums\":1,\"artists\":1}"
        } else if path.hasSuffix("/library/detail") {
            if parameter("kind") == "favorites" {
                let items = (try JSONSerialization.jsonObject(with: Data(songs.utf8)) as! [[String: Any]]).filter { favoriteIds.contains($0["id"] as! Int) }
                let entry: [String: Any] = ["id":"liked", "kind":"favorites", "name":"Liked songs", "subtitle":"", "section":"personal", "track_count":items.count]
                return HTTPResult(status:200, data: try JSONSerialization.data(withJSONObject: ["item":entry, "items":items, "total":items.count, "revision":"fixture", "next_offset":NSNull()]))
            }
            let personal = playlists.first.flatMap { try? JSONSerialization.data(withJSONObject: $0) }.flatMap { String(data: $0, encoding: .utf8) } ?? album
            let item = parameter("kind") == "playlists" ? personal : parameter("kind") == "channels" ? station : parameter("kind") == "collections" ? mix : album
            value = "{\"item\":\(item),\"items\":\(songs),\"total\":2,\"revision\":\"fixture\",\"next_offset\":null}"
        } else if path.hasSuffix("catalog") {
            let text = parameter("q")
            let matches = text.isEmpty || "pink".contains(text.lowercased())
            let kind = parameter("kind")
            let artist = #"{"id":"bbbbbbbbbbbbbbbbbbbbbbbb","kind":"artists","name":"P!nk","subtitle":"","section":"","artwork":null,"track_count":2}"#
            let items = matches ? (kind == "albums" ? "[\(album)]" : kind == "artists" ? "[\(artist)]" : songs) : "[]"
            value = "{\"revision\":\"fixture\",\"items\":\(items),\"total\":\(matches ? 2 : 0),\"next_offset\":null}"
        } else if path.contains("/artwork/") || path.contains("/artist-artwork/") {
            return HTTPResult(status: 404, data: Data())
        } else if path.hasSuffix("/tracks/1") {
            value = #"{"track":{"id":1,"title":"Just Like a Pill","artist":"P!nk","album":"M!ssundaztood"}}"#
        } else if path.hasSuffix("/tracks/2") {
            value = #"{"track":{"id":2,"title":"Family Portrait","artist":"P!nk","album":"M!ssundaztood"}}"#
        } else if path.hasSuffix("/playback/queue") {
            value = #"{"revision":1,"total":1,"items":[{"index":0,"track":{"id":2,"title":"Family Portrait","artist":"P!nk","album":"M!ssundaztood"}}],"next_offset":null}"#
        } else if path.hasSuffix("/manifest") {
            value = #"{"owner_epoch":1,"queue_revision":1,"retry_after_seconds":5,"items":[{"track_id":1,"profile":"original","state":"queued"}]}"#
        } else {
            if request.httpMethod == "POST", let data = request.httpBody,
               let body = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                owner = body["device_id"] as? String
                if let mode = body["mode"] as? String { repeatMode = mode }
                playing = body["action"] as? String != "pause"
            }
            value = "{\"repeat_mode\":\"\(repeatMode)\",\"queue_controls_available\":true,\"partial\":false,\"current_track_id\":\(owner == nil ? "null" : "1"),\"position_seconds\":0,\"server_time\":1,\"queue_revision\":1,\"owner_device_id\":\(owner.map { "\"\($0)\"" } ?? "null"),\"owner_epoch\":1,\"intent\":1,\"playing\":\(playing),\"observed\":\"paused\",\"seek_target_seconds\":0,\"seek_intent\":0,\"play_id\":1,\"upcoming_track_ids\":[2]}"
        }
        let response = ProcessInfo.processInfo.environment["ARRIFY_UI_LONG_TITLE"] == "1"
            ? value.replacingOccurrences(of: "Just Like a Pill", with: "A Very Long Song Title That Wraps Onto Two Lines in the Expanded Player During Keyboard Restoration")
            : value
        return HTTPResult(status: 200, data: Data(response.utf8))
    }
}
#endif
