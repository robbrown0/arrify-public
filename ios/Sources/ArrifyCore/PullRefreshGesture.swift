/// A layout offset alone is not a refresh gesture. Only a released user drag
/// can start work; bouncing, programmatic scrolling and cancellation cannot.
public struct PullRefreshGesture: Sendable {
    public enum Phase: Sendable { case idle, tracking, dragging, settling, programmatic }
    public private(set) var isArmed = false
    public private(set) var isRefreshing = false
    private var phase: Phase = .idle
    public init() {}

    @discardableResult public mutating func phaseChanged(to next: Phase) -> Bool {
        let released = phase == .dragging && (next == .settling || next == .idle)
        let start = released && isArmed && !isRefreshing
        phase = next
        if next != .dragging { isArmed = false }
        if start { isRefreshing = true }
        return start
    }
    public mutating func offsetChanged(_ offset: Double) {
        isArmed = phase == .dragging && !isRefreshing && offset.isFinite && offset < -72
    }
    public mutating func finish() { isRefreshing = false; isArmed = false }
    public mutating func reset() { self = Self() }
}
