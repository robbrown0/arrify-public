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
                HStack(spacing: 8) {
                    Button(action: expand) {
                        HStack(spacing: 10) {
                            ArtworkView(image: playback.artwork).frame(width: 40, height: 40)
                                .clipShape(RoundedRectangle(cornerRadius: 7))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(track.title).font(.subheadline.weight(.semibold)).lineLimit(1)
                                Text(track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel("Now playing, \(track.title), \(track.artist)").accessibilityIdentifier("expandPlayer")
                    if !playback.serverConnection.connected || playback.serverConnection.degraded { ServerConnectionBadge(connection: playback.serverConnection) }
                    AirPlayPicker(onDismiss: { playback.outputSelectionChanged() }, onPresent: { playback.refreshOutput(trigger: "picker-presented") }).frame(width: 44, height: 44).accessibilityIdentifier("miniAirPlay")
                    Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                        Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill")
                            .font(.system(size: 21, weight: .semibold)).frame(width: 44, height: 44).overlay { PlaybackProgressRing(progress: playback.progress).padding(3) }
                    }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play")
                    Button { Task { await playback.send("next") } } label: {
                        Image(systemName: "forward.end.fill").opacity(playback.skipPending ? 0 : 1).overlay { if playback.skipPending { ProgressView().tint(.green) } }.font(.system(size: 19)).frame(width: 44, height: 44)
                    }.accessibilityLabel(playback.skipPending ? "Skipping" : "Next song").disabled(playback.skipPending)
                }.padding(.horizontal, 10).padding(.vertical, 8)
            }.foregroundStyle(.white)
        }
    }
}

