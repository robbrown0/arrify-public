import SwiftUI
import ArrifyCore
import AVKit
import MediaPlayer

struct MiniPlayerView: View {
    @Bindable var playback: PlaybackCoordinator
    let expand: () -> Void
    var body: some View {
        if let track = playback.track {
            VStack(spacing: 0) {
                HStack(spacing: 2) {
                    Button(action: expand) {
                        HStack(spacing: 10) {
                            ArtworkView(image: playback.artwork).frame(width: 42, height: 42)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(track.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                                Text(track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel("Now playing, \(track.title), \(track.artist)")
                    AirPlayPicker().frame(width: 44, height: 44).accessibilityIdentifier("miniAirPlay")
                    Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                        Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill")
                            .font(.system(size: 21, weight: .semibold)).frame(width: 44, height: 44)
                    }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play")
                    Button { Task { await playback.send("next") } } label: {
                        Image(systemName: "forward.end.fill").font(.system(size: 19)).frame(width: 44, height: 44)
                    }.accessibilityLabel("Next song")
                }.padding(.horizontal, 10).padding(.vertical, 8)
                GeometryReader { proxy in
                    Rectangle().fill(.white.opacity(0.16)).overlay(alignment: .leading) {
                        Rectangle().fill(.white).frame(width: proxy.size.width * playback.progress)
                    }
                }.frame(height: 2).accessibilityHidden(true)
            }.foregroundStyle(.white).accessibilityIdentifier("miniPlayer")
        }
    }
}

struct NowPlayingView: View {
    @Bindable var playback: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss
    @State private var seeking = false
    @State private var seekPosition = 0.0
    @State private var showQueue = false
    @State private var showArtwork = false
    var body: some View {
        GeometryReader { proxy in
            let landscape = proxy.size.width > proxy.size.height
            ZStack {
                PlayerBackdrop(image: playback.artwork)
                ScrollView {
                    VStack(spacing: 20) {
                        HStack {
                            Button("Close player", systemImage: "chevron.down") { dismiss() }
                                .labelStyle(.iconOnly).frame(width: 44, height: 44)
                            Spacer()
                            Menu {
                                Button("Show artwork", systemImage: "photo") { showArtwork = true }
                                Button("Up next", systemImage: "list.bullet") { showQueue = true }
                            } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44) }
                            .accessibilityLabel("More player options")
                        }
                        if landscape {
                            HStack(alignment: .center, spacing: 36) {
                                cover.frame(width: min(proxy.size.width * 0.4, max(120, proxy.size.height - 110)))
                                controls.frame(maxWidth: 480)
                            }
                        } else {
                            cover.frame(maxWidth: min(440, max(180, proxy.size.height * 0.43)))
                                .padding(.bottom, 12)
                            controls.frame(maxWidth: 520)
                        }
                    }.padding(.horizontal, 24).padding(.bottom, 24)
                        .frame(maxWidth: .infinity)
                }
            }.foregroundStyle(.white).tint(.white)
        }.sheet(isPresented: $showQueue) { UpNextView(playback: playback) }
            .fullScreenCover(isPresented: $showArtwork) { ArtworkPresentationView(playback: playback) }
    }
    private var cover: some View {
        Button { showArtwork = true } label: {
            ArtworkView(image: playback.artwork).aspectRatio(1, contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .shadow(color: .black.opacity(0.25), radius: 20, y: 12)
        }.buttonStyle(.plain).accessibilityLabel("Show artwork")
    }
    private var controls: some View {
        VStack(spacing: 18) {
            VStack(alignment: .leading, spacing: 5) {
                Text(playback.track?.title ?? "Nothing playing").font(.title2.bold()).lineLimit(2)
                Text(playback.track?.artist ?? "").font(.title3).foregroundStyle(.white.opacity(0.65)).lineLimit(2)
            }.frame(maxWidth: .infinity, alignment: .leading)
            if let message = playback.message {
                HStack { Text(message).font(.caption); Button("Retry") { playback.retry() } }
            }
            if playback.hasOtherOutput { Button("Play here") { Task { await playback.send("takeover") } } }
            VStack(spacing: 4) {
                Slider(value: Binding(get: { seeking ? seekPosition : min(playback.position, max(1, playback.duration)) }, set: { seekPosition = $0 }), in: 0...max(1, playback.duration)) { editing in
                    if editing { seekPosition = playback.position }
                    seeking = editing
                    if !editing { Task { await playback.send("seek", seek: seekPosition) } }
                }.disabled(playback.duration <= 0 || !playback.ownsOutput).accessibilityLabel("Playback position")
                HStack {
                    Text(time(seeking ? seekPosition : playback.position)); Spacer()
                    Text("−" + time(max(0, playback.duration - (seeking ? seekPosition : playback.position))))
                }.font(.caption.monospacedDigit()).foregroundStyle(.white.opacity(0.55))
            }
            HStack {
                Button { Task { await playback.send("shuffle") } } label: {
                    Image(systemName: "shuffle").font(.title3).frame(width: 44, height: 44)
                }.accessibilityLabel("Shuffle upcoming songs").disabled(playback.state?.queueControlsAvailable != true)
                Spacer(minLength: 4)
                Button { Task { await playback.send("previous") } } label: {
                    Image(systemName: "backward.end.fill").font(.system(size: 27)).frame(width: 44, height: 44)
                }.accessibilityLabel("Previous song")
                Spacer(minLength: 4)
                Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                    ZStack {
                        Circle().fill(.white)
                        if playback.busy { ProgressView().tint(.black) }
                        else { Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill").font(.system(size: 30, weight: .semibold)).foregroundStyle(.black) }
                    }.frame(width: 68, height: 68)
                }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play")
                Spacer(minLength: 4)
                Button { Task { await playback.send("next") } } label: {
                    Image(systemName: "forward.end.fill").font(.system(size: 27)).frame(width: 44, height: 44)
                }.accessibilityLabel("Next song")
                Spacer(minLength: 4)
                Button { Task { await playback.cycleRepeat() } } label: {
                    Image(systemName: playback.state?.repeatMode == "one" ? "repeat.1" : "repeat")
                        .font(.title3).foregroundStyle(playback.state?.repeatMode == "off" ? Color.white : Color.green)
                        .frame(width: 44, height: 44)
                }.accessibilityLabel("Repeat").accessibilityValue(playback.state?.repeatMode ?? "off")
                    .disabled(playback.state?.queueControlsAvailable != true)
            }.buttonStyle(.plain)
            SystemVolumeSlider().frame(height: 32).padding(.top, 6)
            HStack {
                Button { showArtwork = true } label: { Image(systemName: "photo").frame(width: 44, height: 44) }
                    .accessibilityLabel("Show artwork").accessibilityIdentifier("artworkDisplayButton")
                Spacer()
                VStack(spacing: 0) {
                    AirPlayPicker().frame(width: 54, height: 44).accessibilityIdentifier("playerAirPlay")
                    Text(playback.outputName).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button { showQueue = true } label: { Image(systemName: "list.bullet").frame(width: 44, height: 44) }
                    .accessibilityLabel("Up next").accessibilityIdentifier("upNext")
            }.font(.title3)
        }
    }
    private func time(_ seconds: Double) -> String {
        let n = Int(seconds.isFinite ? max(0, seconds) : 0)
        return "\(n / 60):\(String(format: "%02d", n % 60))"
    }
}

struct PlayerBackdrop: View {
    let image: UIImage?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color(white: 0.09)
                if let image, !reduceTransparency {
                    Image(uiImage: image).resizable().scaledToFill()
                        .frame(width: proxy.size.width, height: proxy.size.height)
                        .blur(radius: 70).opacity(0.35)
                }
                LinearGradient(colors: [.clear, .black.opacity(0.65)], startPoint: .top, endPoint: .bottom)
            }.clipped()
        }.ignoresSafeArea().accessibilityHidden(true)
    }
}

