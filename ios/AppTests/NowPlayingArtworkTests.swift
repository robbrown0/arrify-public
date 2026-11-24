import XCTest
import UIKit
import ArrifyCore
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer
@testable import Arrify

final class NowPlayingArtworkTests: XCTestCase {
    @MainActor func testRevokedSessionLeavesAppAndLoginRestoresOtherDeviceState() async throws {
        let transport = AuthRevocationTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        let model = AppModel(api: api)
        await model.restore()
        await model.login(username: "fixture", password: "fixture")
        XCTAssertTrue(model.signedIn)
        await transport.setRevoked(true)
        _ = try? await api.library()
        XCTAssertFalse(model.signedIn)
        XCTAssertNotNil(model.error)
        XCTAssertNil(model.playback.state)
        await transport.setRevoked(false)
        await model.login(username: "fixture", password: "fixture")
        await model.playback.reconcile()
        XCTAssertTrue(model.signedIn)
        XCTAssertNil(model.error)
        XCTAssertNotNil(model.playback.state)
        XCTAssertFalse(model.playback.ownsOutput)
        model.playback.stop()
    }

    func testCatalogSectionTitlesFollowUnicodeCasefoldOrder() {
        let expected: [(String, String)] = [
            ("Sail", "S"), ("ßeta", "S"), ("Sun", "S"),
            ("Kale", "K"), ("Kite", "K"), ("Knot", "K"),
            ("ﬂower", "F"), ("ſong", "S"), ("İdea", "I"),
            ("I\u{0307}dea", "I"), ("ıdea", "#"), ("Élan", "#"),
            ("東京", "#"), ("1 Song", "#"), ("", "#")
        ]
        for (title, section) in expected {
            XCTAssertEqual(CatalogSectionTitle.initial(title), section, title)
        }
        // Server casefold ordering must not create S/#/S or K/#/K runs,
        // which previously gave separate sections the same SwiftUI identity.
        for ordered in [["Sail", "ßeta", "Sun"], ["Kale", "Kite", "Knot"]] {
            XCTAssertEqual(Set(ordered.map(CatalogSectionTitle.initial)).count, 1)
        }
    }

    func testSavedAddressesSurviveCredentialRemovalAndCanBeClearedExplicitly() throws {
        let name = "SavedServerAddressesTests-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let data = Data(#"{"endpoint":"https://public.invalid","homeEndpoint":"https://home.invalid","serverId":"test","tokens":{"accessToken":"a","refreshToken":"r","expiresIn":60,"refreshExpiresIn":600,"sessionId":"s"},"accessExpires":0}"#.utf8)
        var credential = try JSONDecoder().decode(Credential.self, from: data)
        SavedServerAddresses.remember(credential, defaults: defaults)
        // A new defaults instance simulates a subsequent launch after reauthentication.
        let restored = try XCTUnwrap(UserDefaults(suiteName: name)?.dictionary(forKey: "savedServerAddresses"))
        XCTAssertEqual(restored["home"] as? String, "https://home.invalid")
        XCTAssertNil(restored["tokens"])
        credential.homeEndpoint = nil
        SavedServerAddresses.remember(credential, defaults: defaults)
        XCTAssertEqual(defaults.dictionary(forKey: "savedServerAddresses")?["home"] as? String, "")
    }

