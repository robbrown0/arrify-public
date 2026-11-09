import ActivityKit
import SwiftUI
import ArrifyCore

/// A read-only remote status card. It never activates audio or registers remote commands.
@MainActor @Observable final class RemotePlaybackActivity {
    let api: APIClient
    var active = false
    var error: String?
    private var activity: Activity<PlaybackActivityAttributes>?
    private var tokenTask: Task<Void, Never>?
    private var generation = UUID()
    init(api: APIClient) { self.api = api }
    func start(state: PlaybackState?, track: Track?, destination: String) async {
        guard let state, let owner = state.ownerDeviceId, let track else { return }
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { error = "Enable Live Activities for Arrify in Settings."; return }
        do {
            guard try await api.liveActivityAvailable() else {
                error = "Lock Screen updates need Apple push notifications configured on your server."; return
            }
            await stop()
            let content = content(state: state, track: track, destination: destination)
            let new = try Activity.request(attributes: PlaybackActivityAttributes(owner: owner), content: content, pushType: .token)
            activity = new; active = true; error = nil
            let token = generation
            tokenTask = Task { [weak self, api] in
                for await bytes in new.pushTokenUpdates {
                    guard let self, self.generation == token, !Task.isCancelled else { return }
                    do {
                        try await api.registerLiveActivity(id: new.id, token: bytes.map { String(format: "%02x", $0) }.joined(), owner: owner)
                    } catch {
                        guard self.generation == token else { return }
                        self.error = "Lock Screen updates couldn’t connect. Open the player and try again."
                        await self.stop(); return
                    }
                }
            }
        } catch { self.error = "Couldn’t start Lock Screen status. Please try again." }
    }
    func update(state: PlaybackState?, track: Track?, destination: String, ownsOutput: Bool) async {
        guard let activity else { return }
        guard !ownsOutput, let state, let track, state.ownerDeviceId == activity.attributes.owner else { await stop(); return }
        if activity.activityState == .dismissed || activity.activityState == .ended { await stop(); return }
        let next = content(state: state, track: track, destination: destination)
        let old = activity.content.state
        if old.title != next.state.title || old.artist != next.state.artist || old.destination != next.state.destination || old.playing != next.state.playing || Date().timeIntervalSince1970 - old.updatedAt >= 60 {
            await activity.update(next)
        }
    }
    private func content(state: PlaybackState, track: Track, destination: String) -> ActivityContent<PlaybackActivityAttributes.ContentState> {
        ActivityContent(state: .init(title: track.title, artist: track.artist, destination: destination, playing: state.playing, updatedAt: Date().timeIntervalSince1970), staleDate: Date().addingTimeInterval(90))
    }
    func stop() async {
        generation = UUID(); tokenTask?.cancel(); tokenTask = nil
        let previous = activity; activity = nil; active = false
        if let previous {
            await previous.end(nil, dismissalPolicy: .immediate)
            try? await api.stopLiveActivity(id: previous.id)
        }
    }
    func clearRestoredActivities() async {
        for old in Activity<PlaybackActivityAttributes>.activities {
            await old.end(nil, dismissalPolicy: .immediate)
            try? await api.stopLiveActivity(id: old.id)
        }
    }
}
