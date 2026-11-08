import XCTest
import UIKit
import ArrifyCore
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer
@testable import Arrify

final class NowPlayingArtworkTests: XCTestCase {
    @MainActor func testRestoredOwnerDoesNotPublishSystemPlaybackOrAutoplay() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let coordinator = PlaybackCoordinator(api: api)
        await transport.setOwner(coordinator.deviceId)
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        await coordinator.reconcile()
        for _ in 0..<40 where coordinator.artwork == nil {
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertFalse(coordinator.player.allowsExternalPlayback)
        XCTAssertTrue(coordinator.ownsOutput)
        XCTAssertNotNil(coordinator.track)
        XCTAssertNotNil(coordinator.artwork)
        XCTAssertNil(coordinator.player.currentItem)
        XCTAssertFalse(coordinator.isPlaying)
        XCTAssertNil(MPNowPlayingInfoCenter.default().nowPlayingInfo)
        coordinator.stop()
    }
    @MainActor func testExplicitPlayRecoversFromMissingInterruptionEnd() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let audio = TestAudioSession()
        let coordinator = PlaybackCoordinator(api: api, audioSession: audio)
        await transport.setOwner(coordinator.deviceId)
        await coordinator.reconcile()
        await coordinator.send("resume")
        XCTAssertTrue(coordinator.showsPauseControl)
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.began.rawValue])
        XCTAssertFalse(coordinator.showsPauseControl)
        // No ended notification: only successful explicit activation can release the block.
        audio.unavailable = true
        await coordinator.send("resume")
        XCTAssertFalse(coordinator.showsPauseControl)
        XCTAssertEqual(coordinator.message, "Audio output is unavailable. Tap Play to try again.")
        var commands = await transport.commands
        XCTAssertEqual(commands, ["resume"])
        audio.unavailable = false
        await coordinator.send("resume")
        XCTAssertTrue(coordinator.showsPauseControl)
        commands = await transport.commands
        XCTAssertEqual(commands, ["resume", "resume"])
        XCTAssertEqual(audio.activationAttempts, 3)
        await coordinator.send("pause")
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.ended.rawValue,
                       AVAudioSessionInterruptionOptionKey: AVAudioSession.InterruptionOptions.shouldResume.rawValue])
        XCTAssertFalse(coordinator.showsPauseControl)
        coordinator.stop()
    }
    @MainActor func testSystemCanRequestArtworkFromBackgroundQueue() async {
        let image = UIGraphicsImageRenderer(size: CGSize(width: 32, height: 32)).image { context in
            UIColor.green.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 32, height: 32))
        }
        let artwork = ArtworkRequest(value: NowPlayingArtwork.make(image: image))
        let size = await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                // Exercise MediaPlayer's real callback off the main actor, as iOS does.
                let result = artwork.value.image(at: CGSize(width: 32, height: 32))
                continuation.resume(returning: result?.size)
            }
        }
        XCTAssertEqual(size, image.size)
    }
}
// Test-only transfer of an immutable MediaPlayer object to its requesting queue.
private struct ArtworkRequest: @unchecked Sendable {
    let value: MPMediaItemArtwork
}

private actor RestoredOwnerTransport: HTTPTransport {
    private var owner = ""
    private(set) var commands: [String] = []
    private let fallback = UITestTransport()
    func setOwner(_ value: String) { owner = value }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/native/playback") {
            if request.httpMethod == "POST", let body = request.httpBody,
               let value = try JSONSerialization.jsonObject(with: body) as? [String: Any],
               let action = value["action"] as? String { commands.append(action) }
            let value: [String: Any] = ["partial": false, "current_track_id": 1,
                "position_seconds": 30, "server_time": 1, "queue_revision": 1,
                "owner_device_id": owner, "owner_epoch": 1, "intent": 1,
                "playing": commands.last != "pause", "observed": "playing", "seek_target_seconds": 30,
                "seek_intent": 0, "play_id": 1, "upcoming_track_ids": [2]]
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: value))
        }
        if path.hasSuffix("/manifest") {
            return HTTPResult(status: 200, data: Data(#"{"owner_epoch":1,"queue_revision":1,"retry_after_seconds":5,"items":[{"track_id":1,"profile":"original","state":"queued"}]}"#.utf8))
        }
        if path.hasSuffix("/tracks/1") {
            return HTTPResult(status: 200, data: Data(#"{"track":{"id":1,"title":"Test song","artist":"Test artist","album":"Test album"}}"#.utf8))
        }
        if path.hasSuffix("/artwork/1") {
            // Opaque one-pixel PNG; no network or account data enters the test.
            return HTTPResult(status: 200, data: Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")!)
        }
        return try await fallback.send(request)
    }
}

@MainActor private final class TestAudioSession: PlaybackAudioSession {
    var unavailable = false
    var activationAttempts = 0
    func activate() throws {
        activationAttempts += 1
        if unavailable { throw URLError(.cannotConnectToHost) }
    }
    func deactivate() throws {}
}

extension NowPlayingArtworkTests {
    @MainActor func testAirPlayMetadataEmbedsArtworkAndReplacesOldCover() throws {
        let track = try JSONDecoder().decode(Track.self, from: Data(#"{"id":1,"title":"Title","artist":"Artist","album":"Album"}"#.utf8))
        let jpeg = Data([0xff, 0xd8, 0xff, 0xd9])
        let metadata = ReceiverMetadata.make(track: track, artwork: jpeg)
        XCTAssertEqual(metadata.first { $0.identifier == .commonIdentifierTitle }?.stringValue, "Title")
        XCTAssertEqual(metadata.first { $0.identifier == .commonIdentifierArtist }?.stringValue, "Artist")
        XCTAssertEqual(metadata.first { $0.identifier == .commonIdentifierAlbumName }?.stringValue, "Album")
        XCTAssertEqual(metadata.first { $0.identifier == .commonIdentifierArtwork }?.dataValue, jpeg)
        XCTAssertFalse(ReceiverMetadata.make(track: track, artwork: nil).contains { $0.identifier == .commonIdentifierArtwork })
    }
    @MainActor func testBrowsingAndRefreshPopulateUnfilteredLibrary() async {
        let api = APIClient(transport: UITestTransport(), store: UITestStore())
        try? await api.login(endpoint: "https://fixture.invalid", username: "test", password: "test", deviceName: "test")
        let browser = LibraryBrowser(api: api)
        await browser.load()
        XCTAssertEqual(browser.tracks.count, 2)
        XCTAssertNotNil(browser.updated)
        await browser.load(query: "missing")
        XCTAssertTrue(browser.tracks.isEmpty)
        await browser.load()
        XCTAssertEqual(browser.tracks.count, 2)
        let library = LibraryStore(api: api)
        await library.refresh()
        XCTAssertEqual(library.overview?.moods.first?.id, "feel-good")
        XCTAssertEqual(library.overview?.items.first?.kind, "channels")
        library.clear()
        XCTAssertNil(library.overview)
    }
}