    @MainActor func testExplicitResumeClaimsInactiveOutputAfterFreshState() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let audio = TestAudioSession()
        let coordinator = PlaybackCoordinator(api: api, audioSession: audio)
        await transport.setOwner("other-device")
        await transport.setOwnerActive(false)
        await coordinator.reconcile()
        XCTAssertFalse(coordinator.needsTakeover)
        XCTAssertFalse(coordinator.showsPauseControl)
        XCTAssertEqual(audio.activationAttempts, 0)
        await coordinator.send("resume")
        XCTAssertTrue(coordinator.ownsOutput)
        XCTAssertEqual(audio.activationAttempts, 1)
        coordinator.stop()
    }

    @MainActor func testFreshActiveOwnerBlocksImplicitResumeWithoutActivatingAudio() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let audio = TestAudioSession()
        let coordinator = PlaybackCoordinator(api: api, audioSession: audio)
        await transport.setOwner("other-device")
        await transport.setOwnerActive(false)
        await coordinator.reconcile()
        await transport.setOwnerActive(true)
        await coordinator.send("resume")
        XCTAssertFalse(coordinator.ownsOutput)
        XCTAssertTrue(coordinator.needsTakeover)
        XCTAssertEqual(audio.activationAttempts, 0)
        XCTAssertNotNil(coordinator.message)
        coordinator.stop()
    }

    @MainActor func testRestoredOwnerDoesNotPublishSystemPlaybackOrAutoplay() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let coordinator = PlaybackCoordinator(api: api)
        await transport.setOwner(coordinator.deviceId)
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        await coordinator.reconcile()
        // Ownership restoration must not autoplay; artwork decoding/disk I/O is
        // asynchronous and is not a two-second performance contract on CI.
        for _ in 0..<200 where coordinator.artwork == nil {
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
    @MainActor func testRemoteControlsDoNotActivateOrTakeOverLocalAudio() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let audio = TestAudioSession()
        let coordinator = PlaybackCoordinator(api: api, audioSession: audio)
        await transport.setOwner("web-device")
        await coordinator.reconcile()
        await coordinator.send("next")
        XCTAssertTrue(coordinator.hasOtherOutput)
        XCTAssertEqual(audio.activationAttempts, 0)
        XCTAssertNil(coordinator.player.currentItem)
        let commands = await transport.commands
        XCTAssertEqual(commands, ["next"])
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
    private(set) var lockScreenRequests = 0
    private(set) var artworkRequests = 0
    private var owner = ""
    private var ownerActive: Bool?
    func setOwnerActive(_ value: Bool) { ownerActive = value }
    private var networkFailure = false
    func setNetworkFailure(_ value: Bool) { networkFailure = value }
    private var current = 1
    private(set) var waitingForMetadata = false
    private var metadataContinuation: CheckedContinuation<Void, Never>?
    func advanceWithDelayedMetadata() { current = 2 }
    func releaseMetadata() { metadataContinuation?.resume(); metadataContinuation = nil }
    private(set) var commands: [String] = []
    private let fallback = UITestTransport()
    func setOwner(_ value: String) { owner = value }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/local-presence") || path.hasSuffix("/live-activity") { lockScreenRequests += 1 }
        if path.hasSuffix("/native/playback") {
            if networkFailure { throw URLError(.networkConnectionLost) }
            if request.httpMethod == "POST", let body = request.httpBody,
               let value = try JSONSerialization.jsonObject(with: body) as? [String: Any],
               let action = value["action"] as? String {
                if value["takeover_if_inactive"] as? Bool == true, owner != value["device_id"] as? String {
                    if ownerActive == true { throw APIError.rejected(status: 409, code: "playback_changed", message: "Choose Play here") }
                    if ownerActive == false { owner = value["device_id"] as? String ?? owner }
                }
                commands.append(action)
            }
            var value: [String: Any] = ["partial": false, "current_track_id": current,
                "position_seconds": 30, "server_time": 1, "queue_revision": 1,
                "owner_device_id": owner, "owner_epoch": 1, "intent": 1,
                "playing": commands.last != "pause", "observed": "playing", "seek_target_seconds": 30,
                "seek_intent": 0, "play_id": 1, "upcoming_track_ids": [2]]
            if let ownerActive { value["owner_active"] = ownerActive }
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: value))
        }
        if path.hasSuffix("/manifest") {
            return HTTPResult(status: 200, data: Data(#"{"owner_epoch":1,"queue_revision":1,"retry_after_seconds":5,"items":[{"track_id":1,"profile":"original","state":"queued"}]}"#.utf8))
        }
        if path.hasSuffix("/tracks/2") {
            waitingForMetadata = true
            await withCheckedContinuation { metadataContinuation = $0 }
            return HTTPResult(status: 200, data: Data(#"{"track":{"id":2,"title":"Next song","artist":"Test artist","album":"Test album"}}"#.utf8))
        }
        if path.hasSuffix("/tracks/1") {
            return HTTPResult(status: 200, data: Data(#"{"track":{"id":1,"title":"Test song","artist":"Test artist","album":"Test album"}}"#.utf8))
        }
        if path.hasSuffix("/artwork/1") {
            artworkRequests += 1
            // Opaque one-pixel PNG; no network or account data enters the test.
            return HTTPResult(status: 200, data: Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")!)
        }
        return try await fallback.send(request)
    }
}

@MainActor private final class TestAudioSession: PlaybackAudioSession {
    var unavailable = false
    var activationAttempts = 0
    func activate() async throws {
        activationAttempts += 1
        if unavailable { throw URLError(.cannotConnectToHost) }
    }
    func deactivate() async throws {}
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
    @MainActor func testArtworkResizesToReceiverBoundsWithoutDistortingAspect() {
        let image = UIGraphicsImageRenderer(size: CGSize(width: 200, height: 100)).image { context in
            UIColor.green.setFill(); context.fill(CGRect(x: 0, y: 0, width: 200, height: 100))
        }
        let artwork = NowPlayingArtwork.make(image: image)
        XCTAssertEqual(artwork.image(at: CGSize(width: 80, height: 80))?.size, CGSize(width: 80, height: 40))
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


extension NowPlayingArtworkTests {
    @MainActor func testCacheOnlyReportsVerifiedCompleteFilesAndClearsOnLogout() async throws {
        let api = APIClient(transport: RestoredOwnerTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let cache = AudioCache(download: { url in
            let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try Data([1,2,3,4]).write(to: file)
            return (file, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: Data(#"{"owner_epoch":1,"queue_revision":1,"retry_after_seconds":2,"items":[{"track_id":1,"source_revision":"v1","profile":"original","state":"ready","size_bytes":4,"mime_type":"audio/flac","url":"/api/v1/stream/1?native_grant=fixture"}]}"#.utf8))
        await cache.fill(manifest, api: api)
        XCTAssertEqual(cache.items.first?.state, "ready")
        XCTAssertEqual(cache.downloadedBytes, 4)
        let file = try XCTUnwrap(cache.file(for: manifest.items[0]))
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        cache.reset()
        XCTAssertEqual(cache.downloadedBytes, 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
    }
    @MainActor func testIncompleteDownloadIsNotReportedAsCached() async throws {
        let api = APIClient(transport: RestoredOwnerTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let cache = AudioCache(download: { url in
            let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try Data([1]).write(to: file)
            return (file, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: Data(#"{"owner_epoch":1,"queue_revision":1,"retry_after_seconds":2,"items":[{"track_id":1,"source_revision":"v1","profile":"original","state":"ready","size_bytes":4,"url":"/api/v1/stream/1?native_grant=fixture"}]}"#.utf8))
        await cache.fill(manifest, api: api)
        XCTAssertEqual(cache.items.first?.state, "retrying")
        XCTAssertEqual(cache.downloadedBytes, 0)
        XCTAssertNil(cache.file(for: manifest.items[0]))
        cache.reset()
    }
}

extension NowPlayingArtworkTests {
    @MainActor func testEndOfSongRecoversLostTransitionResponseWithoutSkippingAgain() async throws {
        let transport = TransitionTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let coordinator = PlaybackCoordinator(api: api, audioSession: TestAudioSession())
        await coordinator.send("resume")
        for _ in 0..<100 where coordinator.player.currentItem == nil {
            try await Task.sleep(for: .milliseconds(20))
        }
        let ended = try XCTUnwrap(coordinator.player.currentItem)
        // The server advances, but its response is lost. Retrying must reconcile
        // that committed play ID instead of sending another next command.
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: ended)
        for _ in 0..<150 where coordinator.state?.currentTrackId != 2 {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(coordinator.state?.currentTrackId, 2)
        let transitions = await transport.transitions
        XCTAssertEqual(transitions, 1)
        coordinator.stop()
    }
}

private actor TransitionTransport: HTTPTransport {
    private let fallback = UITestTransport()
    private var owner = ""
    private var current = 1
    private(set) var transitions = 0
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        var value: [String: Any]
        if path.hasSuffix("/native/playback") {
            if request.httpMethod == "POST", let data = request.httpBody,
               let body = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                owner = body["device_id"] as? String ?? owner
                if body["action"] as? String == "next" {
                    transitions += 1; current = 2
                    throw URLError(.networkConnectionLost)
                }
            }
            value = ["partial": false, "current_track_id": current,
                "position_seconds": 0, "server_time": 1, "queue_revision": current,
                "owner_device_id": owner.isEmpty ? NSNull() : owner as Any, "owner_active": !owner.isEmpty, "owner_epoch": 1, "intent": current,
                "playing": true, "observed": "playing", "seek_target_seconds": 0,
                "seek_intent": 0, "play_id": current, "upcoming_track_ids": current == 1 ? [2] : []]
        } else if path.hasSuffix("/manifest") {
            value = ["owner_epoch": 1, "queue_revision": current, "retry_after_seconds": 5,
                "items": [["track_id": current, "profile": "original", "state": "ready",
                           "url": "/api/v1/stream/\(current)?native_grant=fixture"]]]
        } else { return try await fallback.send(request) }
        return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: value))
    }
}


extension NowPlayingArtworkTests {
    @MainActor func testRemoteTrackChangeKeepsPlayerMountedWhileMetadataLoads() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let coordinator = PlaybackCoordinator(api: api, audioSession: TestAudioSession())
        await transport.setOwner("remote-device")
        await coordinator.reconcile(report: false)
        for _ in 0..<200 where coordinator.track?.id != 1 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(coordinator.track?.id, 1)
        await transport.advanceWithDelayedMetadata()
        let update = Task { await coordinator.reconcile(report: false) }
        for _ in 0..<100 {
            if await transport.waitingForMetadata { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        let waiting = await transport.waitingForMetadata
        XCTAssertTrue(waiting)
        XCTAssertEqual(coordinator.track?.id, 1, "The mounted player must survive metadata latency")
        await transport.releaseMetadata()
        await update.value
        for _ in 0..<200 where coordinator.track?.id != 2 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(coordinator.track?.id, 2)
        coordinator.stop()
    }
}

extension NowPlayingArtworkTests {
    @MainActor func testTransportRecoveryClearsBannerWithoutSkippingSong() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://example.test", username: "listener", password: "fixture", deviceName: "iPhone")
        let coordinator = PlaybackCoordinator(api: api, audioSession: TestAudioSession())
        await transport.setOwner("other-device")
        await transport.setNetworkFailure(true)
        await coordinator.reconcile(report: false)
        XCTAssertFalse(coordinator.connectionInterrupted)
        await coordinator.reconcile(report: false)
        XCTAssertTrue(coordinator.connectionInterrupted)
        coordinator.message = "An unrelated playback error"
        await transport.setNetworkFailure(false)
        await coordinator.reconcile(report: false)
        XCTAssertFalse(coordinator.connectionInterrupted)
        for _ in 0..<200 where coordinator.track?.id != 1 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(coordinator.track?.id, 1)
        XCTAssertEqual(coordinator.message, "An unrelated playback error")
        coordinator.stop()
    }
}

extension NowPlayingArtworkTests {
    @MainActor func testOutputRefreshPreservesReportedNamesWithoutChangingPlayback() {
        let session = NamedOutputSession()
        let coordinator = PlaybackCoordinator(api: APIClient(transport: RestoredOwnerTransport(), store: UITestStore()), audioSession: session)
        XCTAssertEqual(coordinator.outputName, "Office")
        session.name = "AirPlay · Living Room, Kitchen"
        coordinator.outputSelectionChanged()
        XCTAssertEqual(coordinator.outputName, session.name)
        XCTAssertTrue(coordinator.routeDescription.hasSuffix(session.name))
        XCTAssertEqual(session.activations, 0)
        XCTAssertNil(coordinator.player.currentItem)
        XCTAssertFalse(coordinator.player.allowsExternalPlayback)
        coordinator.stop()
    }
    @MainActor func testRouteDiagnosticsAreOptInAndClearOnSignOut() {
        let coordinator = PlaybackCoordinator(api: APIClient(transport: RestoredOwnerTransport(), store: UITestStore()), audioSession: NamedOutputSession())
        coordinator.refreshOutput()
        XCTAssertTrue(coordinator.routeDiagnostics.records.isEmpty)
        coordinator.routeDiagnostics.start()
        coordinator.refreshOutput(trigger: "test-capture")
        XCTAssertEqual(coordinator.routeDiagnostics.records.count, 1)
        XCTAssertTrue(coordinator.routeDiagnostics.exportText.contains("playerAllowsExternalPlayback"))
        coordinator.stop()
        XCTAssertFalse(coordinator.routeDiagnostics.capturing)
        XCTAssertTrue(coordinator.routeDiagnostics.records.isEmpty)
    }
}

@MainActor private final class NamedOutputSession: PlaybackAudioSession {
    var name = "Office"
    var activations = 0
    var outputName: String { name }
    func activate() async throws { activations += 1 }
    func deactivate() async throws {}
}

extension NowPlayingArtworkTests {
    @MainActor func testShuffleHighlightBelongsOnlyToTheCurrentCollection() throws {
        let coordinator = PlaybackCoordinator(api: APIClient(transport: UITestTransport(), store: UITestStore()), audioSession: UITestAudioSession())
        defer { coordinator.stop() }
        let data = Data(#"{"partial":false,"current_track_id":1,"position_seconds":0,"server_time":1,"queue_revision":1,"owner_device_id":"other","owner_epoch":1,"intent":1,"playing":false,"observed":"paused","seek_target_seconds":0,"seek_intent":0,"play_id":1,"shuffle":true,"context":{"kind":"albums","id":"one","name":"Album"}}"#.utf8)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        coordinator.state = try decoder.decode(PlaybackState.self, from: data)
        XCTAssertTrue(coordinator.sourceShowsShuffle(LibrarySource(kind: "albums", id: "one")), "Paused shuffle stays visible")
        XCTAssertFalse(coordinator.sourceShowsShuffle(LibrarySource(kind: "albums", id: "two")))
        XCTAssertFalse(coordinator.sourceShowsShuffle(LibrarySource(kind: "playlists", id: "one")))
        coordinator.state = nil
        XCTAssertFalse(coordinator.sourceShowsShuffle(LibrarySource(kind: "albums", id: "one")))
    }
    func testTrackRowStatusFollowsIdentityAndPlayback() {
        XCTAssertNil(TrackPlaybackStatus.resolve(trackID: 1, currentTrackID: nil, playing: true, wantsPlayback: true))
        XCTAssertNil(TrackPlaybackStatus.resolve(trackID: 2, currentTrackID: 1, playing: true, wantsPlayback: true))
        XCTAssertEqual(TrackPlaybackStatus.resolve(trackID: 1, currentTrackID: 1, playing: true, wantsPlayback: true), .playing)
        XCTAssertEqual(TrackPlaybackStatus.resolve(trackID: 1, currentTrackID: 1, playing: false, wantsPlayback: false), .paused)
        XCTAssertEqual(TrackPlaybackStatus.resolve(trackID: 1, currentTrackID: 1, playing: false, wantsPlayback: true), .preparing)
        // After Next, the previous song loses its marker and the new song gains it.
        XCTAssertNil(TrackPlaybackStatus.resolve(trackID: 1, currentTrackID: 2, playing: true, wantsPlayback: true))
        XCTAssertEqual(TrackPlaybackStatus.resolve(trackID: 2, currentTrackID: 2, playing: true, wantsPlayback: true), .playing)
    }
    @MainActor func testNativeVolumeGeometryFollowsContainerWithoutTrackOffsets() {
        let volume = MPVolumeView(frame: CGRect(x: 0, y: 0, width: 130, height: 44))
        let container = VolumeControlContainer(volumeView: volume)
        for width in [130.0, 300.0, 240.0] {
            container.frame = CGRect(x: 0, y: 0, width: width, height: 44)
            container.layoutIfNeeded()
            XCTAssertTrue(volume.superview === container)
            XCTAssertEqual(volume.frame, container.bounds)
            container.setNeedsLayout()
            container.layoutIfNeeded()
            XCTAssertEqual(volume.frame, container.bounds)
        }
    }
    @MainActor func testTabletVolumeUsesNativeHeightCenteredInControlRow() {
        // A real UISlider supplies asymmetric geometry even when the simulator
        // omits MPVolumeView's system slider. This is a layout regression test,
        // not a claim of physical-device visual verification.
        let volume = LayoutReportingVolumeView(frame: CGRect(x: 0, y: 0, width: 130, height: 44))
        let container = VolumeControlContainer(volumeView: volume, centersNativeHeight: true)
        container.frame = CGRect(x: 0, y: 0, width: 130, height: 44)
        container.layoutIfNeeded()
        for child in volume.subviews { child.isHidden = true }
        let slider = UISlider(frame: CGRect(x: 0, y: -8, width: 130, height: 30))
        volume.addSubview(slider)
        for width in [130.0, 240.0, 130.0] {
            container.frame.size.width = width
            container.setNeedsLayout()
            container.layoutIfNeeded()
            for y in [-8.0, 0.0, 6.0] {
                slider.frame = CGRect(x: 0, y: y, width: width, height: 30)
                let originalFrame = slider.frame
                // Native layout may change after the parent has already laid out.
                volume.didLayout?()
                let track = slider.trackRect(forBounds: slider.bounds)
                XCTAssertEqual(slider.convert(track, to: container).midY, container.bounds.midY, accuracy: 0.5)
                XCTAssertEqual(slider.frame, originalFrame, "Never reposition the native slider itself")
                let settledTransform = volume.transform
                volume.didLayout?()
                XCTAssertEqual(volume.transform, settledTransform, "Repeated layout must not accumulate an offset")
                let point = CGPoint(x: track.midX, y: track.midY)
                let displayedPoint = slider.convert(point, to: container)
                XCTAssertEqual(container.convert(displayedPoint, to: slider).y, point.y, accuracy: 0.01)
            }
        }
    }
    func testLegacyActivityStateStillDecodesWithoutArtwork() throws {
        let data = Data(#"{"title":"Song","artist":"Artist","destination":"Office","playing":true,"updatedAt":100}"#.utf8)
        let state = try JSONDecoder().decode(PlaybackActivityAttributes.ContentState.self, from: data)
        XCTAssertNil(state.artwork)
        XCTAssertEqual(state.title, "Song")
    }
}


extension NowPlayingArtworkTests {
    @MainActor func testArtworkRevisitsAndConcurrentViewsReuseOneRequest() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let disk = ArtworkDiskCache(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let artwork = ArtworkStore(api: api, disk: disk)
        let path = "/api/v1/artwork/1"
        let first = Task { await artwork.image(path) }
        let second = Task { await artwork.image(path) }
        let original = await first.value
        let concurrent = await second.value
        XCTAssertNotNil(original)
        XCTAssertTrue(original === concurrent)
        XCTAssertTrue(artwork.cachedImage(path) === original, "New views can render synchronously from cache")
        let revisit = await artwork.image(path)
        XCTAssertTrue(revisit === original)
        let count = await transport.artworkRequests
        XCTAssertEqual(count, 1)
        artwork.clear()
        XCTAssertNil(artwork.cachedImage(path), "Sign-out clears private artwork")
        _ = await artwork.image(path)
        let afterClear = await transport.artworkRequests
        XCTAssertEqual(afterClear, 1, "Sign-out clears memory but preserves same-account disk thumbnails")
        await artwork.clearPersistentCache()
        _ = await artwork.image(path)
        let afterDiskClear = await transport.artworkRequests
        XCTAssertEqual(afterDiskClear, 2)
        await artwork.clearPersistentCache()
    }
    func testPersistentArtworkIsIsolatedAndExpires() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let disk = ArtworkDiskCache(root: root)
        let bytes = Data("thumbnail".utf8)
        await disk.write(bytes, scope: "server/account-a", path: "/cover")
        let restored = await ArtworkDiskCache(root: root).read(scope: "server/account-a", path: "/cover")
        XCTAssertEqual(restored, bytes)
        let other = await disk.read(scope: "server/account-b", path: "/cover")
        XCTAssertNil(other)
        let files = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
        try FileManager.default.setAttributes([.creationDate: Date().addingTimeInterval(-90000)], ofItemAtPath: files[0].path)
        let expired = await disk.read(scope: "server/account-a", path: "/cover")
        XCTAssertNil(expired)
        await disk.clear()
    }
    @MainActor func testCancelledNavigationPreservesLibraryWithoutError() async throws {
        let transport = CancelledNavigationTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let library = LibraryStore(api: api)
        let browser = LibraryBrowser(api: api)
        await library.refresh(); await browser.load()
        XCTAssertNotNil(library.overview); XCTAssertEqual(browser.tracks.count, 2)
        await transport.fail(with: .cancelled)
        await library.refresh(); await browser.load()
        XCTAssertNil(library.error); XCTAssertNil(browser.error)
        XCTAssertNotNil(library.overview); XCTAssertEqual(browser.tracks.count, 2)
        XCTAssertFalse(library.loading); XCTAssertFalse(browser.loading)
        await transport.fail(with: .notConnectedToInternet)
        await library.refresh(); await browser.load()
        XCTAssertNotNil(library.error); XCTAssertNotNil(browser.error)
    }
    @MainActor func testCancelledPlaybackCommandDoesNotShowCancelledBanner() async throws {
        let transport = CancelledNavigationTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let coordinator = PlaybackCoordinator(api: api, audioSession: TestAudioSession())
        await transport.fail(with: .cancelled)
        await coordinator.send("pause")
        XCTAssertNil(coordinator.message)
        await transport.fail(with: .notConnectedToInternet)
        await coordinator.send("pause")
        XCTAssertNotNil(coordinator.message)
        coordinator.stop()
    }
}

private actor CancelledNavigationTransport: HTTPTransport {
    private let fallback = UITestTransport()
    private var failure: URLError.Code?
    func fail(with code: URLError.Code) { failure = code }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        if let failure, ["/api/v1/native/library", "/api/v1/native/catalog"].contains(request.url!.path) || (request.url!.path == "/api/v1/native/playback" && request.httpMethod == "POST") { throw URLError(failure) }
        return try await fallback.send(request)
    }
}

final class PlaybackDiagnosticsTests: XCTestCase {
    func testLogSurvivesWriterRecreationAndRotates() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let writer = DiagnosticWriter(directory: directory, limit: 1024)
        for n in 0..<100 {
            writer.record("test-event-\(n)")
            if n % 20 == 0 { _ = try await writer.export() }
        }
        let export = try await writer.export()
        let data = try Data(contentsOf: export)
        XCTAssertLessThan(data.count, 2200)
        XCTAssertTrue(String(decoding: data, as: UTF8.self).contains("test-event-99"))
        let reopened = DiagnosticWriter(directory: directory, limit: 1024)
        let recovered = try await reopened.export()
        XCTAssertEqual(try Data(contentsOf: recovered), data)
        await reopened.clear()
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    @MainActor func testBackgroundRecorderCapturesBlockedMainThread() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let writer = DiagnosticWriter(directory: directory)
        writer.monitor(enabled: true)
        // Export is a queue barrier: start the timer before blocking the main thread.
        _ = try await writer.export()
        // Deliberately block only this test process, below the system watchdog limit.
        blockMainThreadForDiagnosticTest(directory: directory)
        writer.monitor(enabled: false)
        let export = try await writer.export()
        XCTAssertTrue(try String(contentsOf: export, encoding: .utf8).contains("main-unresponsive"))
        await writer.clear()
    }
    @MainActor private func blockMainThreadForDiagnosticTest(directory: URL) {
        XCTAssertTrue(Thread.isMainThread)
        let deadline = ProcessInfo.processInfo.systemUptime + 10
        let log = directory.appendingPathComponent("current.txt")
        while ProcessInfo.processInfo.systemUptime < deadline {
            if (try? String(contentsOf: log, encoding: .utf8))?.contains("main-unresponsive") == true { return }
            Thread.sleep(forTimeInterval: 0.05)
        }
    }

    @MainActor func testRealLocalAudioAdvancesWithDiagnosticRecording() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let audioURL = directory.appendingPathComponent("test.caf")
        let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44100 * 8)!
        buffer.frameLength = buffer.frameCapacity
        // Quiet generated tone; no copyrighted music or production server access.
        for n in 0..<Int(buffer.frameLength) { buffer.floatChannelData![0][n] = Float(sin(Double(n) * 2 * .pi * 220 / 44100)) * 0.001 }
        do { let file = try AVAudioFile(forWriting: audioURL, settings: format.settings); try file.write(from: buffer) }
        let writer = DiagnosticWriter(directory: directory.appendingPathComponent("logs"))
        writer.monitor(enabled: true)
        let player = AVQueuePlayer(items: [AVPlayerItem(url: audioURL)])
        player.allowsExternalPlayback = false
        player.isMuted = true
        player.play()
        for _ in 0..<100 {
            if player.currentTime().seconds >= 2 { break }
            try await Task.sleep(for: .milliseconds(100))
        }
        XCTAssertGreaterThanOrEqual(player.currentTime().seconds, 2, "Actual AVPlayer playback must advance")
        player.pause(); player.removeAllItems(); writer.monitor(enabled: false)
        _ = try await writer.export()
        await writer.clear()
        try FileManager.default.removeItem(at: directory)
    }
}


extension NowPlayingArtworkTests {
    @MainActor func testRemoteLockScreenDoesNotAttemptRegistrationWithoutWiFi() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        await transport.setOwner("other-device")
        let state = try await api.state()
        let track = try await api.track(1)
        let remote = RemotePlaybackActivity(api: api)
        remote.automatic = true
        await remote.networkChanged(wifi: false)
        for _ in 0..<3 {
            await remote.update(state: state, track: track, destination: "Other device", ownsOutput: false)
            await remote.start(state: state, track: track, destination: "Other device")
        }
        let requests = await transport.lockScreenRequests
        XCTAssertEqual(requests, 0)
        XCTAssertFalse(remote.active)
        XCTAssertNil(remote.error)
        XCTAssertTrue(remote.status.contains("Wi-Fi"))
        await remote.networkChanged(wifi: true)
        XCTAssertTrue(remote.wifiAvailable)
        await remote.networkChanged(wifi: false)
        XCTAssertFalse(remote.wifiAvailable)
    }
}


extension NowPlayingArtworkTests {
    @MainActor func testPlayerArtworkUsesPersistentFullSizeCoverWithoutNetwork() async throws {
        let transport = RestoredOwnerTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let cacheScope = await api.artworkCacheScope()
        let scope = try XCTUnwrap(cacheScope)
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let disk = ArtworkDiskCache(root: root)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        let cover = UIGraphicsImageRenderer(size: CGSize(width: 1200, height: 1200), format: format).image { context in
            UIColor.blue.setFill(); context.fill(CGRect(x: 0, y: 0, width: 1200, height: 1200))
        }
        await disk.write(try XCTUnwrap(cover.jpegData(compressionQuality: 0.9)), scope: scope, path: "/api/v1/artwork/1")
        let store = ArtworkStore(api: api, disk: disk)
        let restored = await store.image("/api/v1/artwork/1", pixels: 2048)
        XCTAssertEqual(restored?.size.width, 1200)
        let requests = await transport.artworkRequests
        XCTAssertEqual(requests, 0, "Player-sized artwork must load from disk without fetching again")
        await store.clearPersistentCache()
    }
}

extension NowPlayingArtworkTests {
    @MainActor func testLibraryWindowPrependsWithoutDuplicatesAndKeepsRowIdentity() async throws {
        let api = APIClient(transport: WindowCatalogTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let browser = LibraryBrowser(api: api)
        await browser.load(around: 90)
        XCTAssertEqual(browser.tracks[25].id, 90)
        XCTAssertEqual(browser.startOffset, 64)
        let identity = browser.trackRows.first { $0.track.id == 90 }?.id
        await browser.load(previous: true)
        XCTAssertEqual(browser.tracks.count, 100)
        XCTAssertEqual(Set(browser.tracks.map(\.id)).count, 100)
        XCTAssertEqual(browser.trackRows.first { $0.track.id == 90 }?.id, identity)
        await browser.load(more: true)
        XCTAssertEqual(browser.tracks.last?.id, 120)
        await browser.load(previous: true)
        XCTAssertEqual(browser.tracks.map(\.id), Array(1...120))
        XCTAssertNil(browser.previousOffset)
    }
    @MainActor func testReturningToPreviousQueryAfterCancellationReloadsClearedWindow() async throws {
        let transport = WindowCatalogTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let browser = LibraryBrowser(api: api)
        await browser.load(query: "new")
        XCTAssertEqual(browser.tracks.map(\.id), [120])
        XCTAssertNotNil(browser.updated)

        browser.resetSearch()
        XCTAssertTrue(browser.tracks.isEmpty)
        XCTAssertNil(browser.updated, "Cleared results must not satisfy the view's recent-success reuse guard")
        let intermediate = Task { await browser.load(query: "slow") }
        // Wait for B to enter its delayed transport, rather than relying on a
        // scheduler-specific pause before returning to the completed query A.
        for _ in 0..<100 {
            if await transport.slowRequests > 0 { break }
            try await Task.sleep(for: .milliseconds(5))
        }
        let requests = await transport.slowRequests
        XCTAssertEqual(requests, 1)
        intermediate.cancel()
        browser.resetSearch()
        XCTAssertNil(browser.updated)
        await browser.load(query: "new")
        await intermediate.value
        XCTAssertEqual(browser.tracks.map(\.id), [120])
        XCTAssertNotNil(browser.updated)
        XCTAssertNil(browser.error)
        XCTAssertFalse(browser.loading)
    }
    @MainActor func testSlowPreviousSearchCannotReplaceNewSelection() async throws {
        let api = APIClient(transport: WindowCatalogTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let browser = LibraryBrowser(api: api)
        let old = Task { await browser.load(query: "slow") }
        try await Task.sleep(for: .milliseconds(30))
        browser.resetSearch()
        await browser.load(query: "new")
        await old.value
        XCTAssertEqual(browser.tracks.map(\.id), [120])
        XCTAssertFalse(browser.loading)
    }
}
private actor WindowCatalogTransport: HTTPTransport {
    private(set) var slowRequests = 0
    private let fallback = UITestTransport()
    func send(_ request: URLRequest) async throws -> HTTPResult {
        guard request.url!.path.hasSuffix("/catalog") else { return try await fallback.send(request) }
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems ?? []
        func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
        if value("q") == "slow" { slowRequests += 1; try await Task.sleep(for: .milliseconds(200)) }
        let offset = value("around").flatMap(Int.init).map { max(0, $0 - 26) } ?? Int(value("offset") ?? "0") ?? 0
        let ids = value("q") == "new" ? [120] : Array((offset + 1)...min(120, offset + 50))
        let items = ids.map { ["id": $0, "title": "Song \($0)", "artist": "Artist", "album": "Album"] as [String: Any] }
        let result: [String: Any] = ["items": items, "total": 120, "offset": offset,
            "previous_offset": offset > 0 ? max(0, offset - 50) as Any : NSNull(),
            "next_offset": offset + items.count < 120 ? offset + items.count as Any : NSNull(), "revision": "fixture"]
        return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: result))
    }
}

extension NowPlayingArtworkTests {
    @MainActor func testCatalogRetryKeepsFailedPageForEveryKind() async throws {
        for kind in ["tracks", "albums", "artists"] {
            let transport = FailedPageTransport()
            let api = APIClient(transport: transport, store: UITestStore())
            try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
            let browser = LibraryBrowser(api: api, kind: kind)
            await browser.load(query: "kept")
            XCTAssertNotNil(browser.error)
            await browser.retry()
            XCTAssertNil(browser.error)
            XCTAssertEqual(browser.nextOffset, 1)
            await browser.load(query: "kept", more: true)
            XCTAssertNotNil(browser.error)
            XCTAssertEqual(kind == "tracks" ? browser.tracks.count : browser.entries.count, 1)
            await browser.retry()
            XCTAssertNil(browser.error)
            XCTAssertNil(browser.nextOffset)
            XCTAssertEqual(kind == "tracks" ? browser.tracks.count : browser.entries.count, 2)
            let requests = await transport.offsets
            XCTAssertEqual(requests, [0, 0, 1, 1])
            let queries = await transport.queries
            XCTAssertEqual(queries, Array(repeating: "kept", count: 4))
        }
    }
}
private actor FailedPageTransport: HTTPTransport {
    private let fallback = UITestTransport()
    private var failed: Set<Int> = []
    private(set) var offsets: [Int] = []
    private(set) var queries: [String] = []
    func send(_ request: URLRequest) async throws -> HTTPResult {
        guard request.url!.path.hasSuffix("/catalog") else { return try await fallback.send(request) }
        let query = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems ?? []
        func value(_ name: String) -> String? { query.first { $0.name == name }?.value }
        let offset = Int(value("offset") ?? "0") ?? 0
        offsets.append(offset); queries.append(value("q") ?? "")
        if failed.insert(offset).inserted {
            return HTTPResult(status: 429, data: Data(#"{"error":{"code":"rate_limited","message":"Retry shortly."}}"#.utf8))
        }
        let kind = value("kind") ?? "tracks"
        let row: [String: Any] = kind == "tracks"
            ? ["id": offset + 1, "title": "Song", "artist": "Artist", "album": "Album"]
            : ["id": String(offset + 1), "kind": kind, "name": "Item", "subtitle": "", "section": "", "track_count": 1]
        let page: [String: Any] = ["items": [row], "total": 2, "offset": offset, "revision": "fixture", "next_offset": offset == 0 ? 1 as Any : NSNull()]
        return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: page))
    }
}

private actor AuthRevocationTransport: HTTPTransport {
    let fixture = UITestTransport()
    var revoked = false
    func setRevoked(_ value: Bool) { revoked = value }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        if revoked && (request.value(forHTTPHeaderField: "Authorization") != nil || request.url?.path.hasSuffix("refresh") == true) {
            return HTTPResult(status: 401, data: Data(#"{"error":{"code":"invalid_refresh","message":"Sign in again."}}"#.utf8))
        }
        return try await fixture.send(request)
    }
}
