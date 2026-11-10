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
            let (nonce, proof) = try await LocalPeerVerification.proof(api: client, owner: activity.attributes.owner, epoch: activity.attributes.ownerEpoch, purpose: command)
            try Task.checkCancellation()
            let state = try await client.liveActivityCommand(id: activityID, owner: activity.attributes.owner,
                epoch: activity.attributes.ownerEpoch, command: command, nonce: nonce, proof: proof)
            guard let id = state.currentTrackId else { await activity.end(nil, dismissalPolicy: .immediate); return }
            let track = try await client.track(id)
            await activity.update(ActivityContent(state: .init(title: String(track.title.prefix(100)), artist: String(track.artist.prefix(100)),
                destination: String((state.output ?? activity.content.state.destination).prefix(80)), playing: state.playing,
                updatedAt: Date().timeIntervalSince1970, artwork: activity.content.state.trackID == id ? activity.content.state.artwork : nil, trackID: id), staleDate: Date().addingTimeInterval(90)))
            let thumbnail = await ActivityThumbnail.load(api: client, trackID: id)
            if activity.content.state.trackID == id, !Task.isCancelled {
                var content = activity.content.state; content.artwork = thumbnail
                await activity.update(ActivityContent(state: content, staleDate: activity.content.staleDate))
            }
        } catch {
            // Keep the last confirmed state and mark it stale; never claim an unconfirmed action succeeded.
            await activity.update(ActivityContent(state: activity.content.state, staleDate: Date()))
        }
    }
}
