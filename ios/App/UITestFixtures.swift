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
    private var owner: String?
    private var playing = false
    private var repeatMode = "off"
    private let album = #"{"id":"aaaaaaaaaaaaaaaaaaaaaaaa","kind":"albums","name":"M!ssundaztood","subtitle":"P!nk","section":"","artwork":null,"track_count":2}"#
    private let station = #"{"id":"sirius-80son8","kind":"channels","name":"80s on 8","subtitle":"","section":"channels","artwork":null,"track_count":2}"#
    private let mix = #"{"id":"mood-feel-good","kind":"collections","name":"Feel Good Favorites","subtitle":"Familiar favorites","section":"moods","artwork":null,"track_count":2}"#
    private let songs = #"[{"id":1,"title":"Just Like a Pill","artist":"P!nk","album":"M!ssundaztood"},{"id":2,"title":"Try","artist":"P!nk","album":"The Truth About Love"}]"#
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func parameter(_ name: String) -> String { query.first { $0.name == name }?.value ?? "" }
        let value: String
        if path.hasSuffix("capabilities") {
            value = #"{"server_id":"fixture","features":["rotating_refresh","native_media_manifest_v1","native_catalog_v1","native_playback_v1","native_library_v1"]}"#
        } else if path.hasSuffix("sessions") {
            value = #"{"access_token":"fixture","refresh_token":"fixture","expires_in":900,"refresh_expires_in":3600,"session_id":"fixture"}"#
        } else if path.hasSuffix("/library") {
            let stations = (1...12).map { n in station.replacingOccurrences(of: "sirius-80son8", with: "station-\(n)").replacingOccurrences(of: "80s on 8", with: n == 1 ? "80s on 8" : "Station \(n)") }.joined(separator: ",")
            value = "{\"items\":[\(stations),\(mix)],\"moods\":[{\"id\":\"feel-good\",\"name\":\"Feel Good\",\"description\":\"Sunshine, soul, and familiar favorites.\",\"color\":\"#244632\",\"slugs\":[\"mood-feel-good\"]}],\"tracks\":2,\"albums\":1,\"artists\":1}"
        } else if path.hasSuffix("/library/detail") {
            let item = parameter("kind") == "channels" ? station : parameter("kind") == "collections" ? mix : album
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
        return HTTPResult(status: 200, data: Data(value.utf8))
    }
}
#endif
