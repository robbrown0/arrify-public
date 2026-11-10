// ActivityKit activity handles have incomplete Swift 6 isolation annotations.
// All handle ownership and mutations stay in this main-actor coordinator.
@preconcurrency import ActivityKit
import SwiftUI
import ArrifyCore

/// A remote control card; never activates audio on the controlling device.
@MainActor @Observable final class RemotePlaybackActivity {
    let api: APIClient
    var active = false
    var automatic = UserDefaults.standard.object(forKey: "remoteLockScreenAutomatic") as? Bool ?? true {
        didSet {
            UserDefaults.standard.set(automatic, forKey: "remoteLockScreenAutomatic")
            if !automatic { Task { await stop() } }
            dismissedOwner = nil
            error = nil
            lastAttempt = .distantPast
        }
    }
    var status: String {
        if !automatic { return "Off" }
        if starting { return "Connecting Lock Screen controls…" }
        if active { return "Lock Screen controls are active." }
        if dismissedOwner != nil { return "Dismissed for this playback session. Switch this option off and on to show controls again." }
        return "Waiting for playback from another device while Arrify is open."
    }
    private var dismissedOwner: String?
    private var lastAttempt = Date.distantPast
    private var starting = false
    private var thumbnailID: Int?
    private var thumbnail: String?
    private var thumbnailTask: Task<Void, Never>?
    var error: String?
    private var activity: Activity<PlaybackActivityAttributes>?
    private var tokenTask: Task<Void, Never>?
    private var generation = UUID()
    init(api: APIClient) { self.api = api }
    func start(state: PlaybackState?, track: Track?, destination: String) async {
        guard automatic, UIApplication.shared.applicationState == .active, !starting, let state, let owner = state.ownerDeviceId, let track, track.id == state.currentTrackId else { return }
        guard dismissedOwner != "\(owner):\(state.ownerEpoch)" else { return }
        starting = true
        defer { starting = false }
        let requestedGeneration = generation
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { error = "Enable Live Activities for Arrify in Settings."; return }
        do {
            let (nonce, proof) = try await LocalPeerVerification.proof(api: api, owner: owner, epoch: state.ownerEpoch, purpose: "status")
            guard try await api.verifyLocalPresence(owner: owner, epoch: state.ownerEpoch, nonce: nonce, proof: proof) else {
                error = "Lock Screen controls need both devices on the same local network and server push notifications."; return
            }
            guard requestedGeneration == generation, !Task.isCancelled else { return }
            let stoppedGeneration = await stop()
            guard stoppedGeneration == generation, !Task.isCancelled else { return }
            let content = content(state: state, track: track, destination: destination)
            let scope = try await api.sessionScope()
            guard automatic, UIApplication.shared.applicationState == .active, stoppedGeneration == generation else { return }
            let new = try Activity.request(attributes: PlaybackActivityAttributes(owner: owner, ownerEpoch: state.ownerEpoch, sessionScope: scope), content: content, pushType: .token)
            activity = new; active = true; error = nil
            observePushToken(new)
            requestThumbnail(track.id)
        } catch {
            let detail = error as NSError
            if let apiError = error as? APIError {
                self.error = apiError.localizedDescription
            } else {
                self.error = "Lock Screen unavailable (\(detail.domain), \(detail.code)). Open Arrify in the foreground to retry."
            }
        }
    }
    private func observePushToken(_ observedActivity: Activity<PlaybackActivityAttributes>) {
        tokenTask?.cancel()
        let token = generation
        tokenTask = Task { [weak self, api] in
            for await bytes in observedActivity.pushTokenUpdates {
                guard let self, self.generation == token, !Task.isCancelled else { return }
                do {
                    try await api.registerLiveActivity(id: observedActivity.id, token: bytes.map { String(format: "%02x", $0) }.joined(), owner: observedActivity.attributes.owner)
                } catch {
                    guard self.generation == token else { return }
                    self.error = "Lock Screen updates couldn’t connect. Open Arrify to retry."
                    await self.stop(); return
                }
            }
        }
    }
    func update(state: PlaybackState?, track: Track?, destination: String, ownsOutput: Bool) async {
        guard automatic else { if active { await stop() }; return }
        guard !ownsOutput, let state, state.ownerDeviceId != nil, let track else { if active { await stop() }; return }
        guard let activity else {
            if UIApplication.shared.applicationState == .active, Date().timeIntervalSince(lastAttempt) >= 15 {
                lastAttempt = Date()
                await start(state: state, track: track, destination: destination)
            }
            return
        }
        guard state.ownerDeviceId == activity.attributes.owner, state.ownerEpoch == activity.attributes.ownerEpoch else { await stop(); return }
        if activity.activityState == .dismissed { dismissedOwner = "\(activity.attributes.owner):\(activity.attributes.ownerEpoch)"; await stop(); return }
        if activity.activityState == .ended { await stop(); return }
        guard track.id == state.currentTrackId else { return }
        requestThumbnail(track.id)
        let next = content(state: state, track: track, destination: destination)
        let old = activity.content.state
        if old.trackID != next.state.trackID || old.artwork != next.state.artwork || old.title != next.state.title || old.artist != next.state.artist || old.destination != next.state.destination || old.playing != next.state.playing || Date().timeIntervalSince1970 - old.updatedAt >= 60 {
            await activity.update(next)
        }
    }
    private func requestThumbnail(_ id: Int) {
        guard thumbnailID != id else { return }
        thumbnailID = id; thumbnail = nil; thumbnailTask?.cancel()
        let token = generation
        thumbnailTask = Task { [weak self, api] in
            let value = await ActivityThumbnail.load(api: api, trackID: id)
            guard let self, !Task.isCancelled, self.generation == token, self.thumbnailID == id else { return }
            self.thumbnail = value
            guard let value, let activity = self.activity, activity.content.state.trackID == id else { return }
            var state = activity.content.state; state.artwork = value
            await activity.update(ActivityContent(state: state, staleDate: activity.content.staleDate))
        }
    }
    private func content(state: PlaybackState, track: Track, destination: String) -> ActivityContent<PlaybackActivityAttributes.ContentState> {
        ActivityContent(state: .init(title: String(track.title.prefix(100)), artist: String(track.artist.prefix(100)), destination: String(destination.prefix(80)), playing: state.playing, updatedAt: Date().timeIntervalSince1970, artwork: (thumbnailID == track.id ? thumbnail : nil) ?? (activity?.content.state.trackID == track.id ? activity?.content.state.artwork : nil), trackID: track.id), staleDate: Date().addingTimeInterval(90))
    }
    @discardableResult func stop() async -> UUID {
        let nextGeneration = UUID(); generation = nextGeneration; tokenTask?.cancel(); tokenTask = nil
        thumbnailTask?.cancel(); thumbnailTask = nil; thumbnailID = nil; thumbnail = nil
        let previous = activity; activity = nil; active = false
        if let previous {
            await previous.end(nil, dismissalPolicy: .immediate)
            try? await api.stopLiveActivity(id: previous.id)
        }
        return nextGeneration
    }
    func restoreForSession() async {
        let scope = try? await api.sessionScope()
        for old in Activity<PlaybackActivityAttributes>.activities {
            if automatic, old.activityState != .ended, old.activityState != .dismissed, old.attributes.sessionScope == scope, activity == nil {
                activity = old; active = true; observePushToken(old)
            } else {
                await old.end(nil, dismissalPolicy: .immediate)
                try? await api.stopLiveActivity(id: old.id)
            }
        }
    }
}
