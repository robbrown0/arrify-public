import ActivityKit
import SwiftUI
import WidgetKit
import UIKit

@main struct ArrifyLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: PlaybackActivityAttributes.self) { context in
            HStack(spacing: 14) {
                ActivityCover(encoded: context.state.artwork).frame(width: 44, height: 44)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Playing on \(context.state.destination)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Text(context.state.title).font(.headline).lineLimit(1)
                    Text(context.state.artist).font(.subheadline).lineLimit(1)
                    if context.isStale { Text("Open Arrify to refresh").font(.caption).foregroundStyle(.secondary) }
                }
                Spacer(minLength: 0)
                HStack(spacing: 8) {
                    Button(intent: RemotePlaybackIntent(activityID: context.activityID, command: "previous")) { Image(systemName: "backward.end.fill").frame(width: 36, height: 44) }.accessibilityLabel("Previous song")
                    Button(intent: RemotePlaybackIntent(activityID: context.activityID, command: context.state.playing ? "pause" : "resume")) { Image(systemName: context.state.playing ? "pause.fill" : "play.fill").frame(width: 36, height: 44) }.accessibilityLabel(context.state.playing ? "Pause" : "Play")
                    Button(intent: RemotePlaybackIntent(activityID: context.activityID, command: "next")) { Image(systemName: "forward.end.fill").frame(width: 36, height: 44) }.accessibilityLabel("Next song")
                }.buttonStyle(.plain)
            }.padding().foregroundStyle(.white).activityBackgroundTint(.black).activitySystemActionForegroundColor(.white)
                .widgetURL(URL(string: "arrify://player"))
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Image(systemName: "music.note").foregroundStyle(.green) }
                DynamicIslandExpandedRegion(.center) {
                    VStack {
                        Text(context.state.title).font(.headline).lineLimit(1)
                        Text(context.state.artist).font(.caption).lineLimit(1)
                    }
                }
                DynamicIslandExpandedRegion(.bottom) {
                    Text(context.isStale ? "Open Arrify to refresh" : "Playing on \(context.state.destination)").font(.caption).lineLimit(1)
                }
            } compactLeading: { Image(systemName: "music.note").foregroundStyle(.green) }
            compactTrailing: { Image(systemName: context.state.playing ? "waveform" : "pause.fill") }
            minimal: { Image(systemName: "music.note") }
                .widgetURL(URL(string: "arrify://player"))
        }
    }
}

private struct ActivityCover: View {
    let encoded: String?
    var body: some View {
        if let encoded, encoded.utf8.count <= 2000, let bytes = Data(base64Encoded: encoded), let image = UIImage(data: bytes) {
            Image(uiImage: image).resizable().scaledToFill().clipShape(RoundedRectangle(cornerRadius: 6))
        } else {
            Image(systemName: "music.note").font(.title).foregroundStyle(.green)
        }
    }
}
