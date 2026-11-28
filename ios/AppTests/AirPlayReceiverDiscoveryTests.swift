import XCTest
@testable import Arrify

final class AirPlayReceiverDiscoveryTests: XCTestCase {
    func testSameRoomNameAndSharedGroupDoNotCollapseSeparateReceivers() {
        var state = AirPlayDiscoveryState()
        let token = state.begin()
        let bedroomSpeaker = service("Bedroom", id: "speaker", group: "shared")
        let bedroomTV = service("Bedroom", id: "tv", group: "shared")
        let livingRoom = service("Living Room (2)", id: "living", group: "shared")
        XCTAssertTrue(state.update(type: "_airplay._tcp", generation: token,
                                   services: [bedroomTV, livingRoom, bedroomSpeaker]))
        XCTAssertEqual(state.services.count, 3)
        XCTAssertEqual(Set(state.services.compactMap { $0.properties["deviceid"] }), ["speaker", "tv", "living"])
        XCTAssertEqual(state.services.filter { $0.name == "Bedroom" }.count, 2)
        XCTAssertTrue(state.services.contains { $0.name == "Living Room (2)" })
    }

    func testStoppedAndPreviousCaptureCallbacksCannotRestoreClearedNames() {
        var state = AirPlayDiscoveryState()
        let first = state.begin()
        state.update(type: "_airplay._tcp", generation: first, services: [service("Old", id: "old")])
        state.stop()
        XCTAssertFalse(state.update(type: "_airplay._tcp", generation: first, services: [service("Late", id: "late")]))
        XCTAssertEqual(state.services.first?.name, "Old", "Stopping retains the captured evidence")
        state.clear()
        let second = state.begin()
        XCTAssertFalse(state.update(type: "_airplay._tcp", generation: first, services: [service("Stale", id: "stale")]))
        XCTAssertTrue(state.services.isEmpty)
        XCTAssertTrue(state.update(type: "_airplay._tcp", generation: second, services: [service("New", id: "new")]))
        state.clear()
        XCTAssertTrue(state.services.isEmpty)
        XCTAssertTrue(state.statuses.isEmpty)
        XCTAssertFalse(state.running)
    }

    func testDiscoveryReplacesRemovedServicesAndKeepsAirPlayAndRAOPIndependent() {
        var state = AirPlayDiscoveryState()
        let token = state.begin()
        state.update(type: "_airplay._tcp", generation: token, services: [service("Bedroom", id: "speaker")])
        state.update(type: "_raop._tcp", generation: token,
                     services: [AirPlayReceiverService(name: "001122334455@Bedroom", type: "_raop._tcp", domain: "local.", properties: [:])])
        XCTAssertEqual(state.services.count, 2)
        state.update(type: "_airplay._tcp", generation: token, services: [])
        XCTAssertEqual(state.services.map(\.type), ["_raop._tcp"])
        XCTAssertFalse(state.update(type: "_raop._tcp", generation: token, services: state.services))
    }

    func testCaptureBoundsServiceRecordsAndExcludesUnrelatedTXTFields() {
        var state = AirPlayDiscoveryState()
        let token = state.begin()
        state.update(type: "_airplay._tcp", generation: token, services: (0..<200).map { service("Receiver \($0)", id: "\($0)") })
        XCTAssertEqual(state.services.count, 128)
        let receiver = AirPlayReceiverService(name: "Room", type: "_airplay._tcp", domain: "local.",
            properties: ["deviceid": "id", "model": "model", "password": "secret", "url": "http://example.test", "title": "song"])
        XCTAssertEqual(receiver.properties, ["deviceid": "id", "model": "model"])
    }

    private func service(_ name: String, id: String, group: String = "") -> AirPlayReceiverService {
        AirPlayReceiverService(name: name, type: "_airplay._tcp", domain: "local.", properties: ["deviceid": id, "gid": group])
    }
}