struct NowPlayingView: View {
    let model: AppModel
    private var playback: PlaybackCoordinator { model.playback }
    private var favorites: FavoritesStore { model.favorites }
    @Environment(\.dismiss) private var dismiss
    @State private var seeking = false
    @State private var seekPosition = 0.0
    @State private var showQueue = false
    @State private var showArtwork = false
    var body: some View {
        GeometryReader { proxy in
            let landscape = proxy.size.width > proxy.size.height
            let tablet = UIDevice.current.userInterfaceIdiom == .pad
            ZStack {
                PlayerBackdrop(image: playback.artwork)
                ScrollView {
                    VStack(spacing: 20) {
                        HStack {
                            Button("Close player", systemImage: "chevron.down") { dismiss() }
                                .labelStyle(.iconOnly).frame(width: 44, height: 44)
                            Text(playback.contextName).font(.subheadline.weight(.semibold)).lineLimit(1).frame(maxWidth: .infinity)
                            if let track = playback.track {
                                SongActionsMenu(track: track, model: model)
                            }
                        }
                        if landscape {
                            HStack(alignment: .center, spacing: 36) {
                                cover.frame(width: min(proxy.size.width * 0.4, max(120, proxy.size.height - 110)))
                                controls.frame(maxWidth: 480)
                            }
                        } else {
                            PortraitPlayerLayout(
                                availableHeight: max(0, proxy.size.height - 100),
                                maximumArtworkWidth: tablet ? proxy.size.width * 0.68 : proxy.size.width - 48,
                                maximumControlsWidth: tablet ? 640 : 520
                            ) {
                                cover
                                controls
                            }
                        }
                    }.padding(.horizontal, 24).padding(.top, 12).padding(.bottom, 24)
                        .frame(maxWidth: .infinity, minHeight: proxy.size.height, alignment: .top)
                }
            }.accessibilityIdentifier("fullScreenPlayer").foregroundStyle(.white).tint(.white)
        }.onAppear { PlaybackDiagnostics.record("expanded-player-appeared") }
            .onDisappear { PlaybackDiagnostics.record("expanded-player-disappeared") }
            .onChange(of: showQueue) { PlaybackDiagnostics.record("queue-presented=\(showQueue)") }
            .onChange(of: showArtwork) { PlaybackDiagnostics.record("artwork-presented=\(showArtwork)") }
            .sheet(isPresented: $showQueue) { UpNextView(playback: playback) }
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
            HStack {
            VStack(alignment: .leading, spacing: 5) {
                Text(playback.track?.title ?? "Nothing playing").font(.title2.bold()).lineLimit(2)
                Text(playback.track?.artist ?? "").font(.title3).foregroundStyle(.white.opacity(0.65)).lineLimit(2)
            }.frame(maxWidth: .infinity, alignment: .leading)
            if let track = playback.track { FavoriteButton(favorites: favorites, track: track) }
            }
            if let error = favorites.error { Text(error).font(.caption).foregroundStyle(.orange) }
            if let message = playback.message {
                HStack { Text(message).font(.caption); Button("Retry") { playback.retry() } }
            }
            if let transition = playback.transitionMessage { Text(transition).font(.caption).foregroundStyle(.secondary).accessibilityIdentifier("playbackTransition") }
            if playback.hasOtherOutput {
                HStack {
                    Button("Play here") { Task { await playback.send("takeover") } }
                    Spacer()
                }.font(.footnote)
            }
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
                    Image(systemName: "shuffle").font(.title3).foregroundStyle(playback.state?.shuffle == true ? Color.green : Color.white).frame(width: 44, height: 44)
                }.accessibilityLabel("Shuffle upcoming songs").accessibilityValue(playback.state?.shuffle == true ? "on" : "off").disabled(playback.state?.queueControlsAvailable != true)
                Spacer(minLength: 4)
                Button { Task { await playback.send("previous") } } label: {
                    Image(systemName: "backward.end.fill").font(.system(size: 27)).frame(width: 44, height: 44)
                }.accessibilityLabel("Previous song").disabled(playback.skipPending)
                Spacer(minLength: 4)
                Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                    ZStack {
                        Circle().fill(.white)
                        if playback.busy { ProgressView().tint(.black) }
                        else { Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill").font(.system(size: 30, weight: .semibold)).foregroundStyle(.black) }
                    }.frame(width: 68, height: 68).padding(5).overlay { PlaybackProgressRing(progress: playback.progress) }
                }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play").accessibilityIdentifier("expandedPlayPause")
                Spacer(minLength: 4)
                Button { Task { await playback.send("next") } } label: {
                    Image(systemName: "forward.end.fill").opacity(playback.skipPending ? 0 : 1).overlay { if playback.skipPending { ProgressView().tint(.green) } }.font(.system(size: 27)).frame(width: 44, height: 44)
                }.accessibilityLabel(playback.skipPending ? "Skipping" : "Next song").disabled(playback.skipPending)
                Spacer(minLength: 4)
                Button { Task { await playback.cycleRepeat() } } label: {
                    Image(systemName: playback.state?.repeatMode == "one" ? "repeat.1" : "repeat")
                        .font(.title3).foregroundStyle(playback.state?.repeatMode == "off" ? Color.white : Color.green)
                        .frame(width: 44, height: 44)
                }.accessibilityLabel("Repeat").accessibilityValue(playback.state?.repeatMode ?? "off")
                    .disabled(playback.state?.queueControlsAvailable != true)
            }.buttonStyle(.plain)
            SystemVolumeSlider(retainedView: playback.expandedVolumeView).frame(height: 44).padding(.top, 6)
            HStack(spacing: 0) {
                HStack(spacing: 4) {
                    ServerConnectionBadge(connection: playback.serverConnection)
                }.frame(width: 92, alignment: .leading)
                VStack(spacing: 0) {
                    AirPlayPicker(onDismiss: { playback.outputSelectionChanged() }, onPresent: { playback.refreshOutput(trigger: "picker-presented") }).frame(width: 54, height: 44).accessibilityIdentifier("playerAirPlay")
                    Text(playback.routeDescription).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        .accessibilityIdentifier("playerOutputDescription")
                }.frame(maxWidth: .infinity)
                Button { showQueue = true } label: { Image(systemName: "list.bullet").frame(width: 44, height: 44) }
                    .accessibilityLabel("Up next").accessibilityIdentifier("upNext")
                    .frame(width: 92, alignment: .trailing)
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
    @State private var entries: [QueueEntry] = []
    @State private var revision: Int?
    @State private var nextOffset: Int?
    @State private var total = 0
    @State private var editing = false
    @State private var loading = false
    @State private var loadGeneration = UUID()
    @State private var error: String?
    var body: some View {
        NavigationStack {
            List {
                if let track = playback.track {
                    Section(playback.contextName) { trackRow(track) }
                }
                Section("Up Next · \(total)") {
                    if let error { Text(error).foregroundStyle(.orange) }
                    ForEach(entries) { entry in
                        Button { Task { await change("queue_jump", index: entry.index) } } label: {
                            HStack { trackRow(entry.track); Spacer(); Image(systemName: "play.fill").font(.caption) }
                        }.buttonStyle(.plain).accessibilityLabel("Play queued \(entry.track.title)")
                            .contextMenu {
                                Button("Play now", systemImage: "play.fill") { Task { await change("queue_jump", index: entry.index) } }
                                if playback.state?.queueControlsAvailable == true {
                                    Button("Play next", systemImage: "text.line.first.and.arrowtriangle.forward") { Task { await change("reorder", from: entry.index, to: 0) } }
                                    Button("Remove", systemImage: "minus.circle", role: .destructive) { Task { await change("remove", index: entry.index) } }
                                }
                            }
                    }.onDelete { offsets in
                        guard let first = offsets.first, entries.indices.contains(first) else { return }
                        Task { await change("remove", index: entries[first].index) }
                    }.onMove { offsets, destination in
                        guard let first = offsets.first, entries.indices.contains(first) else { return }
                        let target = destination > first ? destination - 1 : destination
                        guard entries.indices.contains(target) else { return }
                        Task { await change("reorder", from: entries[first].index, to: entries[target].index) }
                    }.deleteDisabled(playback.state?.queueControlsAvailable != true)
                        .moveDisabled(playback.state?.queueControlsAvailable != true)
                    if loading { ProgressView() }
                    else if let nextOffset { Button("Load more songs") { Task { await load(offset: nextOffset) } } }
                    else if entries.isEmpty { Text("Nothing queued.").foregroundStyle(.secondary) }
                }
            }.navigationTitle("Queue").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) { if playback.state?.queueControlsAvailable == true { EditButton() } }
                    ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
                }
                .task(id: playback.state?.queueRevision) { await load() }
                .refreshable { await load() }
                .disabled(editing)
        }.presentationDetents([.large])
    }
    private func trackRow(_ track: Track) -> some View {
        VStack(alignment: .leading, spacing: 4) { Text(track.title).lineLimit(2); Text(track.artist).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
    }
    private func load(offset: Int = 0) async {
        guard offset == 0 || !loading else { return }
        let generation = UUID(); loadGeneration = generation; loading = true; error = nil
        defer { if loadGeneration == generation { loading = false } }
        do {
            let page = try await playback.api.queue(offset: offset, revision: offset == 0 ? nil : revision)
            guard loadGeneration == generation, !Task.isCancelled else { return }
            if offset == 0 { entries = page.items } else { entries += page.items }
            revision = page.revision; nextOffset = page.nextOffset; total = page.total
        } catch {
            if loadGeneration == generation, !Task.isCancelled { self.error = "Queue changed or unavailable. Pull to refresh." }
        }
    }
    private func change(_ action: String, index: Int? = nil, from: Int? = nil, to: Int? = nil) async {
        guard !editing else { return }; editing = true
        defer { editing = false }
        await playback.send(action, index: index, from: from, to: to, revision: revision)
        await load()
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
    var onDismiss: () -> Void = {}
    var onPresent: () -> Void = {}
    func makeCoordinator() -> Coordinator { Coordinator(onDismiss: onDismiss, onPresent: onPresent) }
    final class Coordinator: NSObject, AVRoutePickerViewDelegate {
        var onDismiss: () -> Void
        var onPresent: () -> Void
        init(onDismiss: @escaping () -> Void, onPresent: @escaping () -> Void) { self.onDismiss = onDismiss; self.onPresent = onPresent }
        func routePickerViewWillBeginPresentingRoutes(_ routePickerView: AVRoutePickerView) { onPresent() }
        func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) { onDismiss() }
    }
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView(); view.tintColor = .white; view.activeTintColor = .systemGreen
        view.prioritizesVideoDevices = false; view.delegate = context.coordinator
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {
        context.coordinator.onDismiss = onDismiss; context.coordinator.onPresent = onPresent
    }
}
// Keep the native control's identity stable during route-name and playback updates.
// MediaPlayer owns receiver-volume synchronization; never seed it from the phone's
// outputVolume or write a remembered value when this view appears.
final class CenteredVolumeView: MPVolumeView {
    override func volumeSliderRect(forBounds bounds: CGRect) -> CGRect {
        let rect = super.volumeSliderRect(forBounds: bounds)
        return CGRect(x: rect.minX, y: bounds.midY - rect.height / 2, width: rect.width, height: rect.height)
    }
}

