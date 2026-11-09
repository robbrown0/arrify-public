import XCTest
@testable import ArrifyCore
final class NowPlayingUpdateGateTests: XCTestCase {
    func testHeartbeatsDoNotRepublishArtworkButSeeksPauseAndCoversDo() {
        var gate = NowPlayingUpdateGate()
        XCTAssertTrue(gate.shouldPublish(signature: "track1:cover1", position: 0, rate: 1, now: 10))
        for elapsed in [2.0, 5.0, 30.0, 120.0] {
            XCTAssertFalse(gate.shouldPublish(signature: "track1:cover1", position: elapsed, rate: 1, now: 10 + elapsed))
        }
        XCTAssertTrue(gate.shouldPublish(signature: "track1:cover1", position: 40, rate: 1, now: 131))
        XCTAssertTrue(gate.shouldPublish(signature: "track1:cover1", position: 41, rate: 0, now: 132))
        XCTAssertFalse(gate.shouldPublish(signature: "track1:cover1", position: 41, rate: 0, now: 150))
        XCTAssertTrue(gate.shouldPublish(signature: "track1:cover2", position: 41, rate: 0, now: 151))
    }
}
