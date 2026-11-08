import SwiftUI
import ArrifyCore
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer

/// One app-lifetime owner of AVPlayer, system controls and server ownership.
@MainActor @Observable final class PlaybackCoordinator {
    let api: APIClient
    private let audioSession: any PlaybackAudioSession
    let player = AVPlayer()
    let deviceId: String
    var state: PlaybackState?
    var track: Track?
    var artwork: UIImage?
    private var receiverArtwork: Data?
    private var systemArtwork: MPMediaItemArtwork?
    private var artworkTask: Task<Void, Never>?
    var outputName = "iPhone"
    var progress: Double { duration.isFinite && duration > 0 && position.isFinite ? min(1, max(0, position / duration)) : 0 }
    var position = 0.0
    var duration = 0.0
    var busy = false
    var message: String?
    var profile = "original"
    var isPlaying: Bool { player.timeControlStatus == .playing }
    var ownsOutput: Bool { state?.ownerDeviceId == deviceId }
    var hasOtherOutput: Bool { state?.ownerDeviceId != nil && !ownsOutput }
    var showsPauseControl: Bool {
        if hasOtherOutput { return state?.playing == true }
        return permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: true)
    }
    private var pollTask: Task<Void, Never>?
    private var prepareTask: Task<Void, Never>?
    private var loadedProfile = "original"
    private var retryPreparation = false
    private var loadedKey: String?
    private var desiredKey: String?
    private var seekIntent = -1
    private var heartbeatSequence: HeartbeatSequence
    private var observers: [NSObjectProtocol] = []
    private var timeObserver: Any?
    private var permission = PlaybackPermission()
    private var commandInFlight = false
    private var endedInFlight = false
    private var sessionGeneration = UUID()

    init(api: APIClient, audioSession: any PlaybackAudioSession = SystemPlaybackAudioSession()) {
        self.api = api
        self.audioSession = audioSession
        self.outputName = audioSession.outputName
        let key = "arrify.native.device-id"
        deviceId = UserDefaults.standard.string(forKey: key) ?? UUID().uuidString.lowercased()
        UserDefaults.standard.set(deviceId, forKey: key)
        heartbeatSequence = HeartbeatSequence(previous: UserDefaults.standard.integer(forKey: "arrify.native.heartbeat-sequence"))
        // Route music through AVAudioSession; external item playback can open
        // a video-style receiver surface even when the source contains only audio.
        player.allowsExternalPlayback = false
        configureSystemControls()
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 1, preferredTimescale: 600), queue: .main) { @Sendable [weak self] time in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.position = time.seconds.isFinite ? time.seconds : 0
                let seconds = self.player.currentItem?.duration.seconds ?? 0
                self.duration = seconds.isFinite ? seconds : 0
                self.publishNowPlaying()
                if self.player.currentItem?.status == .failed { self.message = "Playback stopped. Tap Retry to reconnect." }
            }
        }
        observe(AVPlayerItem.didPlayToEndTimeNotification) { owner, note in
            guard let current = owner.player.currentItem, note.itemId == ObjectIdentifier(current) else { return }
            guard !owner.endedInFlight else { return }
            owner.endedInFlight = true
            Task { await owner.send("next", automatic: true); owner.endedInFlight = false }
        }
        observe(AVAudioSession.interruptionNotification) { owner, note in
            guard let raw = note.interruptionType,
                  let kind = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            if kind == .began {
                owner.permission.interruptionBegan(wasPlaying: owner.state?.playing == true, ownsOutput: owner.ownsOutput)
                owner.player.pause()
            } else {
                let rawOptions = note.interruptionOptions ?? 0
                let shouldResume = AVAudioSession.InterruptionOptions(rawValue: rawOptions).contains(.shouldResume)
                owner.permission.interruptionEnded(shouldResume: shouldResume)
                Task { await owner.reconcile(); owner.playIfAllowed() }
            }
        }
        observe(AVAudioSession.routeChangeNotification) { owner, note in
            owner.outputName = owner.audioSession.outputName
            owner.updateReceiverMetadata()
            owner.publishNowPlaying()
            let reason = note.routeReason
            if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue {
                owner.player.pause(); owner.permission.userPause()
                Task { await owner.send("pause") }
            }
        }
    }
    private func observe(_ name: Notification.Name, action: @escaping @MainActor (PlaybackCoordinator, AudioNotice) -> Void) {
        observers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { @Sendable [weak self] note in
            let event = AudioNotice(note)
            MainActor.assumeIsolated { if let self { action(self, event) } }
        })
    }
    func start() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await self.reconcile()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
    }
    func stop() {
        sessionGeneration = UUID(); permission = PlaybackPermission()
        pollTask?.cancel(); pollTask = nil; prepareTask?.cancel(); prepareTask = nil
        player.pause(); player.replaceCurrentItem(with: nil)
        artworkTask?.cancel(); artworkTask = nil; artwork = nil; receiverArtwork = nil; systemArtwork = nil
        loadedKey = nil; desiredKey = nil; state = nil; track = nil; position = 0; duration = 0; busy = false
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        try? audioSession.deactivate()
    }
    func reconcile() async {
        let generation = sessionGeneration
        do {
            let update = try await api.state()
            guard generation == sessionGeneration, !Task.isCancelled else { return }
            await apply(update)
            if ownsOutput, permission.enabled, loadedKey != nil, !permission.interrupted {
                var heartbeat = PlaybackCommand(action: "heartbeat", deviceId: deviceId)
                heartbeat.ownerEpoch = state?.ownerEpoch; heartbeat.intent = state?.intent
                heartbeat.expectedCurrent = state?.currentTrackId; heartbeat.position = position
                heartbeat.duration = duration
                let sequence = heartbeatSequence.next()
                UserDefaults.standard.set(sequence, forKey: "arrify.native.heartbeat-sequence")
                heartbeat.reportSeq = sequence
                heartbeat.observed = player.timeControlStatus == .waitingToPlayAtSpecifiedRate ? "buffering" : (isPlaying ? "playing" : "paused")
                heartbeat.profile = loadedProfile; heartbeat.output = audioSession.outputName
                heartbeat.quality = loadedProfile == "original" ? "Original" : loadedProfile.uppercased()
                let ack = try await api.command(heartbeat)
                guard generation == sessionGeneration else { return }
                state = PlaybackState.merging(ack, into: state)
            }
        } catch { if generation == sessionGeneration { message = "Connection interrupted. Playback will continue where possible." } }
    }
    func play(_ track: Track, in tracks: [Track]) async {
        let ids = Array(tracks.drop(while: { $0.id != track.id }).prefix(1000).map(\.id))
        await send("play", ids: ids.isEmpty ? [track.id] : ids)
    }
    func play(source: LibrarySource, trackId: Int? = nil, shuffle: Bool = false) async {
        await send("play", source: source, trackId: trackId, shuffle: shuffle)
    }
    func cycleRepeat() async {
        let next = state?.repeatMode == "off" ? "all" : state?.repeatMode == "all" ? "one" : "off"
        await send("repeat", mode: next)
    }
    func send(_ action: String, ids: [Int]? = nil, seek: Double? = nil, automatic: Bool = false,
              source: LibrarySource? = nil, trackId: Int? = nil, shuffle: Bool? = nil, mode: String? = nil) async {
        guard !commandInFlight else { return }
        if action == "pause" { player.pause(); permission.userPause() }
        let endedTrack = state?.currentTrackId
        let explicitPlayback = !automatic && ["play", "resume", "takeover", "next", "previous"].contains(action)
        commandInFlight = true
        defer { commandInFlight = false }
        let generation = sessionGeneration
        if explicitPlayback {
            do { try audioSession.activate() }
            catch {
                player.pause(); permission.userPause()
                message = "Audio output is unavailable. Tap Play to try again."
                return
            }
            permission.userPlayAfterSessionActivation()
            // AVPlayer cannot recover a failed item by calling play() on it again.
            if player.currentItem?.status == .failed || (!busy && desiredKey != nil && loadedKey == nil) {
                prepareTask?.cancel(); prepareTask = nil
                desiredKey = nil; loadedKey = nil; retryPreparation = true
            }
        }
        do {
            let fresh = try await api.state()
            guard generation == sessionGeneration else { return }
            state = PlaybackState.merging(fresh, into: state)
            if automatic && (state?.currentTrackId != endedTrack || !ownsOutput) { return }
            var command = PlaybackCommand(action: action, deviceId: deviceId)
            command.deviceName = UIDevice.current.model
            command.expectedRevision = state?.queueRevision; command.ownerEpoch = state?.ownerEpoch
            command.mode = mode
            command.source = source; command.trackId = trackId; command.shuffle = shuffle
            command.ids = ids; command.position = seek; command.expectedCurrent = state?.currentTrackId
            if action == "next" {
                command.transitionId = UUID().uuidString.lowercased()
                command.expectedNext = state?.upcomingTrackIds?.first; command.automatic = automatic
                if automatic && !ownsOutput { return }
            }
            let update = try await api.command(command)
            guard generation == sessionGeneration else { return }
            message = nil
            await apply(update)
        } catch {
            guard generation == sessionGeneration else { return }
            message = error.localizedDescription
            await reconcile()
        }
    }
    func retry() { desiredKey = nil; loadedKey = nil; retryPreparation = true; Task { await send("resume") } }
    private func apply(_ update: PlaybackState) async {
        let previous = state
        state = PlaybackState.merging(update, into: state)
        guard let metadataState = state else { return }
        if permission.enabled, !permission.interrupted, let previous,
           !previous.playing, metadataState.playing, metadataState.intent > previous.intent {
            permission.userPlay()
        }
        if track?.id != metadataState.currentTrackId {
            track = nil; artwork = nil; receiverArtwork = nil; systemArtwork = nil; artworkTask?.cancel()
            player.currentItem?.externalMetadata = []
            if let id = metadataState.currentTrackId {
                let metadata = try? await api.track(id)
                if self.state?.currentTrackId == id {
                    track = metadata
                    artworkTask = Task { [weak self, api] in
                        let bytes = try? await api.artwork(id)
                        guard let self, !Task.isCancelled, self.state?.currentTrackId == id else { return }
                        let decoded = await Task.detached(priority: .utility) { bytes.flatMap { ArtworkDecoder.image($0, pixels: 2048) } }.value
                        guard !Task.isCancelled, self.state?.currentTrackId == id else { return }
                        self.artwork = decoded
                        self.systemArtwork = self.artwork.map { NowPlayingArtwork.make(image: $0) }
                        self.receiverArtwork = self.artwork?.jpegData(compressionQuality: 0.9)
                        self.updateReceiverMetadata()
                        self.publishNowPlaying()
                    }
                }
            }
        }
        guard let state = self.state else { return }
        guard ownsOutput, permission.enabled, let id = state.currentTrackId else {
            prepareTask?.cancel(); prepareTask = nil; player.pause(); player.replaceCurrentItem(with: nil)
            loadedKey = nil; desiredKey = nil; busy = false
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        let key = "\(id):\(state.ownerEpoch):\(state.playId)"
        if desiredKey != key {
            desiredKey = key; prepareTask?.cancel(); player.pause(); loadedKey = nil; busy = true
            prepareTask = Task { [weak self] in await self?.prepare(key: key, id: id, epoch: state.ownerEpoch) }
        }
        if loadedKey == key, seekIntent != state.seekIntent {
            seekIntent = state.seekIntent
            player.seek(to: CMTime(seconds: max(0, state.seekTargetSeconds), preferredTimescale: 600), completionHandler: { _ in })
        }
        if !state.playing { player.pause() } else { playIfAllowed() }
        publishNowPlaying()
    }
    private func prepare(key: String, id: Int, epoch: Int) async {
        let requestedProfile = profile
        let explicitRetry = retryPreparation; retryPreparation = false
        do {
            for attempt in 0..<65 {
                try Task.checkCancellation()
                let manifest = try await api.manifest(deviceId: deviceId, epoch: epoch, profile: requestedProfile, retry: attempt == 0 && explicitRetry)
                guard !Task.isCancelled, desiredKey == key, ownsOutput,
                      manifest.ownerEpoch == epoch else { return }
                guard let item = manifest.items.first(where: { $0.trackId == id }) else { throw APIError.invalidResponse }
                if item.state == "ready" {
                    let url = try await api.mediaURL(item)
                    guard !Task.isCancelled, desiredKey == key else { return }
                    player.replaceCurrentItem(with: AVPlayerItem(url: url))
                    updateReceiverMetadata()
                    loadedKey = key; loadedProfile = requestedProfile; busy = false; message = nil
                    if let state {
                        seekIntent = state.seekIntent
                        player.seek(to: CMTime(seconds: max(0, state.positionSeconds), preferredTimescale: 600), completionHandler: { _ in })
                    }
                    playIfAllowed(); publishNowPlaying(); return
                }
                if ["failed", "unavailable"].contains(item.state) { throw PlaybackFailure() }
                try await Task.sleep(for: .seconds(min(5, max(1, manifest.retryAfterSeconds))))
            }
            throw PlaybackFailure()
        } catch is CancellationError {} catch {
            if desiredKey == key { busy = false; message = "This song could not be prepared. Tap Retry or choose another song." }
        }
    }
    private func playIfAllowed() {
        guard permission.allowsPlayback(ownsOutput: ownsOutput, serverPlaying: state?.playing == true, mediaReady: loadedKey != nil) else { return }
        do { try audioSession.activate(); player.play() }
        catch { player.pause(); permission.userPause(); message = "Audio is unavailable. Tap Play to try again." }
    }
    private func updateReceiverMetadata() {
        guard let track, track.id == state?.currentTrackId else { return }
        player.currentItem?.externalMetadata = ReceiverMetadata.make(track: track, artwork: receiverArtwork)
    }
    private func publishNowPlaying() {
        guard ownsOutput, permission.enabled, loadedKey != nil, let track else { return }
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
private struct PlaybackFailure: Error {}

private struct AudioNotice: Sendable {
    let itemId: ObjectIdentifier?
    let interruptionType: UInt?
    let interruptionOptions: UInt?
    let routeReason: UInt?
    init(_ note: Notification) {
        itemId = (note.object as? AVPlayerItem).map(ObjectIdentifier.init)
        interruptionType = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
        interruptionOptions = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt
        routeReason = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
    }
}
