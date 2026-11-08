/// Local intent is independent from the server's shared playing state.
/// A restored session cannot autoplay, and system interruptions cannot override a pause.
public struct PlaybackPermission: Sendable {
    public private(set) var enabled = false
    public private(set) var interrupted = false
    public private(set) var needsUserResume = false
    private var resumeAfterInterruption = false
    public init() {}
    public mutating func userPlay() { enabled = true; needsUserResume = false }
    /// A new explicit Play request can recover a missing interruption-ended event.
    /// Call only after the operating system successfully activates the audio session.
    public mutating func userPlayAfterSessionActivation() {
        interrupted = false
        resumeAfterInterruption = false
        userPlay()
    }
    public mutating func userPause() { needsUserResume = true; resumeAfterInterruption = false }
    public mutating func interruptionBegan(wasPlaying: Bool, ownsOutput: Bool) {
        resumeAfterInterruption = wasPlaying && ownsOutput && enabled && !needsUserResume
        interrupted = true; needsUserResume = true
    }
    public mutating func interruptionEnded(shouldResume: Bool) {
        interrupted = false
        if shouldResume && resumeAfterInterruption { needsUserResume = false }
        resumeAfterInterruption = false
    }
    public func allowsPlayback(ownsOutput: Bool, serverPlaying: Bool, mediaReady: Bool) -> Bool {
        enabled && !interrupted && !needsUserResume && ownsOutput && serverPlaying && mediaReady
    }
}
