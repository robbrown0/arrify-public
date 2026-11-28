import XCTest
import UIKit
import ArrifyCore
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer
@testable import Arrify

final class PlaybackTransitionTests: XCTestCase {
    @MainActor func testSilentAudioFixturesHaveFinalizedNativeFrameCounts() throws {
        for seconds in [0.5,1.0,180.0] {
            let url = try UITestAudiobookAudio.make(seconds:seconds)
            defer { try? FileManager.default.removeItem(at:url) }
            try autoreleasepool {
                let reader = try AVAudioFile(forReading:url)
                XCTAssertEqual(reader.length,AVAudioFramePosition(seconds*8000))
                XCTAssertEqual(reader.fileFormat.sampleRate,8000)
                XCTAssertEqual(reader.fileFormat.channelCount,1)
            }
        }
    }
    @MainActor func testTimelineReplacesPreviousItemClockWithCurrentItemTimeAndLength() async throws {
        let (player, _) = try await fixture(cachedNext: true)
        defer { player.stop() }
        let item = try XCTUnwrap(player.player.currentItem)
        player.position = 203
        player.duration = 196
        XCTAssertTrue(player.updatePlaybackTimeline())
        XCTAssertEqual(player.position, item.currentTime().seconds, accuracy: 0.2)
        XCTAssertEqual(player.duration, item.duration.seconds, accuracy: 0.01)
        XCTAssertLessThan(player.position, 30)
        XCTAssertTrue(player.isPlaying)
        XCTAssertTrue(player.player.currentItem === item)
    }

    @MainActor func testPlayingBeyondEstimatedDurationDoesNotClaimCompletionOrAdvance() async throws {
        let (player, _) = try await fixture(cachedNext: true)
        defer { player.stop() }
        let item = player.player.currentItem
        let track = player.state?.currentTrackId
        player.position = 203
        player.duration = 196
        XCTAssertNil(player.timeline.remaining)
        XCTAssertNil(player.timeline.duration)
        XCTAssertEqual(player.progress, 0)
        XCTAssertTrue(player.isPlaying)
        XCTAssertTrue(player.player.currentItem === item)
        XCTAssertEqual(player.state?.currentTrackId, track)
    }