struct SystemVolumeSlider: UIViewRepresentable {
    var retainedView: MPVolumeView? = nil
    func makeUIView(context: Context) -> MPVolumeView {
        let view = retainedView ?? CenteredVolumeView(frame: CGRect(x: 0, y: 0, width: 300, height: 44)); view.showsRouteButton = false; view.tintColor = .white
        return view
    }
    func updateUIView(_ uiView: MPVolumeView, context: Context) {}
    func sizeThatFits(_ proposal: ProposedViewSize, uiView: MPVolumeView, context: Context) -> CGSize? {
        CGSize(width: proposal.width ?? 300, height: proposal.height ?? 44)
    }
}

struct TabletPlayerBar: View {
    @Bindable var playback: PlaybackCoordinator
    let expand: () -> Void
    @State private var showQueue = false
    var body: some View {
        if let track = playback.track {
            HStack(spacing: 12) {
                Button(action: expand) {
                    HStack(spacing: 12) {
                        ArtworkView(image: playback.artwork).frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 9))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(track.title).font(.headline).lineLimit(1)
                            Text(track.artist).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                            Text(playback.contextName).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.buttonStyle(.plain).accessibilityLabel("Now playing, \(track.title), \(track.artist)").accessibilityIdentifier("expandPlayer")
                VStack(spacing: 8) {
                    HStack(spacing: 16) {
                        Button { Task { await playback.send("previous") } } label: { Image(systemName: "backward.end.fill").frame(width: 44, height: 44) }.accessibilityLabel("Previous song").disabled(playback.skipPending)
                        Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                            Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill").font(.title2).frame(width: 48, height: 48).background(.white.opacity(0.08), in: Circle()).padding(4).overlay { PlaybackProgressRing(progress: playback.progress) }
                        }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play")
                        Button { Task { await playback.send("next") } } label: { Image(systemName: "forward.end.fill").opacity(playback.skipPending ? 0 : 1).overlay { if playback.skipPending { ProgressView().tint(.green) } }.frame(width: 44, height: 44) }.accessibilityLabel(playback.skipPending ? "Skipping" : "Next song").disabled(playback.skipPending)
                    }
                }.frame(width: 180)
                SystemVolumeSlider().frame(width: 130, height: 44)
                if !playback.serverConnection.connected || playback.serverConnection.degraded { ServerConnectionBadge(connection: playback.serverConnection) }
                AirPlayPicker(onDismiss: { playback.outputSelectionChanged() }, onPresent: { playback.refreshOutput(trigger: "picker-presented") }).frame(width: 44, height: 44)
                Button { showQueue = true } label: { Image(systemName: "list.bullet").frame(width: 44, height: 44) }.accessibilityLabel("Up next")
            }.padding(16).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
                .padding(.horizontal, 16).padding(.vertical, 8).foregroundStyle(.white)
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("tabletPlayer")
                .sheet(isPresented: $showQueue) { UpNextView(playback: playback) }
        }
    }
}