struct UpNextView: View {
    @Bindable var playback: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss
    @State private var tracks: [Track] = []
    @State private var loading = true
    @State private var failed = false
    var body: some View {
        NavigationStack {
            List {
                if let track = playback.track {
                    Section("Playing") { row(track) }
                }
                Section("Next songs") {
                    if loading { ProgressView() }
                    else if failed { Text("Could not load the next songs.").foregroundStyle(.secondary) }
                    else if tracks.isEmpty { Text("Nothing queued.").foregroundStyle(.secondary) }
                    ForEach(Array(tracks.enumerated()), id: \.offset) { _, track in row(track) }
                }
            }.navigationTitle("Up Next").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
                .task(id: playback.state?.upcomingTrackIds ?? []) {
                    loading = true; failed = false; tracks = []
                    var loaded: [Track] = []
                    do {
                        // The native snapshot advertises the next two songs, not an editable full queue.
                        for id in (playback.state?.upcomingTrackIds ?? []).prefix(2) {
                            loaded.append(try await playback.api.track(id)); try Task.checkCancellation()
                        }
                        tracks = loaded; loading = false
                    } catch { if !Task.isCancelled { failed = true; loading = false } }
                }
        }.presentationDetents([.medium, .large])
    }
    private func row(_ track: Track) -> some View {
        VStack(alignment: .leading, spacing: 4) { Text(track.title); Text(track.artist).font(.subheadline).foregroundStyle(.secondary) }
    }
}

/// A clean foreground canvas for screen mirroring; audio routing remains system-owned.
struct ArtworkPresentationView: View {
    @Bindable var playback: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color.black.ignoresSafeArea()
                ArtworkView(image: playback.artwork).frame(width: min(proxy.size.width, proxy.size.height), height: min(proxy.size.width, proxy.size.height))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                VStack {
                    HStack { Button("Close artwork", systemImage: "xmark") { dismiss() }.labelStyle(.iconOnly).frame(width: 44, height: 44).background(.ultraThinMaterial, in: Circle()); Spacer() }
                    Spacer()
                }.padding()
            }.tint(.white)
        }.accessibilityIdentifier("artworkPresentation")
    }
}

struct AirPlayPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView(); view.tintColor = .white; view.activeTintColor = .systemGreen
        view.prioritizesVideoDevices = false
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
struct SystemVolumeSlider: UIViewRepresentable {
    func makeUIView(context: Context) -> MPVolumeView {
        let view = MPVolumeView(); view.showsRouteButton = false; view.tintColor = .white
        return view
    }
    func updateUIView(_ uiView: MPVolumeView, context: Context) {}
}
