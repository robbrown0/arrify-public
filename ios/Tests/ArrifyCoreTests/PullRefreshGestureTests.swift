import XCTest
@testable import ArrifyCore

final class PullRefreshGestureTests: XCTestCase {
    func testLayoutAndProgrammaticOffsetsNeverArmOrStartRefresh() {
        var gesture = PullRefreshGesture()
        gesture.offsetChanged(-100)
        XCTAssertFalse(gesture.isArmed)
        XCTAssertFalse(gesture.phaseChanged(to: .idle))
        gesture.phaseChanged(to: .programmatic)
        gesture.offsetChanged(-140)
        XCTAssertFalse(gesture.isArmed)
        XCTAssertFalse(gesture.phaseChanged(to: .settling))
        XCTAssertFalse(gesture.isRefreshing)
    }
    func testOnlyReleasedDragStartsOneRefreshUntilCompletion() {
        var gesture = PullRefreshGesture()
        gesture.phaseChanged(to: .tracking)
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-100)
        XCTAssertTrue(gesture.isArmed)
        XCTAssertFalse(gesture.isRefreshing)
        XCTAssertTrue(gesture.phaseChanged(to: .settling))
        XCTAssertFalse(gesture.isArmed)
        XCTAssertTrue(gesture.isRefreshing)
        XCTAssertFalse(gesture.phaseChanged(to: .idle))
        gesture.phaseChanged(to: .tracking)
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-120)
        XCTAssertFalse(gesture.phaseChanged(to: .idle))
        gesture.finish()
        XCTAssertFalse(gesture.isRefreshing)
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-100)
        XCTAssertTrue(gesture.phaseChanged(to: .idle))
    }
    func testRetractedOrCancelledDragAndNavigationLeaveNoStuckIndicator() {
        var gesture = PullRefreshGesture()
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-100)
        gesture.offsetChanged(-20)
        XCTAssertFalse(gesture.isArmed)
        XCTAssertFalse(gesture.phaseChanged(to: .idle))
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-100)
        XCTAssertFalse(gesture.phaseChanged(to: .programmatic))
        XCTAssertFalse(gesture.isArmed)
        gesture.phaseChanged(to: .dragging)
        gesture.offsetChanged(-100)
        XCTAssertTrue(gesture.phaseChanged(to: .idle))
        gesture.reset()
        XCTAssertFalse(gesture.isArmed)
        XCTAssertFalse(gesture.isRefreshing)
        gesture.offsetChanged(-100)
        XCTAssertFalse(gesture.isArmed)
    }
}