struct CacheStatusView: View {
    @Bindable var cache: AudioCache
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("\(ByteCountFormatter.string(fromByteCount: Int64(cache.downloadedBytes), countStyle: .file)) cached").font(.headline)
            ForEach(Array(cache.items.enumerated()), id: \.element.id) { offset, item in
                HStack {
                    Text(offset == 0 ? "Current song" : "Next \(offset)")
                    Spacer()
                    Text(item.state == "ready" ? "Ready" : item.state == "downloading" ? "Downloading…" : item.state.capitalized).foregroundStyle(.secondary)
                    if item.state == "ready" { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
                }.font(.subheadline)
            }
            Text("Temporary read-ahead for the current song and next three, up to 512 MB total and 256 MB per song. Cleared on sign-out. Downloads may use cellular data.").font(.footnote).foregroundStyle(.secondary)
        }.accessibilityIdentifier("cacheStatus")
    }
}


struct PlaybackProgressRing: View {
    let progress: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        ZStack {
            Circle().stroke(.white.opacity(0.15), lineWidth: 2)
            Circle().trim(from: 0, to: min(1, max(0, progress)))
                .stroke(.green, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(reduceMotion ? nil : .linear(duration: 0.25), value: progress)
        }.allowsHitTesting(false).accessibilityHidden(true)
    }
}

