import ActivityKit
import Foundation

struct PlaybackActivityAttributes: ActivityAttributes, Sendable {
    struct ContentState: Codable, Hashable, Sendable {
        let title: String
        let artist: String
        let destination: String
        let playing: Bool
        let updatedAt: Double
        var artwork: String? = nil
        var trackID: Int? = nil
    }
    let owner: String
    let ownerEpoch: Int
    let sessionScope: String
}
