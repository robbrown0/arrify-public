import Foundation

/// Survives app relaunches within the same server ownership epoch.
/// A wall-clock floor also upgrades clients whose earlier sequence wasn't persisted.
public struct HeartbeatSequence: Sendable {
    private var value: Int
    public init(previous: Int = 0) { value = max(0, previous) }
    public mutating func next(at date: Date = Date()) -> Int {
        value = max(value + 1, Int(date.timeIntervalSince1970 * 1000))
        return value
    }
}