struct ServerConnectionBadge: View {
    let connection: ServerConnection
    @State private var showDetails = false
    private var color: Color { !connection.connected ? .red : connection.degraded ? .yellow : .green }
    private var details: String {
        var lines = [connection.detail]
        if let address = connection.address { lines.append(address) }
        if let notice = connection.homeNotice { lines.append(notice) }
        return lines.joined(separator: "\n\n")
    }
    var body: some View {
        Button { showDetails = true } label: {
            Image(systemName: connection.home ? "wifi.router" : "globe")
                .font(.callout).foregroundStyle(color).frame(width: 44, height: 44)
        }.buttonStyle(.plain)
            .accessibilityLabel("Server connection: " + (!connection.connected ? "unreachable" : connection.degraded ? "slow or interrupted" : connection.home ? "local network" : "public"))
            .alert("Server connection", isPresented: $showDetails) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(details)
            }
    }
}

/// Measure controls at their final width within the layout pass. Never write a
/// measured size into view state: doing so can oscillate during keyboard/sheet
/// transitions and prevent the main run loop from servicing input or playback.
struct PortraitPlayerLayout: Layout {
    var availableHeight: CGFloat
    var maximumArtworkWidth: CGFloat
    var maximumControlsWidth: CGFloat
    private let spacing: CGFloat = 32

    private func dimensions(_ proposal: ProposedViewSize, _ subviews: Subviews) -> (width: CGFloat, controlsWidth: CGFloat, controlsHeight: CGFloat, artwork: CGFloat) {
        let width = max(0, proposal.width.flatMap { $0.isFinite ? $0 : nil } ?? maximumControlsWidth)
        let controlsWidth = min(width, maximumControlsWidth)
        let controlsHeight = subviews[1].sizeThatFits(ProposedViewSize(width: controlsWidth, height: nil)).height
        let artwork = max(0, min(width, maximumArtworkWidth, max(160, availableHeight - controlsHeight - spacing)))
        return (width, controlsWidth, controlsHeight, artwork)
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let d = dimensions(proposal, subviews)
        return CGSize(width: d.width, height: d.artwork + spacing + d.controlsHeight)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let d = dimensions(ProposedViewSize(width: bounds.width, height: nil), subviews)
        subviews[0].place(at: CGPoint(x: bounds.midX, y: bounds.minY), anchor: .top,
                          proposal: ProposedViewSize(width: d.artwork, height: d.artwork))
        subviews[1].place(at: CGPoint(x: bounds.midX, y: bounds.minY + d.artwork + spacing), anchor: .top,
                          proposal: ProposedViewSize(width: d.controlsWidth, height: d.controlsHeight))
    }
}
