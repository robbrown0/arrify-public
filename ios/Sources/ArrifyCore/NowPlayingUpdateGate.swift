import Foundation

/// The system extrapolates elapsed time. Only publish semantic changes or a seek,
/// rather than resending artwork on every timer tick and server heartbeat.
public struct NowPlayingUpdateGate: Sendable {
    private var signature: String?
    private var position = 0.0
    private var rate = 0.0
    private var time = 0.0
    public init() {}
    public mutating func shouldPublish(signature: String, position: Double, rate: Double, now: Double) -> Bool {
        let predicted = self.position + max(0, now - time) * self.rate
        guard self.signature != signature || self.rate != rate || abs(predicted - position) > 2 else { return false }
        self.signature = signature; self.position = position; self.rate = rate; time = now
        return true
    }
}
