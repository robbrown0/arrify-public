import ActivityKit
import Foundation

struct PlaybackActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        let title: String
        let artist: String
        let destination: String
        let playing: Bool
        let updatedAt: Double
    }
    let owner: String
}
