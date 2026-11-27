import Foundation

/// A playback clock reaching an estimated duration is not an end-of-item event.
public struct PlaybackTimeline: Equatable, Sendable {
    public let position: Double
    public let duration: Double?
    public let remaining: Double?

    public init(position: Double, duration: Double, completed: Bool = false) {
        self.position = position.isFinite ? max(0, position) : 0
        guard duration.isFinite, duration > 0,
              completed || self.position < duration else {
            self.duration = nil
            remaining = nil
            return
        }
        self.duration = duration
        remaining = completed ? 0 : max(0, duration - self.position)
    }

    public var progress: Double {
        guard let duration else { return 0 }
        return min(1, max(0, position / duration))
    }

    /// A positive fraction of a second still has audio left to play.
    public var remainingDisplaySeconds: Double? { remaining?.rounded(.up) }
}
