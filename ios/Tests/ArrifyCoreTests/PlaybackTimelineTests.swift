import XCTest
@testable import ArrifyCore

final class PlaybackTimelineTests: XCTestCase {
    func testPlaybackPastEstimatedEndDoesNotClaimFinished() {
        let timeline = PlaybackTimeline(position: 203, duration: 196)
        XCTAssertEqual(timeline.position, 203)
        XCTAssertNil(timeline.duration)
        XCTAssertNil(timeline.remaining)
        XCTAssertEqual(timeline.progress, 0)
    }

    func testReachingEstimateStillNeedsActualCompletion() {
        XCTAssertNil(PlaybackTimeline(position: 203, duration: 203).remaining)
        let ended = PlaybackTimeline(position: 203, duration: 203, completed: true)
        XCTAssertEqual(ended.remaining, 0)
        XCTAssertEqual(ended.progress, 1)
    }

    func testFinalFractionIsNotDisplayedAsZeroSeconds() {
        let timeline = PlaybackTimeline(position: 202.8, duration: 203)
        XCTAssertEqual(timeline.remainingDisplaySeconds, 1)
        XCTAssertEqual(timeline.duration, 203)
        XCTAssertEqual(timeline.progress, 202.8 / 203, accuracy: 0.00001)
    }

    func testValidRemainingAndUnknownOrInvalidLengths() {
        XCTAssertEqual(PlaybackTimeline(position: 100, duration: 220).remaining, 120)
        for duration in [0, -1, Double.nan, Double.infinity] {
            let timeline = PlaybackTimeline(position: 10, duration: duration)
            XCTAssertNil(timeline.duration)
            XCTAssertNil(timeline.remaining)
        }
        XCTAssertEqual(PlaybackTimeline(position: -.infinity, duration: 200).position, 0)
    }
}
