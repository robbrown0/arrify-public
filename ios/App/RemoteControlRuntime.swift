@preconcurrency import ActivityKit
import ArrifyCore
import Foundation

@MainActor enum RemoteControlRuntime {
    static var api: APIClient?
    static func perform(activityID: String, command: String) async {
        guard ["pause", "resume", "previous", "next"].contains(command),
              let activity = Activity<PlaybackActivityAttributes>.activities.first(where: { $0.id == activityID }),
              activity.activityState == .active || activity.activityState == .stale else { return }
        do {
            let client: APIClient
            if let api { client = api }
            else {
                client = APIClient(store: KeychainStore())
                guard try await client.restore() != nil else { return }
                api = client
            }
            guard try await client.sessionScope() == activity.attributes.sessionScope else { return }
            let state = try await client.liveActivityCommand(id: activityID, owner: activity.attributes.owner,
                epoch: activity.attributes.ownerEpoch, command: command)
            guard let id = state.currentTrackId else { await activity.end(nil, dismissalPolicy: .immediate); return }
            let track = try await client.track(id)
            await activity.update(ActivityContent(state: .init(title: track.title, artist: track.artist,
                destination: state.output ?? activity.content.state.destination, playing: state.playing,
                updatedAt: Date().timeIntervalSince1970), staleDate: Date().addingTimeInterval(90)))
        } catch {
            // Keep the last confirmed state and mark it stale; never claim an unconfirmed action succeeded.
            await activity.update(ActivityContent(state: activity.content.state, staleDate: Date()))
        }
    }
}