    @MainActor func testAudiobookHandoffPreservesSystemMetadataAndCachedMusicSuccessors() async throws {
        let (player, _) = try await fixture(cachedNext:true)
        defer { player.stop() }
        let queued = player.player.items().count
        player.suspendForAudiobook()
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [MPMediaItemPropertyTitle:"Audiobook fixture"]
        await player.reconcile(report:false)
        XCTAssertFalse(player.isPlaying)
        XCTAssertEqual(MPNowPlayingInfoCenter.default().nowPlayingInfo?[MPMediaItemPropertyTitle] as? String,"Audiobook fixture")
        XCTAssertEqual(player.player.items().count,queued)
        await player.send("resume")
        for _ in 0..<100 where !player.isPlaying { try await Task.sleep(for:.milliseconds(20)) }
        XCTAssertTrue(player.isPlaying)
        let started = ProcessInfo.processInfo.systemUptime
        await player.send("next")
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime-started,0.5)
        XCTAssertEqual(player.state?.currentTrackId,2)
    }
    override func setUp() {
        super.setUp()
        executionTimeAllowance = 60
    }

    @MainActor func fixture(cachedNext: Bool = false, windowSize: Int = 2, queueSize: Int? = nil, downloadProbe: StartupCacheProbe? = nil, makePlayerItem: @escaping (URL) -> AVPlayerItem = { AVPlayerItem(url: $0) }, onHistoryRecorded: (@MainActor () -> Void)? = nil, audioSession: any PlaybackAudioSession = UITestAudioSession(), backgroundTasks: any PlaybackBackgroundTasks = SystemPlaybackBackgroundTasks()) async throws -> (PlaybackCoordinator, CallResumeTransitionTransport) {
        let transport = CallResumeTransitionTransport()
        let api = APIClient(transport: transport, store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        // Real local silent audio keeps AVQueuePlayer deterministic and offline.
        let bytes = UITestAudiobookAudio.data(seconds:180)
        await transport.setSize(bytes.count)
        await transport.setWindowSize(queueSize ?? windowSize)
        if queueSize != nil { await transport.useRollingWindow() }
        let cache = AudioCache(download: { url in
            if let downloadProbe { return try await downloadProbe.download(url, bytes: bytes) }
            let target = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
            try bytes.write(to: target)
            return (target, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        var initialItem: AVPlayerItem?, arranging = true
        let coordinator = PlaybackCoordinator(api: api, audioSession: audioSession, cache: cache, backgroundTasks: backgroundTasks, makePlayerItem: { url in
            let item = makePlayerItem(url)
            if arranging, initialItem == nil { initialItem = item }
            return item
        })
        coordinator.onHistoryRecorded = onHistoryRecorded
        await transport.setOwner(coordinator.deviceId)
        let manifest = try await api.manifest(deviceId: coordinator.deviceId, epoch: 1, profile: "original")
        await cache.fill(manifest, api: api)
        for media in manifest.items {
            let file = try XCTUnwrap(cache.file(for:media))
            let frames = try autoreleasepool { try AVAudioFile(forReading:file).length }
            XCTAssertEqual(frames,AVAudioFramePosition(8000*180))
        }
        await coordinator.send("resume")
        for _ in 0..<500 where coordinator.player.items().count < windowSize || !coordinator.isPlaying {
            if coordinator.playbackFailed { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        guard coordinator.player.items().count == windowSize, coordinator.isPlaying else {
            let item = coordinator.player.currentItem ?? initialItem
            let error = item?.error as NSError?
            let detail = "Fixture failed to play: \(error?.domain ?? "none") / \(error?.code ?? 0), status=\(item?.status.rawValue ?? -1), duration=\(item?.duration.seconds ?? -1), queue=\(coordinator.player.items().count), control=\(coordinator.player.timeControlStatus.rawValue), \(coordinator.message ?? "no message")"
            coordinator.stop()
            XCTFail(detail)
            throw NSError(domain: "PlaybackFixture", code: 1, userInfo: [NSLocalizedDescriptionKey: detail])
        }
        arranging = false; initialItem = nil
        // Existing network-path regressions exercise an unverified successor.
        // Cached continuity is covered separately with cachedNext: true.
        if !cachedNext {
            let queuedURL = (coordinator.player.items()[1].asset as? AVURLAsset)?.url
            cache.invalidate(2)
            // Exercise server-confirmed navigation without invalidating the audio
            // already queued in AVPlayer. AVPlayer may open that file lazily.
            if let queuedURL { try bytes.write(to: queuedURL) }
            // Simulate the download completing while the command is in flight.
            // The tap must use the server path, but preparation needs real audio.
            await transport.restoreAudioOnAcknowledgement { await cache.fill(manifest, api: api) }
        }
        return (coordinator, transport)
    }

    @MainActor func testCachedEndAdvancesBeforeAcknowledgementAndDelayedPollCannotRewind() async throws {
        let (player, transport) = try await fixture(cachedNext: true)
        defer { player.stop() }
        let first = try XCTUnwrap(player.player.currentItem)
        let next = player.player.items()[1]
        await transport.failTransitions(true)
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<100 where player.state?.currentTrackId != 2 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertTrue(player.player.currentItem === next)
        await player.reconcile()
        XCTAssertEqual(player.state?.currentTrackId, 2, "Offline reconciliation must not restore the ended song")
        for _ in 0..<100 where !player.isPlaying { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.isPlaying)
    }

    @MainActor func testNextRecoversTransientManifestFailureWithoutPreparationError() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.allowTransitionsImmediately()
        await transport.failOneNextManifest()
        await player.send("next")
        for _ in 0..<400 where player.state?.currentTrackId != 2 || !player.isPlaying || player.busy {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertTrue(player.isPlaying)
        XCTAssertFalse(player.playbackFailed)
        XCTAssertNil(player.message)
        let recovered = await transport.transientManifestFailures
        XCTAssertEqual(recovered, 1)
        let nextRequests = await transport.nextRequests
        XCTAssertEqual(nextRequests, 1, "Recover the preparation read without replaying Next")
    }
    @MainActor func testCachedManualSkipIsImmediateAndPauseWinsWhileOffline() async throws {
        let (player, transport) = try await fixture(cachedNext: true)
        defer { player.stop() }
        await transport.failTransitions(true)
        let next = player.player.items()[1]
        let start = ProcessInfo.processInfo.systemUptime
        await player.send("next")
        XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - start, 0.5)
        XCTAssertTrue(player.player.currentItem === next)
        await player.send("pause")
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
    }

    @MainActor func testReadAheadKeepsVerifiedLocalSuccessors() async throws {
        let (player, _) = try await fixture(cachedNext:true,windowSize:4)
        defer { player.stop() }
        let state = try XCTUnwrap(player.state)
        let manifest = try await player.api.manifest(deviceId:player.deviceId,epoch:state.ownerEpoch,profile:"original")
        let original = player.player.items()
        XCTAssertEqual(original.count,4)
        XCTAssertTrue(original.allSatisfy { ($0.asset as? AVURLAsset)?.url.isFileURL == true })
        for _ in 0..<3 {
            try await player.refreshCachedSuccessors(manifest,expectedRevision:state.queueRevision)
            XCTAssertEqual(player.player.items().map { ObjectIdentifier($0) },original.map { ObjectIdentifier($0) },
                           "Read-ahead must not discard already verified and prebuffered local items")
            XCTAssertTrue(player.player.currentItem === original.first)
            XCTAssertEqual(player.state?.currentTrackId,state.currentTrackId)
        }
    }

    @MainActor func testLostCachedTransitionAcknowledgementRetriesSameIDWithoutReplay() async throws {
        let (player, transport) = try await fixture(cachedNext: true)
        defer { player.stop() }
        await transport.dropNextAcknowledgement()
        let first = try XCTUnwrap(player.player.currentItem)
        let next = player.player.items()[1]
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.player.currentItem === next,"The cached successor must be retained before its acknowledgement")
        await transport.releaseNext()
        for _ in 0..<100 { if await transport.nextCommits == 1 { break }; try await Task.sleep(for: .milliseconds(10)) }
        for _ in 0..<100 {
            await player.reconcile()
            if await transport.nextRequests >= 2 { break }
            try await Task.sleep(for:.milliseconds(10))
        }
        let requests = await transport.nextRequests
        let commits = await transport.nextCommits
        let ids = await transport.transitionIds
        XCTAssertGreaterThanOrEqual(requests,2,"A lost acknowledgement must actually retry before checking idempotency")
        XCTAssertEqual(commits, 1)
        XCTAssertEqual(Set(ids).count, 1)
        XCTAssertTrue(player.player.currentItem === next)
        XCTAssertEqual(player.state?.currentTrackId, 2)
    }

    @MainActor func testThreeCachedSkipsContinueOfflineAndReconcileInOrder() async throws {
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4)
        defer { player.stop() }
        for expected in 2...4 {
            for _ in 0..<500 where player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
            let start = ProcessInfo.processInfo.systemUptime
            var completed = false
            let skip = Task { await player.send("next"); completed = true }
            for _ in 0..<500 where !completed { try await Task.sleep(for:.milliseconds(10)) }
            guard completed else {
                await transport.allowTransitionsImmediately(); await transport.releaseNext(); await skip.value
                XCTFail("Cached navigation waited for a blocked server acknowledgement"); return
            }
            let elapsed = ProcessInfo.processInfo.systemUptime-start
            let commits = await transport.nextCommits
            XCTAssertEqual(commits,0,"All three local skips must complete before any acknowledgement")
            print("NEXT_LATENCY cached-offline command_ms=\(Int(elapsed*1000)) acknowledgements=\(commits)")
            XCTAssertEqual(player.state?.currentTrackId, expected)
            await player.reconcile()
            XCTAssertEqual(player.state?.currentTrackId, expected)
            await skip.value
        }
        let last = player.player.currentItem
        await transport.allowTransitionsImmediately()
        await transport.releaseNext()
        for _ in 0..<200 {
            await player.reconcile()
            if await transport.nextCommits == 3 { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        let commits = await transport.nextCommits
        XCTAssertEqual(commits, 3)
        XCTAssertEqual(player.state?.currentTrackId, 4)
        XCTAssertTrue(player.player.currentItem === last, "Intermediate acknowledgements must never reload older songs")
    }

    @MainActor func testUncachedNextDoesNotWaitForStatePreflight() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.holdStateRequests()
        let next = Task { await player.send("next") }
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let sent = await transport.nextWaiting
        XCTAssertTrue(sent, "A blocked state GET must not delay an owner skip command")
        await transport.releaseStateRequests()
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await transport.releaseNext()
        await next.value
    }

    @MainActor func testInterruptedCachedNextStartsBeforeDelayedAcknowledgement() async throws {
        let (player, transport) = try await fixture(cachedNext:true)
        defer { player.stop() }
        NotificationCenter.default.post(name:AVAudioSession.interruptionNotification,object:nil,
            userInfo:[AVAudioSessionInterruptionTypeKey:AVAudioSession.InterruptionType.began.rawValue])
        for _ in 0..<100 where player.showsPauseControl { try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertFalse(player.showsPauseControl)
        let started = ProcessInfo.processInfo.systemUptime
        var returned = false
        let next = Task { await player.send("next"); returned = true }
        for _ in 0..<200 where !returned { try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertTrue(returned,"An explicit Next must reactivate and use verified audio without waiting for the server")
        XCTAssertEqual(player.state?.currentTrackId,2)
        if returned {
            for _ in 0..<500 where player.navigationPending { try await Task.sleep(for:.milliseconds(10)) }
            XCTAssertFalse(player.navigationPending,"Measure real playback progress, not just a local command")
            XCTAssertTrue(player.isPlaying)
            let commits = await transport.nextCommits
            XCTAssertEqual(commits,0,"The acknowledgement must still be held during the measurement")
            print("NEXT_LATENCY interrupted-cached actual_progress_ms=\(Int((ProcessInfo.processInfo.systemUptime-started)*1000)) acknowledgement_held=true")
        }
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for:.milliseconds(10)) }
        await transport.allowTransitionsImmediately(); await transport.releaseNext(); await next.value
        for _ in 0..<200 { if await transport.nextCommits == 1 { break }; try await Task.sleep(for:.milliseconds(10)) }
        let commits = await transport.nextCommits
        XCTAssertEqual(commits,1)
        XCTAssertEqual(player.state?.currentTrackId,2,"The late acknowledgement must not replay or skip another song")
    }

    @MainActor func testPauseAndRapidNextDuringInterruptionActivationCannotAdvance() async throws {
        let session = DelayedActivationSession()
        let (player,transport) = try await fixture(cachedNext:true,audioSession:session)
        defer { player.stop() }
        NotificationCenter.default.post(name:AVAudioSession.interruptionNotification,object:nil,
            userInfo:[AVAudioSessionInterruptionTypeKey:AVAudioSession.InterruptionType.began.rawValue])
        for _ in 0..<100 where player.showsPauseControl { try await Task.sleep(for:.milliseconds(10)) }
        session.hold = true
        let next = Task { await player.send("next") }
        for _ in 0..<100 where !session.waiting { try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertTrue(session.waiting)
        await player.send("next") // Coalesce the second tap during activation.
        await player.send("pause")
        session.release(); await next.value
        let requests = await transport.nextRequests
        XCTAssertEqual(requests,0)
        XCTAssertEqual(player.state?.currentTrackId,1)
        XCTAssertEqual(player.player.rate,0)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertFalse(player.navigationPending)
    }

    @MainActor func testRecoveryCannotRestartPreviousSongWhileNextAwaitsServer() async throws {
        let (player,transport) = try await fixture()
        defer { player.stop() }
        let original = try XCTUnwrap(player.player.currentItem)
        let next = Task { await player.send("next") }
        for _ in 0..<100 { if await transport.nextWaiting { break }; try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertTrue(player.navigationPending)
        player.player.pause() // The watchdog would see no progress while Next is pending.
        player.recoverServerRoute()
        XCTAssertFalse(player.recovering,"Navigation owns the transition; recovery must not restart the old current song")
        XCTAssertTrue(player.player.currentItem === original)
        XCTAssertEqual(player.state?.currentTrackId,1)
        await transport.allowTransitionsImmediately(); await transport.releaseNext(); await next.value
        for _ in 0..<500 where player.navigationPending { try await Task.sleep(for:.milliseconds(10)) }
        XCTAssertEqual(player.state?.currentTrackId,2)
        XCTAssertTrue(player.isPlaying)
    }

    @MainActor func testRapidCachedSkipIsCoalescedUntilPlaybackProgress() async throws {
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4)
        defer { player.stop() }
        await transport.failTransitions(true)
        await player.send("next")
        await player.send("next")
        XCTAssertEqual(player.state?.currentTrackId, 2)
    }

    @MainActor func testFourthSkipUsesRefreshedWindowBeforeLaterAcknowledgementsFinish() async throws {
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4, queueSize: 7)
        defer { player.stop() }
        for expected in 2...4 {
            for _ in 0..<500 where player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
            await player.send("next")
            XCTAssertEqual(player.state?.currentTrackId, expected)
        }
        for _ in 0..<500 where player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
        var completed = false
        let start = ProcessInfo.processInfo.systemUptime
        let fourth = Task { await player.send("next"); completed = true }
        await transport.releaseNext() // Commit only 1 -> 2. The next acknowledgement remains held.
        for _ in 0..<500 where !completed || player.navigationPending || !player.isPlaying { try await Task.sleep(for: .milliseconds(10)) }
        let elapsed = ProcessInfo.processInfo.systemUptime-start
        let commitsBeforeRelease = await transport.nextCommits
        XCTAssertTrue(completed, "Replenished, authorized audio must not wait for the other two acknowledgements")
        XCTAssertFalse(player.navigationPending, "Measure real media progress, not just the command returning")
        XCTAssertTrue(player.isPlaying)
        XCTAssertEqual(commitsBeforeRelease, 1)
        XCTAssertEqual(player.state?.currentTrackId, 5)
        print("NEXT_LATENCY refreshed-window actual_progress_ms=\(Int(elapsed*1000)) previous_acknowledgements_remaining=2")
        await transport.allowTransitionsImmediately(); await transport.releaseNext()
        await fourth.value
        for _ in 0..<500 { if await transport.nextCommits == 4 { break }; try await Task.sleep(for: .milliseconds(10)) }
        let commits = await transport.nextCommits
        XCTAssertEqual(commits, 4)
        XCTAssertEqual(player.state?.currentTrackId, 5, "Acknowledgements must preserve the audible queue order")
    }

    @MainActor func testPauseCancelsSkipWaitingForCachedAcknowledgements() async throws {
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4, queueSize: 7)
        defer { player.stop() }
        for _ in 0..<3 {
            for _ in 0..<500 where player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
            await player.send("next")
        }
        for _ in 0..<500 where player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
        var skipped = false
        let skip = Task { await player.send("next"); skipped = true }
        await Task.yield()
        let pause = Task { await player.send("pause") }
        for _ in 0..<200 where !skipped { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(skipped, "Pause must cancel a waiting manual skip without waiting for network acknowledgements")
        XCTAssertEqual(player.player.rate, 0)
        await transport.allowTransitionsImmediately(); await transport.releaseNext()
        await skip.value; await pause.value
        XCTAssertEqual(player.state?.currentTrackId, 4)
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
    }
    @MainActor func testChangedAcknowledgedTailStopsBeforeSendingStaleTransitions() async throws {
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4)
        defer { player.stop() }
        for expected in 2...4 {
            for _ in 0..<500 where player.navigationPending { try await Task.sleep(for:.milliseconds(10)) }
            await player.send("next")
            XCTAssertEqual(player.state?.currentTrackId,expected)
        }
        await transport.removeUpcomingOnAcknowledgement()
        await transport.releaseNext()
        for _ in 0..<200 where player.message != "The queue changed. Tap Play to continue." { try await Task.sleep(for:.milliseconds(10)) }
        let requests = await transport.nextRequests
        XCTAssertEqual(requests,1,"A changed queue must reject the remaining local journal before another command")
        XCTAssertEqual(player.message,"The queue changed. Tap Play to continue.")
        XCTAssertEqual(player.player.rate,0)
        XCTAssertFalse(player.showsPauseControl)
        await transport.releaseNext()
    }

    @MainActor func testUncachedStartupPreemptsReadAheadUntilAudioAdvances() async throws {
        let probe = StartupCacheProbe()
        let audio = try UITestAudiobookAudio.make()
        defer { try? FileManager.default.removeItem(at: audio) }
        var installedUncached = false
        var pausedAtInstall = false
        var cache: AudioCache?
        let (player, transport) = try await fixture(cachedNext: true, windowSize: 4, downloadProbe: probe, makePlayerItem: { url in
            if !url.isFileURL { installedUncached = true; pausedAtInstall = cache?.startupPending == true }
            return AVPlayerItem(url: audio)
        })
        cache = player.cache
        defer { player.stop() }
        let manifest = try await player.api.manifest(deviceId: player.deviceId, epoch: 1, profile: "original")
        await probe.holdDownloads()
        player.cache.invalidate(3)
        let background = Task { await player.cache.fill(manifest, api: player.api) }
        for _ in 0..<200 { if await probe.waiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let waiting = await probe.waiting
        XCTAssertTrue(waiting)
        player.cache.invalidate(2)
        for item in player.player.items().dropFirst() { player.player.remove(item) }
        await transport.allowTransitionsImmediately()
        let start = ProcessInfo.processInfo.systemUptime
        await player.send("next")
        for _ in 0..<500 where !installedUncached || !player.isPlaying || player.navigationPending { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(installedUncached)
        XCTAssertTrue(pausedAtInstall, "A full-file cache download must not compete with the uncached item startup")
        XCTAssertTrue(player.isPlaying)
        XCTAssertFalse(player.navigationPending)
        let cancellations = await probe.cancellations
        XCTAssertGreaterThan(cancellations, 0)
        print("NEXT_LATENCY uncached actual_progress_ms=\(Int((ProcessInfo.processInfo.systemUptime-start)*1000)) background_cancelled=\(cancellations)")
        await probe.releaseDownloads()
        await background.value
    }

    @MainActor func testRejectedCachedTransitionStopsAndDoesNotReplayOldTrack() async throws {
        let (player, transport) = try await fixture(cachedNext: true)
        defer { player.stop() }
        await transport.rejectTransitions()
        await player.send("next")
        for _ in 0..<100 where player.showsPauseControl { try await Task.sleep(for: .milliseconds(10)) }
        await player.reconcile()
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
    }

    @MainActor func testCancelledReadAheadKeepsUsefulDownloadAndReportsItReady() async throws {
        let api = APIClient(transport: UITestTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let data = Data("""
        {"owner_epoch":1,"queue_revision":1,"retry_after_seconds":1,"items":[{"track_id":1,"profile":"original","state":"ready","size_bytes":4,"mime_type":"audio/mpeg","url":"/api/v1/stream/1"}]}
        """.utf8)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: data)
        let probe = CacheDownloadProbe()
        let cache = AudioCache(download: { try await probe.download($0) })
        defer { cache.reset() }
        let first = Task { await cache.fill(manifest, api: api) }
        for _ in 0..<100 { if await probe.calls == 1 { break }; try await Task.sleep(for: .milliseconds(10)) }
        first.cancel()
        await cache.fill(manifest, api: api)
        await first.value
        let calls = await probe.calls
        XCTAssertEqual(calls, 1, "A queue refresh must adopt, not restart, a useful download")
        XCTAssertNotNil(cache.file(for: manifest.items[0]))
        XCTAssertEqual(cache.items.first?.state, "ready")
    }

    @MainActor func testSelectionFeedbackAppearsBeforeSlowStateRequestAndClearsOnStop() async throws {
        let (player, transport) = try await fixture()
        await transport.holdStateRequests()
        let selection = Task { await player.play(source: LibrarySource(kind: "albums", id: "fixture"), trackId: 2) }
        for _ in 0..<500 {
            if player.selectingTrackID == 2, await transport.stateRequestWaiting { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        let waiting = await transport.stateRequestWaiting
        XCTAssertTrue(waiting, "The selection must remain blocked on state, not finish before the assertion")
        XCTAssertEqual(player.rowStatus(for: 2), .preparing)
        XCTAssertEqual(player.state?.currentTrackId, 1, "Feedback must not pretend the server has changed songs")
        player.stop()
        XCTAssertNil(player.selectingTrackID)
        await transport.releaseStateRequests()
        await selection.value
        XCTAssertNil(player.selectingTrackID)
        XCTAssertNil(player.state)
    }

    @MainActor func testBackgroundProtectionPrecedesRemovalAndExpiresSynchronously() async throws {
        let assertions = RecordingBackgroundTasks()
        let (player, transport) = try await fixture(backgroundTasks: assertions)
        defer { player.stop() }
        var protectedOldItem = false
        assertions.onPreparation = { [weak player] in protectedOldItem = player?.player.currentItem != nil }
        let next = Task { await player.send("next") }
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await transport.delayMedia()
        await transport.releaseNext()
        await next.value
        for _ in 0..<500 { if await transport.mediaWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(protectedOldItem)
        let identifier = try XCTUnwrap(assertions.preparation)
        XCTAssertFalse(assertions.ended.contains(identifier))
        assertions.expirations[identifier]?()
        XCTAssertTrue(assertions.ended.contains(identifier), "Expiration must end the assertion before returning")
        XCTAssertTrue(player.playbackFailed)
        XCTAssertFalse(player.navigationPending)
        await transport.releaseMedia()
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(player.playbackFailed)
        XCTAssertEqual(assertions.ended.filter { $0 == identifier }.count, 1)
    }

    @MainActor func testPreparationProtectionCoversDelayedActivation() async throws {
        let assertions = RecordingBackgroundTasks()
        let session = DelayedActivationSession()
        let (player, transport) = try await fixture(audioSession: session, backgroundTasks: assertions)
        defer { session.release(); player.stop() }
        let next = Task { await player.send("next") }
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        session.hold = true
        await transport.setPosition(60)
        await transport.releaseNext()
        await next.value
        for _ in 0..<500 where !session.waiting { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(session.waiting)
        let identifier = try XCTUnwrap(assertions.preparation)
        XCTAssertFalse(assertions.ended.contains(identifier))
        // Rewinding during activation must reset the progress baseline instead
        // of holding protection until the old position is reached again.
        let rewound = await player.player.seek(to: .zero, toleranceBefore: .zero, toleranceAfter: .zero)
        XCTAssertTrue(rewound)
        XCTAssertFalse(assertions.ended.contains(identifier))
        session.release()
        for _ in 0..<500 where !assertions.ended.contains(identifier) { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(assertions.ended.contains(identifier))
        XCTAssertTrue(player.isPlaying)
        XCTAssertEqual(assertions.ended.filter { $0 == identifier }.count, 1)
    }

    @MainActor func testBufferedTransitionKeepsProtectionAndPauseWinsOverActivation() async throws {
        let assertions = RecordingBackgroundTasks()
        let session = DelayedActivationSession()
        let (player, transport) = try await fixture(audioSession: session, backgroundTasks: assertions)
        defer { session.release(); player.stop() }
        let first = try XCTUnwrap(player.player.currentItem)
        let next = player.player.items()[1]
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        session.hold = true
        await transport.releaseNext()
        for _ in 0..<500 where !session.waiting { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(session.waiting)
        XCTAssertTrue(player.player.currentItem === next)
        let identifier = try XCTUnwrap(assertions.preparation)
        XCTAssertFalse(assertions.ended.contains(identifier))
        await player.send("pause")
        session.release()
        for _ in 0..<100 where !assertions.ended.contains(identifier) { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(assertions.ended.contains(identifier))
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
    }

    @MainActor func testExpiredOldPreparationCannotCancelNewTrack() async throws {
        let assertions = RecordingBackgroundTasks()
        let (player, transport) = try await fixture(backgroundTasks: assertions)
        defer { player.stop() }
        let old = try XCTUnwrap(assertions.preparation)
        let next = Task { await player.send("next") }
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await transport.delayMedia()
        await transport.releaseNext()
        await next.value
        for _ in 0..<500 { if await transport.mediaWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let current = try XCTUnwrap(assertions.preparation)
        XCTAssertNotEqual(old, current)
        assertions.expirations[old]?()
        XCTAssertFalse(player.playbackFailed)
        XCTAssertFalse(assertions.ended.contains(current))
        await transport.releaseMedia()
        for _ in 0..<500 where !assertions.ended.contains(current) { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.isPlaying)
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertEqual(assertions.ended.filter { $0 == current }.count, 1)
    }

    @MainActor func testRestartWaitsForPreviousAudioDeactivation() async throws {
        let session = DelayedDeactivationSession()
        let (player, _) = try await fixture(audioSession: session)
        defer { session.release(); player.stop() }
        session.hold = true
        player.stop()
        for _ in 0..<100 where !session.waiting { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(session.waiting)
        player.stop() // Repeated teardown must join the same ordered handoff.
        let activations = session.activations
        let manifest = try await player.api.manifest(deviceId: player.deviceId, epoch: 1, profile: "original")
        await player.cache.fill(manifest, api: player.api)
        let resume = Task { await player.send("resume") }
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(session.activations, activations, "New activation must wait for outstanding deactivation")
        session.release()
        await resume.value
        for _ in 0..<500 where !player.isPlaying { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.isPlaying)
        XCTAssertGreaterThan(session.activations, activations)
    }

    @MainActor func testFailedManualNextReleasesNavigationGate() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.failTransitions(true)
        await player.send("next")
        XCTAssertFalse(player.navigationPending)
        XCTAssertEqual(player.state?.currentTrackId, 1)
    }

    @MainActor func testPauseWinsOverDelayedRetryActivation() async throws {
        let session = DelayedActivationSession()
        let (player, _) = try await fixture(audioSession: session)
        defer { player.stop() }
        session.hold = true
        player.retry()
        for _ in 0..<100 where !session.waiting { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(session.waiting)
        await player.send("pause")
        session.release()
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertFalse(player.recovering)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testForegroundKeepsCurrentItemAndVerifiedRoute() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        for _ in 0..<500 where player.reconciling { try await Task.sleep(for: .milliseconds(10)) }
        let probes = await transport.capabilityRequests
        let item = player.player.currentItem
        await player.foregrounded()
        let after = await transport.capabilityRequests
        XCTAssertEqual(after, probes)
        XCTAssertTrue(player.player.currentItem === item)
        XCTAssertEqual(player.state?.currentTrackId, 1)
    }

    @MainActor func testRepeatedNextDoesNotQueueDuringRequestOrPreparation() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        let next = Task { await player.send("next") }
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.navigationPending)
        for _ in 0..<6 { await player.send("next") }
        await transport.delayMedia()
        await transport.releaseNext()
        await next.value
        for _ in 0..<500 { if await transport.mediaWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.navigationPending)
        for _ in 0..<6 { await player.send("next") }
        let count = await transport.nextRequests
        XCTAssertEqual(count, 1)
        await transport.releaseMedia()
        for _ in 0..<500 where player.navigationPending {
            player.checkPlaybackProgress()
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertFalse(player.navigationPending)
        XCTAssertEqual(player.state?.currentTrackId, 2)
    }

    @MainActor func testChangedTrackWhileNextWaitsForStateDoesNotSkipAgain() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        for _ in 0..<500 where player.reconciling { try await Task.sleep(for: .milliseconds(10)) }
        await transport.advanceExternally()
        await player.send("next")
        // Owner navigation sends immediately; the server rejects stale song/revision
        // fences, then reconciliation adopts the external advance without skipping.
        for _ in 0..<200 where player.state?.currentTrackId != 2 {
            try await Task.sleep(for: .milliseconds(10))
        }
        let requests = await transport.nextRequests
        let commits = await transport.nextCommits
        XCTAssertEqual(requests, 1)
        XCTAssertEqual(commits, 0)
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertFalse(player.navigationPending)
    }

    @MainActor func testSlowMetadataDoesNotBlockAudioTransition() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.delayMetadata()
        let next = Task { await player.send("next") }
        for _ in 0..<500 { if await transport.nextWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        await transport.releaseNext()
        await next.value
        for _ in 0..<500 where player.navigationPending {
            player.checkPlaybackProgress()
            try await Task.sleep(for: .milliseconds(20))
        }
        let held = await transport.metadataWaiting
        XCTAssertTrue(held)
        XCTAssertFalse(player.navigationPending)
        XCTAssertTrue(player.isPlaying)
        XCTAssertEqual(player.state?.currentTrackId, 2)
        await transport.releaseMetadata()
    }

    @MainActor func testAirPlayGroupChangePreservesItemAndLastReceiverLossPauses() async throws {
        let session = GroupRouteSession()
        let (player, _) = try await fixture(audioSession: session)
        defer { player.stop() }
        let item = player.player.currentItem
        let activations = session.activations
        player.handleRouteChange(reason: AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue, previousWasAirPlay: true)
        XCTAssertTrue(player.player.currentItem === item)
        XCTAssertTrue(player.showsPauseControl)
        XCTAssertEqual(session.activations, activations)
        player.player.pause()
        player.recoverServerRoute()
        XCTAssertFalse(player.recovering, "Do not rebuild the item during AirPlay negotiation")
        session.usesAirPlay = false
        player.handleRouteChange(reason: AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue, previousWasAirPlay: true)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testNextItemWaitsForServerConfirmationAndDelayedPollCannotRestoreOldSong() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        XCTAssertEqual(player.player.actionAtItemEnd, .pause)
        let first = try XCTUnwrap(player.player.currentItem)
        let next = player.player.items()[1]
        // Wait for the real-start heartbeat to finish so the deliberately held
        // GET belongs to this task, not a coalesced background reconciliation.
        for _ in 0..<500 where player.reconciling || player.historyRevision == 0 {
            if !player.reconciling { await player.reconcile() }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertFalse(player.reconciling)
        XCTAssertGreaterThan(player.historyRevision, 0)
        await transport.delayPoll()
        let poll = Task { await player.reconcile() }
        for _ in 0..<500 { if await transport.pollWaiting { break }; try await Task.sleep(for: .milliseconds(10)) }
        let delayedPollStarted = await transport.pollWaiting
        XCTAssertTrue(delayedPollStarted, "Establish the delayed response before simulating the track ending")
        guard delayedPollStarted else {
            poll.cancel(); await transport.releasePoll(); await poll.value
            return
        }
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

    @MainActor func testEndedTrackContinuesAfterConnectionReturnsWithoutManualNext() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.failTransitions(true)
        let first = try XCTUnwrap(player.player.currentItem)
        player.player.pause()
        NotificationCenter.default.post(name: AVPlayerItem.didPlayToEndTimeNotification, object: first)
        for _ in 0..<500 where player.message != "Could not confirm the next song. Tap Next to retry." {
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertTrue(player.player.currentItem === first)
        await transport.allowTransitionsImmediately()
        await transport.failTransitions(false)
        player.resumeEndedAfterConnection(now: ProcessInfo.processInfo.systemUptime + 20)
        for _ in 0..<200 where player.state?.currentTrackId != 2 { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertEqual(player.state?.currentTrackId, 2)
        XCTAssertFalse(player.player.currentItem === first)
    }

    @MainActor func testInterruptionReportSurvivesOverlappingPoll() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        // Wait for any fixture observation to finish before holding this poll.
        for _ in 0..<500 where player.reconciling { try await Task.sleep(for: .milliseconds(20)) }
        await transport.delayPoll()
        let poll = Task { await player.reconcile(report: false) }
        for _ in 0..<500 { if await transport.pollWaiting { break }; try await Task.sleep(for: .milliseconds(20)) }
        let waiting = await transport.pollWaiting
        XCTAssertTrue(waiting)
        NotificationCenter.default.post(name: AVAudioSession.interruptionNotification, object: nil,
            userInfo: [AVAudioSessionInterruptionTypeKey: AVAudioSession.InterruptionType.began.rawValue])
        // Let the interruption's task encounter the held poll.
        await Task.yield()
        try await Task.sleep(for: .milliseconds(50))
        await transport.releasePoll()
        await poll.value
        for _ in 0..<500 { if await transport.observations.contains(where: { $0.observed == "interrupted" }) { break }; try await Task.sleep(for: .milliseconds(20)) }
        let observations = await transport.observations
        XCTAssertTrue(observations.contains { $0.observed == "interrupted" })
        XCTAssertFalse(player.showsPauseControl)
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
        for _ in 0..<100 where !player.recovering { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.recovering)
        await player.send("pause")
        try await Task.sleep(for: .milliseconds(200))
        XCTAssertFalse(player.recovering)
        XCTAssertFalse(player.showsPauseControl)
        XCTAssertEqual(player.player.rate, 0)
    }

    @MainActor func testConnectionReturnRetriesExhaustedRecoveryButPausePreventsRestart() async throws {
        let (player, transport) = try await fixture()
        defer { player.stop() }
        await transport.failMedia(true)
        player.recoverServerRoute()
        for _ in 0..<500 where !player.playbackFailed { try await Task.sleep(for: .milliseconds(10)) }
        XCTAssertTrue(player.playbackFailed)
        player.resumeRecoveryAfterConnection()
        XCTAssertFalse(player.recovering, "Retry cycles must be spaced out")
        player.resumeRecoveryAfterConnection(now: ProcessInfo.processInfo.systemUptime + 20)
        XCTAssertTrue(player.recovering, "A working connection should allow recovery without a user tap")
        await player.send("pause")
        player.resumeRecoveryAfterConnection(now: ProcessInfo.processInfo.systemUptime + 60)
        XCTAssertFalse(player.recovering)
        XCTAssertEqual(player.player.rate, 0)
        XCTAssertFalse(player.showsPauseControl)
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
        // KVO can already have a heartbeat in flight. A concurrent reconcile
        // intentionally coalesces, so wait for its acknowledgement, not just rate.
        for _ in 0..<100 where refreshes == 0 {
            await player.reconcile()
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTAssertEqual(refreshes, 1)
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

    @MainActor func testAudioCacheRetainsCurrentAndThreeUpcomingFiles() async throws {
        let api = APIClient(transport: UITestTransport(), store: UITestStore())
        try await api.login(endpoint: "https://fixture.invalid", username: "fixture", password: "fixture", deviceName: "Test")
        let items = (1...5).map { id in
            ["track_id": id, "profile": "original", "state": "ready", "size_bytes": 4,
             "mime_type": "audio/mpeg", "url": "/api/v1/stream/\(id)"] as [String: Any]
        }
        let data = try JSONSerialization.data(withJSONObject: ["owner_epoch": 1, "queue_revision": 1, "retry_after_seconds": 1, "items": items])
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let manifest = try decoder.decode(Manifest.self, from: data)
        let cache = AudioCache(download: { url in
            let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
            try Data([1, 2, 3, 4]).write(to: file)
            return (file, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
        })
        defer { cache.reset() }
        await cache.fill(manifest, api: api)
        XCTAssertEqual(cache.items.map(\.id), [1, 2, 3, 4])
        XCTAssertEqual(cache.downloadedBytes, 16)
        for item in manifest.items.prefix(4) { XCTAssertNotNil(cache.file(for: item)) }
        XCTAssertNil(cache.file(for: manifest.items[4]))
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
    private(set) var capabilityRequests = 0
    private(set) var nextRequests = 0
    private var holdMetadata = false
    private var pendingMetadata: CheckedContinuation<Void, Never>?
    private(set) var metadataWaiting = false
    func delayMetadata() { holdMetadata = true }
    func releaseMetadata() { pendingMetadata?.resume(); pendingMetadata = nil }
    func advanceExternally() { current = 2; revision += 1 }
    private var owner = ""
    private var size = 0
    private var current = 1
    private var windowSize = 2
    private var rollingWindow = false
    private var removeAcknowledgedUpcoming = false
    func useRollingWindow() { rollingWindow = true }
    func removeUpcomingOnAcknowledgement() { removeAcknowledgedUpcoming = true }
    private var immediateTransitions = false
    private var rejectedTransitions = false
    func setWindowSize(_ value: Int) { windowSize = value }
    func allowTransitionsImmediately() { immediateTransitions = true }
    func rejectTransitions() { rejectedTransitions = true }
    private var positionSeconds = 0.0
    func setPosition(_ seconds: Double) { positionSeconds = seconds }
    private var revision = 1
    private var playing = true
    private var delayOnePoll = false
    private var holdAllStateRequests = false
    private var heldStateRequests: [CheckedContinuation<Void, Never>] = []
    var stateRequestWaiting: Bool { !heldStateRequests.isEmpty }
    func holdStateRequests() { holdAllStateRequests = true }
    func releaseStateRequests() {
        holdAllStateRequests = false
        let requests = heldStateRequests; heldStateRequests.removeAll()
        for request in requests { request.resume() }
    }
    private var delayOneMedia = false
    private var pendingMedia: CheckedContinuation<Void, Never>?
    private(set) var mediaWaiting = false
    func delayMedia() { delayOneMedia = true }
    func releaseMedia() { pendingMedia?.resume(); pendingMedia = nil }
    private var failNextManifest = false
    private(set) var transientManifestFailures = 0
    func failOneNextManifest() { failNextManifest = true }
    private var failingMedia = false
    func failMedia(_ value: Bool) { failingMedia = value }
    private var failingTransitions = false
    private var loseNextAck = false
    private var lastTransition: String?
    private(set) var nextCommits = 0
    private(set) var transitionIds: [String] = []
    func dropNextAcknowledgement() { loseNextAck = true }
    func failTransitions(_ value: Bool) { failingTransitions = value }
    private var pendingPoll: CheckedContinuation<Void, Never>?
    private var pendingNext: CheckedContinuation<Void, Never>?
    private var restoreAudio: (@Sendable () async -> Void)?
    func restoreAudioOnAcknowledgement(_ action: @escaping @Sendable () async -> Void) { restoreAudio = action }
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
            "partial": false, "current_track_id": current, "position_seconds": positionSeconds,
            "server_time": revision, "queue_revision": revision, "owner_device_id": owner,
            "owner_epoch": 1, "intent": revision, "playing": playing, "observed": "playing",
            "seek_target_seconds": 0, "seek_intent": current, "play_id": current,
            "upcoming_track_ids": removeAcknowledgedUpcoming && nextCommits > 0 ? [] : current < windowSize ? Array((current + 1)...min(windowSize, rollingWindow ? current + 3 : windowSize)) : []
        ] as [String: Any]))
    }
    func send(_ request: URLRequest) async throws -> HTTPResult {
        let path = request.url!.path
        if path.hasSuffix("/capabilities") { capabilityRequests += 1 }
        if path.hasSuffix("/native/playback") {
            if request.httpMethod == "POST", let body = request.httpBody,
               let value = try JSONSerialization.jsonObject(with: body) as? [String: Any] {
                let action = value["action"] as? String
                if action == "next" {
                    nextRequests += 1
                    if let id = value["transition_id"] as? String {
                        transitionIds.append(id)
                        if id == lastTransition { return try state() }
                    }
                    let expectedCurrent = value["expected_current"] as? Int
                    let expectedRevision = value["expected_revision"] as? Int
                    let expectedEpoch = value["owner_epoch"] as? Int
                    if expectedCurrent != current || expectedRevision != revision || expectedEpoch != 1 {
                        return HTTPResult(status: 409, data: Data("{\"error\":{\"code\":\"playback_changed\",\"message\":\"Playback changed\"}}".utf8))
                    }
                    if rejectedTransitions { return HTTPResult(status: 409, data: Data("{\"error\":{\"code\":\"playback_changed\",\"message\":\"Output changed\"}}".utf8)) }
                    if failingTransitions { throw URLError(.networkConnectionLost) }
                    nextWaiting = true
                    if !immediateTransitions { await withCheckedContinuation { pendingNext = $0 } }
                    await restoreAudio?()
                    current = value["expected_next"] as? Int ?? 2; revision += 1; nextCommits += 1
                    lastTransition = value["transition_id"] as? String
                    if loseNextAck { loseNextAck = false; throw URLError(.networkConnectionLost) }
                } else if action == "pause" { playing = false; revision += 1 }
                else if action == "heartbeat" {
                    observations.append(Observation(current: value["expected_current"] as? Int ?? 0,
                        observed: value["observed"] as? String ?? "", afterNext: current == 2))
                }
            } else if holdAllStateRequests {
                let old = try state()
                await withCheckedContinuation { heldStateRequests.append($0) }
                return old
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
            if failNextManifest, current == 2 {
                failNextManifest = false; transientManifestFailures += 1
                throw URLError(.networkConnectionLost)
            }
            if delayOneMedia {
                delayOneMedia = false; mediaWaiting = true
                await withCheckedContinuation { pendingMedia = $0 }
            }
            if failingMedia { throw URLError(.notConnectedToInternet) }
            return HTTPResult(status: 200, data: try JSONSerialization.data(withJSONObject: [
                "owner_epoch": 1, "queue_revision": revision, "retry_after_seconds": 1,
                "items": Array((rollingWindow ? current : 1)...min(windowSize, rollingWindow ? current + 3 : windowSize)).map { ["track_id": $0, "profile": "original", "state": "ready",
                    "url": "/api/v1/stream/\($0)", "mime_type": "audio/wav", "size_bytes": size, "source_revision": "fixture"] as [String: Any] }
            ] as [String: Any]))
        }
        if path == "/api/v1/tracks/2", holdMetadata {
            holdMetadata = false; metadataWaiting = true
            await withCheckedContinuation { pendingMetadata = $0 }
        }
        return try await fallback.send(request)
    }
}

@MainActor private final class GroupRouteSession: PlaybackAudioSession {
    var usesAirPlay = true
    var activations = 0
    func activate() async throws { activations += 1 }
    func deactivate() async throws {}
}

final class AudioActivationTests: XCTestCase {
    @MainActor func testActivationRunsOffMainAndReusesSessionUntilInvalidated() async throws {
        let calls = ActivationCalls()
        let session = SystemPlaybackAudioSession(activate: { calls.record() }, deactivate: {})
        try await session.activate()
        try await session.activate()
        XCTAssertEqual(calls.snapshot.count, 1)
        XCTAssertFalse(calls.snapshot.main)
        session.invalidate()
        try await session.activate()
        XCTAssertEqual(calls.snapshot.count, 2)
        try await session.deactivate()
        try await session.activate()
        XCTAssertEqual(calls.snapshot.count, 3)
    }
}
private final class ActivationCalls: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    private var main = false
    func record() { lock.lock(); defer { lock.unlock() }; count += 1; main = main || Thread.isMainThread }
    var snapshot: (count: Int, main: Bool) { lock.lock(); defer { lock.unlock() }; return (count, main) }
}

@MainActor private final class DelayedActivationSession: PlaybackAudioSession {
    var hold = false
    var waiting = false
    private var continuation: CheckedContinuation<Void, Never>?
    func activate() async throws {
        if hold { waiting = true; await withCheckedContinuation { continuation = $0 } }
    }
    func release() { continuation?.resume(); continuation = nil; hold = false }
    func deactivate() async throws {}
}

@MainActor private final class RecordingBackgroundTasks: PlaybackBackgroundTasks {
    var preparation: UIBackgroundTaskIdentifier?
    var expirations: [UIBackgroundTaskIdentifier: @MainActor @Sendable () -> Void] = [:]
    var ended: [UIBackgroundTaskIdentifier] = []
    var onPreparation: (() -> Void)?
    func begin(_ name: String, expiration: @escaping @MainActor @Sendable () -> Void) -> UIBackgroundTaskIdentifier {
        let identifier = UIBackgroundTaskIdentifier(rawValue: expirations.count + 1)
        expirations[identifier] = expiration
        if name == "Prepare current song" { preparation = identifier; onPreparation?() }
        return identifier
    }
    func end(_ identifier: UIBackgroundTaskIdentifier) { ended.append(identifier) }
}

@MainActor private final class DelayedDeactivationSession: PlaybackAudioSession {
    var hold = false
    var waiting = false
    var activations = 0
    private var continuation: CheckedContinuation<Void, Never>?
    func activate() async throws { activations += 1 }
    func deactivate() async throws {
        if hold { waiting = true; await withCheckedContinuation { continuation = $0 } }
    }
    func release() { hold = false; continuation?.resume(); continuation = nil }
}

private actor CacheDownloadProbe {
    private(set) var calls = 0
    func download(_ url: URL) async throws -> (URL, URLResponse) {
        calls += 1
        try await Task.sleep(for: .milliseconds(200))
        let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data([1, 2, 3, 4]).write(to: file)
        return (file, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}

actor StartupCacheProbe {
    private var held = false
    private(set) var waiting = false
    private(set) var cancellations = 0
    func holdDownloads() { held = true }
    func releaseDownloads() { held = false }
    func download(_ url: URL, bytes: Data) async throws -> (URL, URLResponse) {
        if held {
            waiting = true
            do { while held { try await Task.sleep(for: .milliseconds(10)) } }
            catch { cancellations += 1; waiting = false; throw error }
            waiting = false
        }
        let target = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".wav")
        try bytes.write(to: target)
        return (target, HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
