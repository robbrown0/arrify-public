import XCTest
import ArrifyCore
@preconcurrency import AVFoundation
@testable import Arrify

final class PlaybackTransitionTests: XCTestCase {
    @MainActor func fixture(onHistoryRecorded: (@MainActor () -> Void)? = nil) async throws -> (PlaybackCoordinator, CallResumeTransitionTransport) {
        let transport = CallResumeTransitionTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        // Real local silent audio keeps AVQueuePlayer deterministic and offline.
        let audio = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        let format = AVAudioFormat(standardFormatWithSampleRate: 8000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 8000 * 180)!
        buffer.frameLength = buffer.frameCapacity
        buffer.floatChannelData![0].initialize(repeating: 0, count: Int(buffer.frameLength))
        do {
            let file = try AVAudioFile(forWriting: audio, settings: format.settings)
            try file.write(from: buffer)
        }
        let bytes = try Data(contentsOf: audio)
        try FileManager.default.removeItem(at: audio)
        await transport.setSize(bytes.count)
        let cache = AudioCache(download: { url in
            let target = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
            try bytes.write(to: target)
            return (target, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        let coordinator = PlaybackCoordinator(api: api, audioSession: UITestAudioSession(), cache: cache)
        coordinator.onHistoryRecorded = onHistoryRecorded
        await transport.setOwner(coordinator.deviceId)
        let manifest = try await api.manifest(deviceId: coordinator.deviceId, epoch: 1, profile: "original")
        await cache.fill(manifest, api: api)
        await coordinator.send("resume")
        for _ in 0..<100 where coordinator.player.items().count < 2 { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(coordinator.player.items().count, 2)
        return (coordinator, transport)
    }

    @MainActor func testNextItemWaitsForServerConfirmationAndDelayedPollCannotRestoreOldSong() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        XCTAssertEqual(player.player.actionAtItemEnd, .pause)
        let first = try XCTUnwrap(player.player.currentItem)
        let next = player.player.items()[1]
        await transport.delayPoll()
        let poll = Task { await player.reconcile() }
        for _ in 0..<100 { if await transport.pollWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let waiting = await transport.nextWaiting
        XCTAssertTrue(waiting)
        XCTAssertTrue(player.player.currentItem === first, "Never play the next item before its command is acknowledged")
        await transport.releaseNext()
        for _ in 0..<100 where player.player.currentItem !== next { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertTrue(player.player.currentItem === next)
        await transport.releasePoll()
        await poll.value
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertTrue(player.player.currentItem === next)
        let observations = await transport.observations
        XCTAssertFalse(observations.contains { $0.current == 1 && $0.afterNext })
    }

    @MainActor func testFailedTransitionCannotReplayEndedSongAtOldCursor() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.failTransitions(true)
        let first = try XCTUnwrap(player.player.currentItem)
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<500 where player.message != "Could not confirm the next song. Tap Next to retry." {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(player.message, "Could not confirm the next song. Tap Next to retry.")
        await player.reconcile()
        XCTAssertTrue(player.player.currentItem === first)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
        await transport.failTransitions(false)
        let resume = Task { await player.send("resume") }
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await transport.releaseNext()
        await resume.value
        XCTAssertEqual(player.state?.currentTrackId, 2)
    }

    @MainActor func testCallInterruptionPreservesItemAndDoesNotOverrideUserPause() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        let item = try XCTUnwrap(player.player.currentItem)
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.began.rawValue])
        for _ in 0..<100 { if await transport.observations.contains(where: { $0.observed == "interrupted" }) { break }; try await Task.sleep(for: .milliseconds(20)) }
        let interrupted = await transport.observations
        XCTAssertTrue(interrupted.contains { $0.observed == "interrupted" })
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertTrue(player.player.currentItem === item)
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.ended.rawValue,
                       AVAudioSessionInterruptionOptionKey: AVAudioSession.InterruptionOptions.shouldResume.rawValue])
        for _ in 0..<100 where !player.showsPauseControl { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(player.showsPauseControl)
        XCTAssertTrue(player.player.currentItem === item)
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.began.rawValue])
        await player.send("pause")
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.ended.rawValue,
                       AVAudioSessionInterruptionOptionKey: AVAudioSession.InterruptionOptions.shouldResume.rawValue])
        XCTAssertFalse(player.showsPauseControl)
    }
    @MainActor func testRecoveryUsesReplacementCacheAtSamePositionWithoutQueueAdvance() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        player.player.pause()
        await player.player.seek(to: CMTime(seconds: 75, preferredTimescale: 600))
        let old = try XCTUnwrap(player.player.currentItem)
        // A fresh completed cache entry can rescue an older unusable asset.
        player.cache.invalidate(1)
        let manifest = try await player.api.manifest(deviceId: player.deviceId, epoch: 1, profile: "original")
        await player.cache.fill(manifest, api: player.api)
        await transport.failMedia(true)
        player.recoverServerRoute()
        for _ in 0..<150 where player.recovering { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertFalse(player.recovering)
        XCTAssertFalse(player.playbackFailed)
        XCTAssertNil(player.message)
        XCTAssertFalse(player.player.currentItem === old)
        XCTAssertEqual(player.state?.currentTrackId, 1)
        XCTAssertEqual(player.player.currentTime().seconds, 75, accuracy: 2)
        let nextWaiting = await transport.nextWaiting
        XCTAssertFalse(nextWaiting)
    }

    @MainActor func testRecoveryExhaustsAndPauseCancelsRetryWithoutAutoplay() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.failMedia(true)
        player.recoverServerRoute()
        for _ in 0..<250 where !player.playbackFailed { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertTrue(player.playbackFailed)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
        await player.reconcile()
        let observations = await transport.observations
        XCTAssertEqual(observations.last?.observed, "failed")
        XCTAssertEqual(player.state?.currentTrackId, 1)
        player.retry()
        XCTAssertTrue(player.recovering)
        await player.send("pause")
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(player.recovering)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testPauseTakesEffectWhileNextCommandAwaitsServer() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        let next = Task { await player.send("next") }
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await player.send("pause")
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
        await transport.releaseNext()
        await next.value
        for _ in 0..<100 where player.state?.playing != false { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertFalse(player.state?.playing ?? true)
        for _ in 0..<100 where player.busy { try await Task.sleep(for: .milliseconds(20)) }
        XCTAssertFalse(player.busy, "A pause must not cancel matching preparation and leave its spinner stuck")
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testConfirmedHistoryRefreshesOncePerPlayAndMediaResetRequiresUser() async throws {
        var refreshes = 0
        let (player, _) = try await fixture(onHistoryRecorded: { refreshes += 1 })
        defer { player.stop() }
        for _ in 0..<100 where !player.isPlaying { try await Task.sleep(for: .milliseconds(20)) }
        await player.reconcile(); await player.reconcile()
        XCTAssertEqual(refreshes, 1)
        let oldEngine = player.player
        NotificationCenter.default.post(name: AVAudioSession.mediaServicesWereResetNotification, object: nil)
        XCTAssertFalse(player.player === oldEngine)
        XCTAssertTrue(player.playbackFailed)
        XCTAssertFalse(player.showsPauseControl)
        await player.reconcile()
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testWallClockWatchdogRecoversNearEndWithoutSkippingAndIgnoresUserPause() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        player.player.pause()
        await player.player.seek(to: CMTime(seconds: 177, preferredTimescale: 600))
        // First sample establishes progress; then elapsed wall time detects no progress.
        let now = ProcessInfo.processInfo.systemUptime
        player.checkPlaybackProgress(now: now)
        player.checkPlaybackProgress(now: now + 9)
        XCTAssertTrue(player.recovering)
        XCTAssertEqual(player.state?.currentTrackId, 1)
        let nextWaiting = await transport.nextWaiting
        XCTAssertFalse(nextWaiting)
        await player.send("pause")
        player.checkPlaybackProgress(now: now + 100)
        XCTAssertFalse(player.recovering)
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertEqual(player.state?.currentTrackId, 1)
    }

    @MainActor func testLateRecoveryResponseCannotReclaimAnotherDeviceOutput() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.delayMedia()
        player.recoverServerRoute()
        for _ in 0..<100 { if await transport.mediaWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let waiting = await transport.mediaWaiting
        XCTAssertTrue(waiting)
        await transport.setOwner("other-device")
        await player.reconcile(report: false)
        await transport.releaseMedia()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(player.ownsOutput)
        XCTAssertFalse(player.recovering)
        XCTAssertTrue(player.player.items().isEmpty)
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testHungMediaRequestHitsDeadlineAndLateReplyCannotRestartPlayback() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.delayMedia()
        player.recoverServerRoute()
        for _ in 0..<340 where !player.playbackFailed { try await Task.sleep(for: .milliseconds(100)) }
        XCTAssertTrue(player.playbackFailed)
        XCTAssertFalse(player.recovering)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
        await transport.releaseMedia()
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertTrue(player.playbackFailed)
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertEqual(player.state?.currentTrackId, 1)
    }

    @MainActor func testQueuedHistoryRefreshCannotRepopulateClearedAccountLibrary() async throws {
        let (player, _) = try await fixture()
        defer { player.stop() }
        let library = LibraryStore(api: player.api)
        library.scheduleRefresh()
        library.clear()
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertNil(library.overview)
        XCTAssertNil(library.updated)
        library.scheduleRefresh()
        for _ in 0..<100 where library.overview == nil { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertNotNil(library.overview)
    }

    @MainActor func testCacheRejectsTruncatedFile() async throws {
        let (player, _) = try await fixture()
        defer { player.stop() }
        let manifest = try await player.api.manifest(deviceId: player.deviceId, epoch: 1, profile: "original")
        let media = try XCTUnwrap(manifest.items.first)
        let file = try XCTUnwrap(player.cache.file(for: media))
        try Data([0]).write(to: file)
        XCTAssertNil(player.cache.file(for: media))
    }

}

actor CallResumeTransitionTransport: HTTPTransport {
    struct Observation: Sendable { let current: Int; let observed: String; let afterNext: Bool }
    private let fallback = UITestTransport()
    private var owner = ""
    private var size = 0
    private var current = 1
    private var revision = 1
    private var playing = true
    private var delayOnePoll = false
    private var delayOneMedia = false
    private var pendingMedia: CheckedContinuation<Void, Never>?
    private(set) var mediaWaiting = false
    func delayMedia() { delayOneMedia = true }
    func releaseMedia() { pendingMedia?.resume(); pendingMedia = nil }
    private var failingMedia = false
    func failMedia(_ value: Bool) { failingMedia = value }
    private var failingTransitions = false
    func failTransitions(_ value: Bool) { failingTransitions = value }
    private var pendingPoll: CheckedContinuation<Void, Never>?
    private var pendingNext: CheckedContinuation<Void, Never>?
    private(set) var pollWaiting = false
    private(set) var nextWaiting = false
    private(set) var observations: [Observation] = []
    func setOwner(_ value: String) { owner = value }
    func setSize(_ value: Int) { size = value }
    func delayPoll() { delayOnePoll = true }
    func releasePoll() { pendingPoll?.resume(); pendingPoll = nil }
    func releaseNext() { pendingNext?.resume(); pendingNext = nil }
    private func state() throws -> HTTPResult {
        HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: [
            "partial": false, "current_track_id": current, "position_seconds": 0,
            "server_time": revision, "queue_revision": revision, "owner_device_id": owner,
            "owner_epoch": 1, "intent": revision, "playing": playing, "observed": "playing",
            "seek_target_seconds": 0, "seek_intent": current, "play_id": current,
            "upcoming_track_ids": current == 1 ? [2] : []
        ] as [String: Any]))
    }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/native/playback") {
            if request.httpMethod == "POST", let body = request.httpBody,
               let value = try JSONSerialization.jsonObject(with: body) as? [String: Any] {
                let action = value["action"] as? String
                if action == "next" {
                    if failingTransitions { throw URLError(.networkConnectionLost) }
                    nextWaiting = true
                    await withCheckedContinuation { pendingNext = $0 }
                    current = 2; revision += 1
                } else if action == "pause" { playing = false; revision += 1 }
                else if action == "heartbeat" {
                    observations.append(Observation(current: value["expected_current"] as? Int ?? 0,
                        observed: value["observed"] as? String ?? "", afterNext: current == 2))
                }
            } else if delayOnePoll {
                delayOnePoll = false
                let old = try state()
                pollWaiting = true
                await withCheckedContinuation { pendingPoll = $0 }
                return old
            }
            return try state()
        }
        if path.hasSuffix("/manifest") {
            if delayOneMedia {
                delayOneMedia = false; mediaWaiting = true
                await withCheckedContinuation { pendingMedia = $0 }
            }
            if failingMedia { throw URLError(.notConnectedToInternet) }
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: [
                "owner_epoch": 1, "queue_revision": revision, "retry_after_seconds": 1,
                "items": [1,2].map { ["track_id": $0, "profile": "original", "state": "ready",
                    "url": "/api/v1/stream/\($0)", "size_bytes": size, "source_revision": "fixture"] as [String: Any] }
            ] as [String: Any]))
        }
        return try await fallback.send(request)
    }
}
