import XCTest
@testable import ArrifyCore

final class SelectedOutputNamesTests: XCTestCase {
    func testUsesNamesNotTransportAndPreservesAllSelectedPorts() {
        XCTAssertEqual(SelectedOutputNames.display([(name: "Office", fallback: "AirPlay"), (name: "Kitchen", fallback: "AirPlay")]), "Office, Kitchen")
        XCTAssertEqual(SelectedOutputNames.display([(name: "Rob’s headphones", fallback: "Bluetooth")]), "Rob’s headphones")
    }
    func testGenericRouteDoesNotInventGroupMembers() {
        XCTAssertEqual(SelectedOutputNames.display([(name: "AirPlay", fallback: "AirPlay")]), "AirPlay")
        XCTAssertEqual(SelectedOutputNames.display([(name: " \n", fallback: "Bluetooth")]), "Bluetooth")
        XCTAssertEqual(SelectedOutputNames.display([]), "Audio output")
    }
    func testDoesNotRewriteOrTruncateReceiverNames() {
        let name = "AirPlay · " + String(repeating: "Living Room ", count: 12)
        XCTAssertEqual(SelectedOutputNames.display([(name: name, fallback: "AirPlay")]), name.trimmingCharacters(in: .whitespacesAndNewlines))
        XCTAssertEqual(SelectedOutputNames.display([(name: "Office", fallback: "AirPlay"), (name: "Office", fallback: "AirPlay")]), "Office, Office")
    }
}
