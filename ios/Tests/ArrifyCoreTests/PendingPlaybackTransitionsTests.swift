import XCTest
@testable import ArrifyCore

final class PendingPlaybackTransitionsTests: XCTestCase {
    private func acknowledged() throws -> PlaybackState {
        let data = Data("""
        {"partial":false,"current_track_id":2,"position_seconds":0,"server_time":2,"queue_revision":2,"owner_device_id":"device","owner_epoch":1,"intent":2,"playing":true,"observed":"playing","seek_target_seconds":0,"seek_intent":2,"play_id":2,"upcoming_track_ids":[3,4,5]}
        """.utf8)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(PlaybackState.self, from: data)
    }
    private func next(_ current: Int, _ next: Int, revision: Int) -> PlaybackCommand {
        var command = PlaybackCommand(action: "next", deviceId: "device")
        command.ownerEpoch = 1; command.expectedRevision = revision
        command.expectedCurrent = current; command.expectedNext = next; command.automatic = false
        return command
    }
    func testProjectionPreservesLocalPlayAndReplenishesTailInOrder() throws {
        let result = try XCTUnwrap(acknowledged().applyingPendingTransitions([next(2,3,revision:2), next(3,4,revision:3)]))
        XCTAssertEqual(result.currentTrackId,4)
        XCTAssertEqual(result.playId,4)
        XCTAssertEqual(result.queueRevision,4)
        XCTAssertEqual(result.upcomingTrackIds,[5])
    }
    func testProjectionRejectsChangedQueueRevisionOwnerAndUnexpectedSuccessor() throws {
        let state = try acknowledged()
        XCTAssertNil(state.applyingPendingTransitions([next(2,3,revision:3)]))
        XCTAssertNil(state.applyingPendingTransitions([next(2,4,revision:2)]))
        XCTAssertNil(state.applyingPendingTransitions([next(1,3,revision:2)]))
        var command = next(2,3,revision:2); command.ownerEpoch = 2
        XCTAssertNil(state.applyingPendingTransitions([command]))
        command = next(2,3,revision:2); command.action = "pause"
        XCTAssertNil(state.applyingPendingTransitions([command]))
    }
}
