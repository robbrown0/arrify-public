import ActivityKit
import Foundation

struct PlaybackActivityAttributes: ActivityAttributes, Sendable {
    struct ContentState: Codable, Hashable, Sendable {
        let title: String
        let artist: String
        let destination: String
        let playing: Bool
        let updatedAt: Double
    }
    let owner: String
    let ownerEpoch: Int
    let sessionScope: String
}
