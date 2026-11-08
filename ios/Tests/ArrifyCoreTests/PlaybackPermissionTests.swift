import XCTest
@testable import ArrifyCore
final class PlaybackPermissionTests: XCTestCase {
    func testRestoredSessionRequiresUserIntent() {
        let permission = PlaybackPermission()
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testInterruptionResumesOnlyWithSystemConsent() {
        var permission = PlaybackPermission(); permission.userPlay()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
        permission.interruptionEnded(shouldResume: false)
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
        permission.userPlay()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        permission.interruptionEnded(shouldResume: true)
        XCTAssertTrue(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testExplicitPauseDuringCallWins() {
        var permission = PlaybackPermission(); permission.userPlay()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        permission.userPause()
        permission.interruptionEnded(shouldResume: true)
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testExplicitPlayRecoversWhenInterruptionEndNeverArrives() {
        var permission = PlaybackPermission(); permission.userPlay()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        permission.userPause() // CarPlay disconnects while the app is suspended.
        permission.userPlay() // A shared server command alone cannot clear the interruption.
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
        permission.userPlayAfterSessionActivation()
        XCTAssertFalse(permission.interrupted)
        XCTAssertTrue(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testDisconnectAndDelayedInterruptionEndCannotUndoExplicitPause() {
        var permission = PlaybackPermission(); permission.userPlay()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        permission.userPlayAfterSessionActivation()
        permission.userPause()
        permission.interruptionEnded(shouldResume: true)
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testFreshInterruptionAfterRecoveryStillBlocksPlayback() {
        var permission = PlaybackPermission(); permission.userPlayAfterSessionActivation()
        permission.interruptionBegan(wasPlaying: true, ownsOutput: true)
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: true))
    }
    func testOwnershipAndReadinessFencePlayback() {
        var permission = PlaybackPermission(); permission.userPlay()
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: false, serverPlaying: true, mediaReady: true))
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: true, mediaReady: false))
        XCTAssertFalse(permission.allowsPlayback(ownsOutput: true, serverPlaying: false, mediaReady: true))
    }
}

extension PlaybackPermissionTests {
    func testHeartbeatSequenceSurvivesRelaunchAndClockRollback() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        var sequence = HeartbeatSequence()
        let first = sequence.next(at: now)
        XCTAssertGreaterThan(first, 245) // Previously accepted by build 1/2 before restart.
        let second = sequence.next(at: now)
        XCTAssertGreaterThan(second, first)
        var restored = HeartbeatSequence(previous: second)
        XCTAssertGreaterThan(restored.next(at: now.addingTimeInterval(-3600)), second)
    }
}
