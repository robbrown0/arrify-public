import SwiftUI
import ArrifyCore
import Network
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer

/// One app-lifetime owner of AVPlayer, system controls and server ownership.
@MainActor @Observable final class PlaybackCoordinator {
    let api: APIClient
    let remoteActivity: RemotePlaybackActivity
    private let localPeer = LocalPeerVerification()
    private let audioSession: any PlaybackAudioSession
    private(set) var player = AVQueuePlayer()
    let cache: AudioCache
    // Retain the receiver-owned volume control across full-screen presentations.
    // A newly created slider must not substitute the phone's local volume.
    @ObservationIgnored lazy var expandedVolumeView: MPVolumeView = {
        let view = CenteredVolumeView(frame: CGRect(x: 0, y: 0, width: 300, height: 44))
        view.showsRouteButton = false; view.tintColor = .white
        return view
    }()
    private var aheadTask: Task<Void, Never>?
    private var aheadGeneration = UUID()
    private var aheadCompletedRevision: Int?
    private var queuedTracks: [ObjectIdentifier: Int] = [:]
    private var queuedEpoch = -1
    private var transitionTask: UIBackgroundTaskIdentifier = .invalid
    private var nowPlayingGate = NowPlayingUpdateGate()
    private var artworkRevision = 0
    private var receiverMetadataKey: String?
    var contextName: String { state?.context?.name ?? "Queue" }
    let deviceId: String
    var state: PlaybackState?
    var track: Track?
    let artworkStore: ArtworkStore
    var artwork: UIImage?
    private var receiverArtwork: Data?
    private var systemArtwork: MPMediaItemArtwork?
    private var artworkPrefetchTask: Task<Void, Never>?
    private var artworkTask: Task<Void, Never>?
    var outputName = "iPhone"
    var displayOutputName: String { hasOtherOutput ? (state?.output.flatMap { $0.isEmpty ? nil : $0 } ?? "Other device") : outputName }
    var routeDescription: String {
        hasOtherOutput ? displayOutputName : "\(UIDevice.current.model) → \(displayOutputName)"
    }
    let routeDiagnostics = AudioRouteDiagnostics()
    private var outputRefreshTask: Task<Void, Never>?
    func refreshOutput(trigger: String = "reconcile", reason: UInt? = nil, previousOutputs: [RoutePortDetails]? = nil) {
        outputName = audioSession.outputName
        if routeDiagnostics.capturing {
            routeDiagnostics.record(trigger: trigger, player: player, volumeView: expandedVolumeView,
                localName: outputName, displayedName: displayOutputName, remoteOwner: hasOtherOutput,
                reason: reason, previousOutputs: previousOutputs)
        }
    }
    /// Read again after route negotiation; never reactivate or reroute audio to obtain a name.
    func outputSelectionChanged(trigger: String = "picker-dismissed", reason: UInt? = nil, previousOutputs: [RoutePortDetails]? = nil) {
        outputRefreshTask?.cancel()
        refreshOutput(trigger: trigger, reason: reason, previousOutputs: previousOutputs)
        outputRefreshTask = Task { @MainActor [weak self] in
            for delay in [600, 1400, 3000] {
                do { try await Task.sleep(for: .milliseconds(delay)) } catch { return }
                guard let self else { return }
                self.refreshOutput(trigger: trigger + "-settled")
            }
        }
    }
    var progress: Double { duration.isFinite && duration > 0 && position.isFinite ? min(1, max(0, position / duration)) : 0 }
    var position = 0.0
    var duration = 0.0
    var busy = false
    var message: String?
    var serverConnection = ServerConnection()
    @ObservationIgnored private var networkMonitor: NWPathMonitor?
    var connectionInterrupted = false
    private var connectionFailures = 0
    var profile = "original"
    var isPlaying: Bool { player.timeControlStatus == .playing }
    var ownsOutput: Bool { state?.ownerDeviceId == deviceId }
    var hasOtherOutput: Bool { state?.ownerDeviceId != nil && !ownsOutput }
    var showsPauseControl: Bool {
        if hasOtherOutput { return state?.playing == true && state?.observed != "failed" }
        if playbackFailed || (loadedKey != nil && completedKey == loadedKey) { return false }
        return permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: true)
    }
    private var pollTask: Task<Void, Never>?
    private var eventsTask: Task<Void, Never>?
    private(set) var reconciling = false
    private var controlRevision = 0
    private var liveUpdatePending = false
    private var reportPending = false
    private var liveConnected = false
    private var prepareTask: Task<Void, Never>?
    private var preparationGeneration = UUID()
    private var endedTransitionID: String?
    private var loadedProfile = "original"
    private var retryPreparation = false
    private var loadedKey: String?
    private var completedKey: String?
    private var routeRecoveryTask: Task<Void, Never>?
    private var recoveryGeneration = UUID()
    private var recoveryDeadlineTask: Task<Void, Never>?
    private var recoveryBackgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var preparationBackgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var watchdogTask: Task<Void, Never>?
    private var lastProgressTime = ProcessInfo.processInfo.systemUptime
    private var lastProgressPosition = 0.0
    private var mediaWindow: [Int: MediaItem] = [:]
    private var resumeCheckpoint: Double?
    private var recoveryUsed = false
    private var recoveryStableSince: TimeInterval?
    private var pendingPause = false
    private var preparationSeeking = false
    private(set) var recovering = false
    private(set) var playbackFailed = false
    private(set) var historyRevision = 0
    private var reportedHistoryKey: String?
    @ObservationIgnored var onHistoryRecorded: (@MainActor () -> Void)?
    private var desiredKey: String?
    private var seekIntent = -1
    private var heartbeatSequence: HeartbeatSequence
    private var observers: [NSObjectProtocol] = []
    private var timeObserver: Any?
    @ObservationIgnored private var statusObservation: NSKeyValueObservation?
    @ObservationIgnored private var itemObservation: NSKeyValueObservation?
    @ObservationIgnored private var currentObservation: NSKeyValueObservation?
    private var diagnosticState: String?
    private var historyRequestKey: String?
    private var permission = PlaybackPermission()
    private var commandInFlight = false
    private var endedInFlight = false
    private var sessionGeneration = UUID()

    init(api: APIClient, audioSession: any PlaybackAudioSession = SystemPlaybackAudioSession(), cache: AudioCache? = nil) {
        self.api = api
        self.artworkStore = ArtworkStore(api: api)
        self.cache = cache ?? AudioCache()
        self.remoteActivity = RemotePlaybackActivity(api: api)
        self.audioSession = audioSession
        self.outputName = audioSession.outputName
        let key = "arrify.native.device-id"
        deviceId = UserDefaults.standard.string(forKey: key) ?? UUID().uuidString.lowercased()
        UserDefaults.standard.set(deviceId, forKey: key)
        heartbeatSequence = HeartbeatSequence(previous: UserDefaults.standard.integer(forKey: "arrify.native.heartbeat-sequence"))
        // Route music through AVAudioSession; external item playback can open
        // a video-style receiver surface even when the source contains only audio.
        player.allowsExternalPlayback = false
        // Buffered successors must not become audible before the server confirms
        // the transition. A delayed/failed command must never play B then reload A.
        player.actionAtItemEnd = .pause
        configureSystemControls()
        installTimeObserver()
        watchdogTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
                self?.checkPlaybackProgress()
            }
        }
        observe(AVPlayerItem.failedToPlayToEndTimeNotification) { owner, note in
            if note.itemId == owner.player.currentItem.map(ObjectIdentifier.init) {
                PlaybackDiagnostics.record("item-failed code=\(note.errorCode ?? 0) domain=\(note.errorDomain)")
                owner.recoverServerRoute()
            }
        }
        observe(AVPlayerItem.playbackStalledNotification) { owner, note in
            if note.itemId == owner.player.currentItem.map(ObjectIdentifier.init) {
                PlaybackDiagnostics.record("item-stalled")
                // AVPlayer may recover on its own; the progress watchdog bounds waiting.
            }
        }
        observe(AVPlayerItem.didPlayToEndTimeNotification) { owner, note in
            guard let identity = note.itemId, let ended = owner.queuedTracks[identity],
                  ended == owner.state?.currentTrackId, !owner.endedInFlight,
                  owner.permission.allowsPlayback(ownsOutput: owner.ownsOutput, serverPlaying: owner.state?.playing == true, mediaReady: true) else { return }
            owner.cancelRecovery()
            owner.controlRevision += 1
            owner.completedKey = owner.loadedKey
            owner.endedTransitionID = UUID().uuidString.lowercased()
            owner.endedInFlight = true
            PlaybackDiagnostics.record("track-ended awaiting-server-confirmation")
            owner.transitionTask = UIApplication.shared.beginBackgroundTask(withName: "Finish song transition") { [weak owner] in
                Task { @MainActor in owner?.finishBackgroundTransition() }
            }
            let playID = owner.state?.playId
            Task {
                // Never drop an end event just because a user command is finishing.
                for attempt in 0..<3 {
                    while owner.commandInFlight { try? await Task.sleep(for: .milliseconds(100)) }
                    guard owner.permission.allowsPlayback(ownsOutput: owner.ownsOutput, serverPlaying: owner.state?.playing == true, mediaReady: true), owner.state?.currentTrackId == ended, owner.state?.playId == playID else { break }
                    await owner.send("next", automatic: true)
                    if owner.state?.playId != playID { break }
                    try? await Task.sleep(for: .seconds(attempt + 1))
                }
                if owner.state?.playId == playID, owner.state?.playing == true {
                    // Keep the ended item fenced: reloading the unchanged server
                    // track would replay it from an old heartbeat cursor.
                    owner.player.pause()
                    owner.message = "Could not confirm the next song. Tap Next to retry."
                }
                owner.endedInFlight = false; owner.finishBackgroundTransition(); owner.startReadAhead()
                if owner.liveUpdatePending { await owner.reconcile(report: false) }
            }
        }
        observe(AVAudioSession.interruptionNotification) { owner, note in
            guard let raw = note.interruptionType,
                  let kind = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            if kind == .began {
                owner.cancelRecovery()
                owner.controlRevision += 1
                owner.permission.interruptionBegan(wasPlaying: owner.state?.playing == true, ownsOutput: owner.ownsOutput)
                owner.player.pause()
                PlaybackDiagnostics.record("audio-interruption began")
                Task { await owner.reconcile() }
            } else {
                let rawOptions = note.interruptionOptions ?? 0
                let shouldResume = AVAudioSession.InterruptionOptions(rawValue: rawOptions).contains(.shouldResume)
                owner.controlRevision += 1
                owner.permission.interruptionEnded(shouldResume: shouldResume)
                owner.resetProgressClock()
                PlaybackDiagnostics.record("audio-interruption ended resume=\(shouldResume)")
                Task {
                    await owner.reconcile()
                    if shouldResume, owner.resumeCheckpoint != nil {
                        owner.recoveryUsed = false; owner.recoverServerRoute()
                    } else { owner.playIfAllowed() }
                }
            }
        }
        observe(UIApplication.didBecomeActiveNotification) { owner, _ in
            owner.outputSelectionChanged(trigger: "foreground")
            Task {
                guard owner.pollTask != nil else { return }
                await owner.api.networkChanged(); await owner.reconcile()
            }
        }
        observe(AVAudioSession.mediaServicesWereLostNotification) { owner, _ in
            owner.cancelRecovery(); owner.permission.userPause(); owner.player.pause()
            owner.playbackFailed = true; owner.message = "Audio service interrupted. Tap Play to resume."
            PlaybackDiagnostics.record("media-services-lost")
        }
        observe(AVAudioSession.mediaServicesWereResetNotification) { owner, _ in
            owner.cancelRecovery(); owner.controlRevision += 1; owner.permission.userPause()
            owner.cancelPreparation(); owner.aheadTask?.cancel(); owner.aheadTask = nil
            owner.resetPlayerEngine()
            owner.loadedKey = nil; owner.desiredKey = nil; owner.queuedTracks = [:]
            owner.busy = false; owner.preparationSeeking = false; owner.playbackFailed = true
            owner.message = "Audio service restarted. Tap Play to resume."
            PlaybackDiagnostics.record("media-services-reset")
        }
        observe(AVAudioSession.routeChangeNotification) { owner, note in
            owner.outputSelectionChanged(trigger: "route-change", reason: note.routeReason, previousOutputs: note.previousOutputs)
            owner.updateReceiverMetadata()
            owner.publishNowPlaying()
            let reason = note.routeReason
            if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
                owner.cancelRecovery(); owner.player.pause(); owner.permission.userPause()
                Task { await owner.send("pause") }
            }
        }
    }
    private func resetPlayerEngine() {
        player.pause()
        if let observer = timeObserver { player.removeTimeObserver(observer); timeObserver = nil }
        statusObservation = nil; currentObservation = nil; itemObservation = nil
        player = AVQueuePlayer(); player.allowsExternalPlayback = false
        player.actionAtItemEnd = .pause; installTimeObserver()
    }
    private func installTimeObserver() {
        statusObservation = player.observe(\.timeControlStatus, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.playerStateChanged() }
        }
        currentObservation = player.observe(\.currentItem, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.itemObservation = self.player.currentItem?.observe(\.status, options: [.new]) { [weak self] _, _ in
                    Task { @MainActor [weak self] in self?.playerStateChanged() }
                }
                self.playerStateChanged()
            }
        }
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 1, preferredTimescale: 600), queue: .main) { @Sendable [weak self] time in
            MainActor.assumeIsolated {
                guard let self else { return }
                guard self.ownsOutput else { return }
                if !self.recovering && self.resumeCheckpoint == nil { self.position = time.seconds.isFinite ? time.seconds : 0 }
                let seconds = self.player.currentItem?.duration.seconds ?? 0
                self.duration = seconds.isFinite ? seconds : 0
                PlaybackDiagnostics.record("playback-tick position=\(self.position) duration=\(self.duration)")
                self.publishNowPlaying()
                if self.player.currentItem?.status == .failed { self.recoverServerRoute() }
            }
        }
    }
    private func playerStateChanged() {
        let item = player.currentItem
        let waiting: String
        switch player.reasonForWaitingToPlay {
        case .toMinimizeStalls: waiting = "buffer"
        case .evaluatingBufferingRate: waiting = "evaluating"
        case .noItemToPlay: waiting = "no-item"
        case nil: waiting = "none"
        default: waiting = "other"
        }
        let snapshot = "player-state engine=\(player.status.rawValue) status=\(item?.status.rawValue ?? -1) time=\(player.timeControlStatus.rawValue) waiting=\(waiting) empty=\(item?.isPlaybackBufferEmpty ?? true)"
        if snapshot != diagnosticState { diagnosticState = snapshot; PlaybackDiagnostics.record(snapshot) }
        publishNowPlaying()
        if item?.status == .failed, !busy, !recovering, !playbackFailed { recoverServerRoute() }
        // Record actual starts promptly; short songs must not depend on the polling interval.
        if isPlaying, loadedKey != nil, historyRequestKey != loadedKey, !recovering {
            historyRequestKey = loadedKey
            Task { [weak self] in await self?.reconcile() }
        }
    }
    private func resetProgressClock() {
        lastProgressTime = ProcessInfo.processInfo.systemUptime
        lastProgressPosition = player.currentTime().seconds.isFinite ? player.currentTime().seconds : position
    }
    /// Wall-clock observation still runs when AVPlayer's media-time observer stops.
    func checkPlaybackProgress(now: TimeInterval = ProcessInfo.processInfo.systemUptime) {
        guard ownsOutput, loadedKey != nil, loadedKey != completedKey, !busy, !recovering, !playbackFailed,
              permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: true),
              let item = player.currentItem else { resetProgressClock(); return }
        if item.status == .failed { recoverServerRoute(); return }
        let sample = player.currentTime().seconds
        if sample.isFinite, sample < lastProgressPosition - 0.5 {
            // A confirmed seek/repeat may move backward; establish a new baseline.
            lastProgressPosition = sample; lastProgressTime = now
        } else if sample.isFinite, sample > lastProgressPosition + 0.1 {
            position = sample; lastProgressPosition = sample; lastProgressTime = now
            if recoveryUsed {
                if recoveryStableSince == nil { recoveryStableSince = now }
                if now - (recoveryStableSince ?? now) >= 15 { recoveryUsed = false }
            }
        } else if now - lastProgressTime >= 8 {
            recoveryStableSince = nil
            recoverServerRoute()
        }
    }
    private func observe(_ name: Notification.Name, action: @escaping @MainActor (PlaybackCoordinator, AudioNotice) -> Void) {
        observers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { @Sendable [weak self] note in
            let event = AudioNotice(note)
            MainActor.assumeIsolated { if let self { action(self, event) } }
        })
    }
    private func finishBackgroundTransition() {
        if transitionTask != .invalid { UIApplication.shared.endBackgroundTask(transitionTask); transitionTask = .invalid }
    }
    func start() {
        guard pollTask == nil else { return }
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let wifi = path.status == .satisfied && path.usesInterfaceType(.wifi)
            Task { @MainActor [weak self] in
                guard let self else { return }
                await self.remoteActivity.networkChanged(wifi: wifi)
                await self.api.networkChanged()
                await self.reconcile()
            }
        }
        networkMonitor = monitor; monitor.start(queue: DispatchQueue(label: "arrify.server-network"))
        eventsTask = Task { [weak self, api] in
            var failures = 0
            while !Task.isCancelled {
                do {
                    try await api.listeningEvents { [weak self] in await self?.liveUpdate() }
                    failures = 0
                } catch { failures = min(failures + 1, 5) }
                self?.liveConnected = false
                do { try await Task.sleep(for: .seconds(min(30, 1 << failures))) } catch { return }
            }
        }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.reconcile()
                do { try await Task.sleep(for: .seconds(self.liveConnected ? 10 : (UIApplication.shared.applicationState == .active ? 5 : 15))) } catch { return }
            }
        }
    }
    func stop() {
        cancelRecovery(); resumeCheckpoint = nil; preparationSeeking = false; playbackFailed = false; recoveryUsed = false; mediaWindow = [:]; pendingPause = false; reportedHistoryKey = nil; historyRequestKey = nil
        networkMonitor?.cancel(); networkMonitor = nil; serverConnection = ServerConnection()
        localPeer.stop()
        outputRefreshTask?.cancel(); outputRefreshTask = nil; routeDiagnostics.clear()
        sessionGeneration = UUID(); permission = PlaybackPermission(); connectionInterrupted = false; connectionFailures = 0; message = nil
        aheadTask?.cancel(); aheadTask = nil; aheadCompletedRevision = nil; cache.reset(); finishBackgroundTransition()
        eventsTask?.cancel(); eventsTask = nil; liveUpdatePending = false; reportPending = false; liveConnected = false
        pollTask?.cancel(); pollTask = nil; cancelPreparation(); prepareTask = nil
        player.pause(); player.removeAllItems(); queuedTracks = [:]
        artworkPrefetchTask?.cancel(); artworkPrefetchTask = nil
        artworkTask?.cancel(); artworkTask = nil; artwork = nil; receiverArtwork = nil; systemArtwork = nil
        loadedKey = nil; completedKey = nil; desiredKey = nil; state = nil; track = nil; position = 0; duration = 0; busy = false
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        nowPlayingGate = NowPlayingUpdateGate()
        try? audioSession.deactivate()
    }
    private func liveUpdate() async {
        liveConnected = true; liveUpdatePending = true
        await reconcile(report: false)
    }
    func reconcile(report: Bool = true) async {
        guard !endedInFlight, !commandInFlight else { return }
        if reconciling {
            // Preserve interruption/heartbeat reports that overlap an older poll.
            reportPending = reportPending || report
            return
        }
        reconciling = true; liveUpdatePending = false; reportPending = false
        defer {
            reconciling = false
            if liveUpdatePending || reportPending {
                let reportAgain = reportPending
                Task { [weak self] in await self?.reconcile(report: reportAgain) }
            }
        }
        let generation = sessionGeneration
        let revision = controlRevision
        do {
            let update = try await api.state()
            guard generation == sessionGeneration, revision == controlRevision,
                  !endedInFlight, !commandInFlight, !Task.isCancelled else { return }
            await apply(update)
            guard generation == sessionGeneration, revision == controlRevision,
                  !endedInFlight, !commandInFlight, !Task.isCancelled else { return }
            if report, ownsOutput, permission.enabled, loadedKey != nil, loadedKey == desiredKey,
               let current = player.currentItem, queuedTracks[ObjectIdentifier(current)] == state?.currentTrackId {
                let sample = player.currentTime().seconds
                if sample.isFinite, !recovering, resumeCheckpoint == nil { position = max(0, sample) }
                let length = current.duration.seconds
                if length.isFinite { duration = max(0, length) }
                var heartbeat = PlaybackCommand(action: "heartbeat", deviceId: deviceId)
                heartbeat.ownerEpoch = state?.ownerEpoch; heartbeat.intent = state?.intent
                heartbeat.expectedCurrent = state?.currentTrackId; heartbeat.position = position
                heartbeat.duration = duration
                let buffered = player.currentItem?.loadedTimeRanges.compactMap { $0.timeRangeValue }.map { max(0, $0.end.seconds - position) }.max() ?? 0
                heartbeat.buffer = BufferReport(items: cache.items, playerSeconds: Int(min(86400, max(0, buffered.isFinite ? buffered : 0))))
                let sequence = heartbeatSequence.next()
                UserDefaults.standard.set(sequence, forKey: "arrify.native.heartbeat-sequence")
                heartbeat.reportSeq = sequence
                // A temporary audio-session pause is not a new user Pause intent.
                heartbeat.observed = permission.interrupted ? "interrupted" : (playbackFailed ? "failed" : (recovering ? "recovering" : (isPlaying ? "playing" : (showsPauseControl ? "buffering" : "paused"))))
                heartbeat.profile = loadedProfile; heartbeat.output = audioSession.outputName
                heartbeat.quality = loadedProfile == "original" ? "Original" : loadedProfile.uppercased()
                let ack = try await api.command(heartbeat)
                guard generation == sessionGeneration, revision == controlRevision, !endedInFlight, !commandInFlight else { return }
                state = PlaybackState.merging(ack, into: state)
                if heartbeat.observed == "playing", reportedHistoryKey != loadedKey {
                    reportedHistoryKey = loadedKey; historyRevision += 1; onHistoryRecorded?()
                }
            }
            if ownsOutput { refreshOutput() }
            connectionFailures = 0; connectionInterrupted = false
            serverConnection = await api.connection()
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            guard generation == sessionGeneration, !Task.isCancelled else { return }
            connectionFailures += 1
            connectionInterrupted = connectionFailures >= 2
            serverConnection = await api.connection()
        }
    }
    func play(_ track: Track, in tracks: [Track]) async {
        let ids = Array(tracks.drop(while: { $0.id != track.id }).prefix(1000).map(\.id))
        await send("play", ids: ids.isEmpty ? [track.id] : ids)
    }
    func play(source: LibrarySource, trackId: Int? = nil, shuffle: Bool = false) async {
        await send("play", source: source, trackId: trackId, shuffle: shuffle)
    }
    func isCurrentSource(_ source: LibrarySource) -> Bool {
        state?.currentTrackId != nil && state?.context?.matches(source) == true
    }
    func sourceShowsPause(_ source: LibrarySource) -> Bool { isCurrentSource(source) && showsPauseControl }
    func toggleSource(_ source: LibrarySource) async {
        if isCurrentSource(source) { await send(showsPauseControl ? "pause" : "resume") }
        else { await play(source: source) }
    }
    func shuffleSource(_ source: LibrarySource) async {
        if isCurrentSource(source) { await send("shuffle") }
        else { await play(source: source, shuffle: true) }
    }
    func cycleRepeat() async {
        let next = state?.repeatMode == "off" ? "all" : state?.repeatMode == "all" ? "one" : "off"
        await send("repeat", mode: next)
    }
    func send(_ action: String, ids: [Int]? = nil, seek: Double? = nil, automatic: Bool = false,
              source: LibrarySource? = nil, trackId: Int? = nil, shuffle: Bool? = nil, mode: String? = nil, index: Int? = nil, from: Int? = nil, to: Int? = nil, revision: Int? = nil) async {
        if action == "resume", !commandInFlight, ownsOutput, state?.playing == true,
           loadedKey != nil, loadedKey != completedKey, playbackFailed || player.currentItem?.status == .failed {
            retry(); return
        }
        let requestGeneration = sessionGeneration
        if action == "pause" {
            cancelRecovery(); player.pause(); permission.userPause(); controlRevision += 1
            publishNowPlaying()
            if commandInFlight { pendingPause = true; return }
        } else if !automatic && ["play", "resume", "takeover", "next", "previous", "queue_jump", "seek"].contains(action) {
            cancelRecovery()
        }
        while commandInFlight {
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
            guard requestGeneration == sessionGeneration else { return }
        }
        let action = action == "resume" && loadedKey != nil && completedKey == loadedKey ? "next" : action
        if action == "pause" { player.pause(); permission.userPause() }
        let endedTrack = state?.currentTrackId
        let endedPlay = state?.playId
        let explicitPlayback = !automatic && ["play", "resume", "takeover", "next", "previous", "queue_jump"].contains(action)
        controlRevision += 1
        commandInFlight = true
        defer {
            commandInFlight = false
            if pendingPause {
                pendingPause = false
                Task { [weak self] in
                    guard let self, self.sessionGeneration == requestGeneration else { return }
                    await self.send("pause")
                }
            }
            if liveUpdatePending { Task { [weak self] in await self?.reconcile(report: false) } }
        }
        let generation = sessionGeneration
        if explicitPlayback && (!hasOtherOutput || action == "takeover") {
            do { try audioSession.activate(); outputSelectionChanged(trigger: "session-activated") }
            catch {
                player.pause(); permission.userPause()
                message = "Audio output is unavailable. Tap Play to try again."
                return
            }
            permission.userPlayAfterSessionActivation(); recoveryUsed = false; resetProgressClock()
            // AVPlayer cannot recover a failed item by calling play() on it again.
            if !busy && desiredKey != nil && loadedKey == nil {
                cancelPreparation(); prepareTask = nil
                desiredKey = nil; loadedKey = nil; retryPreparation = true
            }
        }
        do {
            let fresh = try await api.state()
            guard generation == sessionGeneration else { return }
            state = PlaybackState.merging(fresh, into: state)
            if automatic && (state?.currentTrackId != endedTrack || state?.playId != endedPlay || !ownsOutput) { await apply(fresh); return }
            var command = PlaybackCommand(action: action, deviceId: deviceId)
            command.deviceName = UIDevice.current.model
            command.expectedRevision = revision ?? state?.queueRevision; command.ownerEpoch = state?.ownerEpoch
            command.mode = mode; command.index = index; command.from = from; command.to = to
            command.source = source; command.trackId = trackId; command.shuffle = shuffle
            command.ids = ids; command.position = seek; command.expectedCurrent = state?.currentTrackId
            if action == "next" {
                command.transitionId = automatic ? (endedTransitionID ?? UUID().uuidString.lowercased()) : UUID().uuidString.lowercased()
                command.expectedNext = state?.upcomingTrackIds?.first; command.automatic = automatic
                if automatic && !ownsOutput { return }
            }
            let update = try await api.command(command)
            guard generation == sessionGeneration else { return }
            message = nil; connectionFailures = 0; connectionInterrupted = false
            await apply(update)
            if action == "resume", playbackFailed, loadedKey != nil { recoverServerRoute() }
            if action == "seek", let seek, playbackFailed, loadedKey != nil {
                resumeCheckpoint = max(0, seek); recoveryUsed = false; recoverServerRoute()
            }
        } catch {
            guard generation == sessionGeneration, !Task.isCancelled, !RequestCancellation.matches(error) else { return }
            message = error.localizedDescription
            await reconcile()
        }
    }
    func retry() {
        guard !recovering else { return }
        if ownsOutput, state?.playing == true, loadedKey != nil, loadedKey != completedKey {
            do { try audioSession.activate(); permission.userPlayAfterSessionActivation() }
            catch { message = "Audio output is unavailable. Tap Play to try again."; return }
            recoveryUsed = false; playbackFailed = false; recoverServerRoute()
        } else { retryPreparation = true; Task { await send("resume") } }
    }
    private func apply(_ update: PlaybackState) async {
        let generation = sessionGeneration
        let revision = controlRevision
        let previous = state
        state = PlaybackState.merging(update, into: state)
        guard let metadataState = state else { return }
        let incomingKey = metadataState.currentTrackId.map { "\($0):\(metadataState.ownerEpoch):\(metadataState.playId)" }
        // Fence audio immediately, before any metadata/artwork await can yield.
        if !ownsOutput || !metadataState.playing || (loadedKey != nil && incomingKey != loadedKey) {
            cancelRecovery()
            player.pause()
            if !ownsOutput || incomingKey != desiredKey { cancelPreparation() }
        }
        if previous?.upcomingTrackIds != metadataState.upcomingTrackIds {
            // A remote queue edit must not wait for an obsolete read-ahead download.
            aheadTask?.cancel(); aheadTask = nil; aheadCompletedRevision = nil
        }
        if permission.enabled, !permission.interrupted, !pendingPause, let previous,
           !previous.playing, metadataState.playing, metadataState.intent > previous.intent {
            permission.userPlay()
        }
        if track?.id != metadataState.currentTrackId {
            // Keep the visible player mounted while metadata is in flight. Clearing
            // track here removes the accessory and relays out the entire tab view.
            artworkTask?.cancel()
            if metadataState.currentTrackId == nil {
                track = nil; artwork = nil; receiverArtwork = nil; systemArtwork = nil
            }
            player.currentItem?.externalMetadata = []
            if let id = metadataState.currentTrackId {
                let metadata = try? await api.track(id)
                if self.state?.currentTrackId == id {
                    if let metadata { track = metadata; receiverArtwork = nil; systemArtwork = nil }
                    artworkTask = Task { [weak self] in
                        guard let self else { return }
                        let decoded = await self.artworkStore.image("/api/v1/artwork/\(id)", pixels: 2048)
                        guard !Task.isCancelled, self.state?.currentTrackId == id else { return }
                        self.artwork = decoded
                        self.artworkRevision += 1
                        self.systemArtwork = self.artwork.map { NowPlayingArtwork.make(image: $0) }
                        self.receiverArtwork = self.artwork?.jpegData(compressionQuality: 0.9)
                        self.updateReceiverMetadata()
                        self.publishNowPlaying()
                    }
                }
            }
        }
        guard generation == sessionGeneration, revision == controlRevision,
              let state = self.state else { return }
        Task { [remoteActivity, localPeer, api, track, displayOutputName, ownsOutput] in
            await localPeer.update(api: api, state: state, ownsOutput: ownsOutput)
            await remoteActivity.update(state: state, track: track, destination: displayOutputName, ownsOutput: ownsOutput)
        }
        if hasOtherOutput { position = state.positionSeconds; duration = state.durationSeconds ?? 0 }
        guard ownsOutput, permission.enabled, let id = state.currentTrackId else {
            cancelRecovery(); mediaWindow = [:]
            aheadTask?.cancel(); aheadTask = nil
            cancelPreparation(); prepareTask = nil; player.pause(); player.removeAllItems(); queuedTracks = [:]
            loadedKey = nil; desiredKey = nil; busy = false
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            nowPlayingGate = NowPlayingUpdateGate()
            return
        }
        let key = "\(id):\(state.ownerEpoch):\(state.playId)"
        if desiredKey != key {
            cancelRecovery(); resumeCheckpoint = nil; playbackFailed = false; recoveryUsed = false; resetProgressClock()
        }
        if desiredKey != key, endedInFlight, profile == loadedProfile, queuedEpoch == state.ownerEpoch,
           let next = player.items().dropFirst().first, queuedTracks[ObjectIdentifier(next)] == id {
            // Only a confirmed server transition releases the prebuffered next item.
            if let old = player.currentItem { queuedTracks.removeValue(forKey: ObjectIdentifier(old)) }
            player.advanceToNextItem()
            desiredKey = key; loadedKey = key; seekIntent = state.seekIntent
            position = 0; duration = 0; resetProgressClock()
            busy = false; message = nil; updateReceiverMetadata()
            PlaybackDiagnostics.record("track-transition confirmed")
        }
        if desiredKey != key {
            desiredKey = key; cancelPreparation(); aheadTask?.cancel(); aheadTask = nil; aheadCompletedRevision = nil
            player.pause(); player.removeAllItems(); queuedTracks = [:]; loadedKey = nil; busy = true
            position = max(0, state.positionSeconds); duration = 0
            prepareTask = Task { [weak self] in await self?.prepare(key: key, id: id, epoch: state.ownerEpoch) }
        }
        if loadedKey == key, seekIntent != state.seekIntent {
            cancelRecovery(); resumeCheckpoint = nil; resetProgressClock()
            seekIntent = state.seekIntent
            position = max(0, state.seekTargetSeconds)
            player.seek(to: CMTime(seconds: max(0, state.seekTargetSeconds), preferredTimescale: 600), completionHandler: { _ in })
        }
        if !state.playing { cancelRecovery(); player.pause(); aheadTask?.cancel(); aheadTask = nil } else { playIfAllowed() }
        synchronizeQueuedItems()
        if loadedKey != nil, state.playing { startReadAhead() }
        publishNowPlaying()
    }
    private func finishRecoveryBackgroundTask() {
        if recoveryBackgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(recoveryBackgroundTask); recoveryBackgroundTask = .invalid
        }
    }
    private func finishPreparationBackgroundTask() {
        if preparationBackgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(preparationBackgroundTask); preparationBackgroundTask = .invalid
        }
    }
    private func cancelPreparation() {
        prepareTask?.cancel(); finishPreparationBackgroundTask()
    }
    private func failPreparation() {
        cancelPreparation(); player.currentItem?.cancelPendingSeeks()
        busy = false; preparationSeeking = false; playbackFailed = true
        message = "This song could not be prepared. Tap Retry or choose another song."
    }
    private func cancelRecovery() {
        if recovering { playbackFailed = true; player.currentItem?.cancelPendingSeeks() }
        recoveryGeneration = UUID()
        routeRecoveryTask?.cancel(); routeRecoveryTask = nil
        recoveryDeadlineTask?.cancel(); recoveryDeadlineTask = nil
        finishRecoveryBackgroundTask()
        recovering = false; resetProgressClock()
    }
    private func failRecovery() {
        cancelRecovery(); playbackFailed = true; player.pause()
        message = "Playback stopped. Tap Retry to reconnect."
        PlaybackDiagnostics.record("recovery-exhausted")
        publishNowPlaying()
    }
    private func remember(_ manifest: Manifest) {
        mediaWindow = Dictionary(manifest.items.map { ($0.trackId, $0) }, uniquingKeysWith: { first, _ in first })
    }
    /// This only replaces the authorized current item; it never advances the queue.
    func recoverServerRoute() {
        guard routeRecoveryTask == nil, !busy, loadedKey != nil, loadedKey != completedKey,
              permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: true),
              let current = player.currentItem, let id = state?.currentTrackId,
              queuedTracks[ObjectIdentifier(current)] == id, let epoch = state?.ownerEpoch else { return }
        guard !recoveryUsed else { failRecovery(); return }
        recoveryUsed = true; recoveryStableSince = nil; recovering = true; playbackFailed = false
        let token = UUID(); recoveryGeneration = token
        recoveryBackgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Recover current song") { [weak self] in
            Task { @MainActor [weak self] in
                guard let self, self.recoveryGeneration == token else { return }
                self.failRecovery()
            }
        }
        let key = loadedKey
        let sample = player.currentTime().seconds
        let resumePosition = resumeCheckpoint ?? (current.status != .failed && sample.isFinite ? max(0, sample) : position)
        resumeCheckpoint = resumePosition
        let originalURL = (current.asset as? AVURLAsset)?.url
        if originalURL?.isFileURL == true { cache.invalidate(id, matching: originalURL) }
        aheadTask?.cancel(); aheadTask = nil; aheadGeneration = UUID(); aheadCompletedRevision = nil
        player.pause(); message = "Reconnecting playback…"
        PlaybackDiagnostics.record("recovery-start cached=\(mediaWindow[id].flatMap { cache.file(for: $0) } != nil)")
        recoveryDeadlineTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
            guard let self, self.recoveryGeneration == token else { return }
            self.failRecovery()
        }
        routeRecoveryTask = Task { @MainActor [weak self] in
            guard let self else { return }
            var retryURL = originalURL
            for attempt in 0..<3 {
                do {
                    try Task.checkCancellation()
                    let url: URL
                    if attempt == 0, let media = self.mediaWindow[id], let local = self.cache.file(for: media) {
                        url = local
                    } else if attempt == 2, let retryURL, !retryURL.isFileURL,
                              let alternate = try await self.api.alternateMediaURL(retryURL) {
                        url = alternate
                    } else {
                        let manifest = try await self.api.manifest(deviceId: self.deviceId, epoch: epoch, profile: self.loadedProfile, retry: true)
                        guard manifest.ownerEpoch == epoch,
                              let media = manifest.items.first(where: { $0.trackId == id && $0.state == "ready" }) else { throw PlaybackFailure() }
                        guard self.recoveryGeneration == token, !Task.isCancelled else { return }
                        self.remember(manifest)
                        url = try await self.api.mediaURL(media)
                    }
                    guard self.recoveryGeneration == token, self.loadedKey == key, self.ownsOutput, !Task.isCancelled else { return }
                    if !url.isFileURL { retryURL = url }
                    if self.player.status == .failed { self.resetPlayerEngine() }
                    let replacement = AVPlayerItem(url: url)
                    self.player.removeAllItems(); self.queuedTracks = [ObjectIdentifier(replacement): id]
                    self.player.insert(replacement, after: nil)
                    do { try await self.readyAndSeek(replacement, position: resumePosition) }
                    catch {
                        if url.isFileURL { self.cache.invalidate(id) }
                        throw error
                    }
                    guard self.recoveryGeneration == token, !Task.isCancelled,
                          self.permission.allowsPlayback(ownsOutput: self.ownsOutput, serverPlaying: self.state?.playing == true, mediaReady: true) else { return }
                    try self.audioSession.activate()
                    self.player.play()
                    // Ready/seek success is not proof of recovered playback. Observe actual progress
                    // before ending the retry cycle, so a failed cache can fall through to streaming.
                    let progressDeadline = ProcessInfo.processInfo.systemUptime + 8
                    while true {
                        try Task.checkCancellation()
                        guard self.player.currentItem === replacement, replacement.status != .failed,
                              ProcessInfo.processInfo.systemUptime < progressDeadline else { throw PlaybackFailure() }
                        let progress = self.player.currentTime().seconds
                        if progress.isFinite && progress > resumePosition + 0.05 { break }
                        try await Task.sleep(for: .milliseconds(100))
                    }
                    guard self.recoveryGeneration == token, !Task.isCancelled else { return }
                    self.recoveryDeadlineTask?.cancel(); self.recoveryDeadlineTask = nil
                    self.finishRecoveryBackgroundTask()
                    self.routeRecoveryTask = nil; self.recovering = false; self.message = nil
                    self.position = resumePosition; self.resumeCheckpoint = nil; self.resetProgressClock()
                    self.updateReceiverMetadata(); self.playerStateChanged(); self.publishNowPlaying(); self.startReadAhead()
                    PlaybackDiagnostics.record("recovery-ready attempt=\(attempt + 1) cached=\(url.isFileURL)")
                    return
                } catch {
                    guard self.recoveryGeneration == token, !Task.isCancelled else { return }
                    self.player.pause()
                    if let failedURL = (self.player.currentItem?.asset as? AVURLAsset)?.url, failedURL.isFileURL {
                        self.cache.invalidate(id, matching: failedURL)
                    }
                    PlaybackDiagnostics.record("recovery-attempt-failed attempt=\(attempt + 1)")
                    do { try await Task.sleep(for: .seconds(1)) } catch { return }
                }
            }
            if self.recoveryGeneration == token { self.failRecovery() }
        }
    }
    private func readyAndSeek(_ item: AVPlayerItem, position: Double) async throws {
        let deadline = ProcessInfo.processInfo.systemUptime + 8
        while item.status == .unknown {
            try Task.checkCancellation()
            guard player.currentItem === item, ProcessInfo.processInfo.systemUptime < deadline else { throw PlaybackFailure() }
            try await Task.sleep(for: .milliseconds(50))
        }
        guard item.status == .readyToPlay else {
            PlaybackDiagnostics.record("item-not-ready code=\((item.error as NSError?)?.code ?? 0)")
            throw PlaybackFailure()
        }
        let completion = PlaybackSeekCompletion()
        player.seek(to: CMTime(seconds: max(0, position), preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) { finished in
            Task { @MainActor in completion.finished = finished }
        }
        while completion.finished == nil {
            try Task.checkCancellation()
            guard player.currentItem === item, ProcessInfo.processInfo.systemUptime < deadline else { item.cancelPendingSeeks(); throw PlaybackFailure() }
            try await Task.sleep(for: .milliseconds(50))
        }
        guard completion.finished == true else { throw PlaybackFailure() }
    }
    private func prepare(key: String, id: Int, epoch: Int) async {
        guard !Task.isCancelled, desiredKey == key, ownsOutput else { return }
        let requestedProfile = profile
        let explicitRetry = retryPreparation; retryPreparation = false
        let preparationToken = UUID(); preparationGeneration = preparationToken
        finishPreparationBackgroundTask()
        preparationBackgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Prepare current song") { [weak self] in
            Task { @MainActor [weak self] in
                guard let self, self.preparationGeneration == preparationToken, self.busy else { return }
                self.failPreparation()
            }
        }
        let deadline = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
            guard let self, self.desiredKey == key, self.preparationGeneration == preparationToken, self.busy else { return }
            self.failPreparation()
        }
        defer {
            deadline.cancel()
            if preparationGeneration == preparationToken { finishPreparationBackgroundTask() }
        }
        do {
            for attempt in 0..<8 {
                try Task.checkCancellation()
                let manifest = try await api.manifest(deviceId: deviceId, epoch: epoch, profile: requestedProfile, retry: attempt == 0 && explicitRetry)
                guard !Task.isCancelled, desiredKey == key, ownsOutput,
                      manifest.ownerEpoch == epoch else { return }
                remember(manifest)
                guard let item = manifest.items.first(where: { $0.trackId == id }) else { throw APIError.invalidResponse }
                if item.state == "ready" {
                    let url: URL
                    if let local = cache.file(for: item) { url = local } else { url = try await api.mediaURL(item) }
                    guard !Task.isCancelled, desiredKey == key else { return }
                    let current = AVPlayerItem(url: url)
                    player.removeAllItems(); queuedTracks = [ObjectIdentifier(current): id]; queuedEpoch = epoch
                    player.insert(current, after: nil)
                    updateReceiverMetadata()
                    loadedKey = key; loadedProfile = requestedProfile; message = nil
                    preparationSeeking = true
                    seekIntent = state?.seekIntent ?? -1
                    try await readyAndSeek(current, position: state?.positionSeconds ?? 0)
                    guard !Task.isCancelled, desiredKey == key else { return }
                    preparationSeeking = false; busy = false; playbackFailed = false; resumeCheckpoint = nil; resetProgressClock()
                    playIfAllowed(); publishNowPlaying(); startReadAhead(); return
                }
                if ["failed", "unavailable"].contains(item.state) { throw PlaybackFailure() }
                try await Task.sleep(for: .seconds(min(5, max(1, manifest.retryAfterSeconds))))
            }
            throw PlaybackFailure()
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            if desiredKey == key { preparationSeeking = false; busy = false; playbackFailed = true; message = "This song could not be prepared. Tap Retry or choose another song." }
        }
    }
    private func synchronizeQueuedItems() {
        let expected = state?.repeatMode == "one" ? [] : Array((state?.upcomingTrackIds ?? []).prefix(3))
        let queued = Array(player.items().dropFirst())
        if queued.map({ queuedTracks[ObjectIdentifier($0)] }) != Array(expected.prefix(queued.count)).map({ Optional($0) }) {
            for item in queued { player.remove(item); queuedTracks.removeValue(forKey: ObjectIdentifier(item)) }
        }
    }
    private func enqueueAhead(_ manifest: Manifest) async throws {
        guard state?.queueRevision == manifest.queueRevision, player.currentItem != nil, !endedInFlight else { return }
        synchronizeQueuedItems()
        let next = state?.repeatMode == "one" ? [] : Array((state?.upcomingTrackIds ?? []).prefix(3))
        let count = max(0, player.items().count - 1)
        for id in next.dropFirst(count) {
            guard let media = manifest.items.first(where: { $0.trackId == id && $0.state == "ready" }) else { break }
            let url: URL
            if let file = cache.file(for: media) { url = file } else { break }
            guard !Task.isCancelled, ownsOutput, state?.ownerEpoch == manifest.ownerEpoch, state?.queueRevision == manifest.queueRevision else { return }
            let item = AVPlayerItem(url: url)
            if player.canInsert(item, after: player.items().last) {
                queuedTracks[ObjectIdentifier(item)] = id; player.insert(item, after: player.items().last)
            }
        }
    }
    private func startReadAhead() {
        guard aheadTask == nil, !endedInFlight, !recovering, !playbackFailed, !busy, ownsOutput, permission.enabled, state?.playing == true, aheadCompletedRevision != state?.queueRevision else { return }
        let generation = UUID(); aheadGeneration = generation
        aheadTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.aheadGeneration == generation { self.aheadTask = nil } }
            while !Task.isCancelled, self.ownsOutput, let state = self.state, state.playing {
                do {
                    let manifest = try await self.api.manifest(deviceId: self.deviceId, epoch: state.ownerEpoch, profile: self.loadedProfile)
                    guard !Task.isCancelled, !self.endedInFlight, self.state?.ownerEpoch == manifest.ownerEpoch, self.state?.queueRevision == manifest.queueRevision else { return }
                    self.remember(manifest)
                    // Artwork is independently best-effort; it never gates audio downloads.
                    self.artworkPrefetchTask?.cancel()
                    self.artworkPrefetchTask = Task { [artworkStore = self.artworkStore] in
                        for item in manifest.items.prefix(4) {
                            guard !Task.isCancelled else { return }
                            _ = await artworkStore.image("/api/v1/artwork/\(item.trackId)", pixels: 2048)
                        }
                    }
                    // Download current first. Do not open duplicate remote players for future songs.
                    await self.cache.fill(manifest, api: self.api)
                    guard !Task.isCancelled, !self.endedInFlight, self.state?.ownerEpoch == manifest.ownerEpoch, self.state?.queueRevision == manifest.queueRevision else { return }
                    // Replace only future items with verified local files; never interrupt current audio.
                    for queued in self.player.items().dropFirst() {
                        if let id = self.queuedTracks[ObjectIdentifier(queued)],
                           let media = manifest.items.first(where: { $0.trackId == id }),
                           self.cache.file(for: media) != nil {
                            self.player.remove(queued); self.queuedTracks.removeValue(forKey: ObjectIdentifier(queued))
                        }
                    }
                    try await self.enqueueAhead(manifest)
                    if self.cache.items.allSatisfy({ ["ready", "oversized", "budget"].contains($0.state) }) {
                        self.aheadCompletedRevision = manifest.queueRevision
                        return
                    }
                } catch { /* Streaming remains available; bounded retries on the next pass. */ }
                do { try await Task.sleep(for: .seconds(10)) } catch { return }
            }
        }
    }
    private func playIfAllowed() {
        guard loadedKey != completedKey, !busy, !preparationSeeking, !recovering, !playbackFailed else { return }
        if player.currentItem?.status == .failed { recoverServerRoute(); return }
        guard permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: loadedKey != nil) else { return }
        guard player.timeControlStatus == .paused else { return }
        do { try audioSession.activate(); refreshOutput(trigger: "playback-activated"); player.play() }
        catch { player.pause(); permission.userPause(); message = "Audio is unavailable. Tap Play to try again." }
    }
    private func updateReceiverMetadata() {
        guard let track, track.id == state?.currentTrackId else { return }
        guard let item = player.currentItem else { return }
        let key = "\(ObjectIdentifier(item)):\(track.id):\(artworkRevision)"
        guard receiverMetadataKey != key else { return }
        receiverMetadataKey = key
        item.externalMetadata = ReceiverMetadata.make(track: track, artwork: receiverArtwork)
    }
    private func publishNowPlaying() {
        guard ownsOutput, permission.enabled, loadedKey != nil, let track else { return }
        let signature = "\(track.id):\(artworkRevision):\(Int(duration)):\(state?.playId ?? 0)"
        guard nowPlayingGate.shouldPublish(signature: signature, position: position,
                                          rate: isPlaying ? 1 : 0, now: ProcessInfo.processInfo.systemUptime) else { return }
        var info: [String: Any] = [MPMediaItemPropertyTitle: track.title,
            MPMediaItemPropertyArtist: track.artist, MPMediaItemPropertyAlbumTitle: track.album,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
            MPNowPlayingInfoPropertyIsLiveStream: false,
            MPMediaItemPropertyPlaybackDuration: duration, MPNowPlayingInfoPropertyElapsedPlaybackTime: position,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0]
        if let systemArtwork { info[MPMediaItemPropertyArtwork] = systemArtwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
    private func configureSystemControls() {
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.addTarget { @Sendable [weak self] _ in Task { @MainActor in await self?.send("resume") }; return .success }
        commands.pauseCommand.addTarget { @Sendable [weak self] _ in Task { @MainActor in await self?.send("pause") }; return .success }
        commands.togglePlayPauseCommand.addTarget { @Sendable [weak self] _ in
            Task { @MainActor in guard let self else { return }; await self.send(self.showsPauseControl ? "pause" : "resume") }; return .success
        }
        commands.nextTrackCommand.addTarget { @Sendable [weak self] _ in Task { @MainActor in await self?.send("next") }; return .success }
        commands.previousTrackCommand.addTarget { @Sendable [weak self] _ in Task { @MainActor in await self?.send("previous") }; return .success }
        commands.changePlaybackPositionCommand.addTarget { @Sendable [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            let seconds = event.positionTime
            Task { @MainActor in await self?.send("seek", seek: seconds) }; return .success
        }
    }
}
@MainActor private final class PlaybackSeekCompletion { var finished: Bool? }
private struct PlaybackFailure: Error {}

private struct AudioNotice: Sendable {
    let errorCode: Int?
    let errorDomain: String
    let itemId: ObjectIdentifier?
    let interruptionType: UInt?
    let interruptionOptions: UInt?
    let routeReason: UInt?
    let previousOutputs: [RoutePortDetails]?
    init(_ note: Notification) {
        let error = note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? NSError
        errorCode = error?.code
        // Never log localized descriptions, URLs or arbitrary userInfo.
        errorDomain = [NSURLErrorDomain, AVFoundationErrorDomain, NSOSStatusErrorDomain].contains(error?.domain ?? "") ? error!.domain : "other"
        itemId = (note.object as? AVPlayerItem).map(ObjectIdentifier.init)
        interruptionType = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
        interruptionOptions = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
        routeReason = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
        previousOutputs = (note.userInfo?[AVAudioSessionRouteChangePreviousRouteKey] as? AVAudioSessionRouteDescription)?.outputs.map(RoutePortDetails.init)
    }
}
