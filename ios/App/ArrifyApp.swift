import SwiftUI
import ArrifyCore
import AVKit

@MainActor @Observable final class AppModel {
    let api: APIClient
    let playback: PlaybackCoordinator
    let library: LibraryStore
    let artwork: ArtworkStore
    var signedIn = false
    var restoring = true
    var endpoint = "https://arrify.robnets.com"
    var error: String?
    init() {
        #if DEBUG
        if ProcessInfo.processInfo.environment["ARRIFY_UI_TESTING"] == "1" {
            api = APIClient(transport: UITestTransport(), store: UITestStore())
        } else { api = APIClient(store: KeychainStore()) }
        #else
        api = APIClient(store: KeychainStore())
        #endif
        #if DEBUG
        if ProcessInfo.processInfo.environment["ARRIFY_UI_TESTING"] == "1" {
            playback = PlaybackCoordinator(api: api, audioSession: UITestAudioSession())
        } else { playback = PlaybackCoordinator(api: api) }
        #else
        playback = PlaybackCoordinator(api: api)
        #endif
        library = LibraryStore(api: api)
        artwork = ArtworkStore(api: api)
    }
    func restore() async {
        defer { restoring = false }
        do {
            if let saved = try await api.restore() { endpoint = saved; signedIn = true; playback.start() }
        } catch { self.error = error.localizedDescription }
    }
    func login(username: String, password: String) async {
        do {
            try await api.login(endpoint: endpoint, username: username, password: password, deviceName: UIDevice.current.model)
            signedIn = true; error = nil; playback.start()
        } catch { self.error = error.localizedDescription }
    }
    func logout() async { playback.stop(); library.clear(); artwork.clear(); signedIn = false; await api.logout() }
}

@main struct ArrifyApp: App {
    @State private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            Group {
                if model.restoring { ProgressView("Opening Arrify…") }
                else if model.signedIn { MainView(model: model) }
                else { SignInView(model: model) }
            }
            .tint(.green).preferredColorScheme(.dark)
            .task { await model.restore() }
        }
    }
}

struct SignInView: View {
    @Bindable var model: AppModel
    @State private var username = ""
    @State private var password = ""
    @State private var submitting = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Your music. Wherever you listen.").font(.title2.bold())
                    Text("Sign in with your Arrify account.").foregroundStyle(.secondary)
                }
                Section { Link("Privacy policy", destination: URL(string: "https://arrify.robnets.com/privacy")!).accessibilityIdentifier("privacyPolicy") }
                Section("Server") {
                    TextField("Server address", text: $model.endpoint)
                        .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never)
                        .autocorrectionDisabled().accessibilityIdentifier("serverAddress")
                    Text("Use Arrify Home only when connected to your home network or VPN.").font(.footnote).foregroundStyle(.secondary)
                }
                Section("Account") {
                    TextField("Username", text: $username).textContentType(.username)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("username")
                    SecureField("Password", text: $password).textContentType(.password).accessibilityIdentifier("password")
                }
                if let error = model.error { Section { Text(error).foregroundStyle(.orange) } }
                Section {
                    Button {
                        submitting = true
                        Task { await model.login(username: username, password: password); password = ""; submitting = false }
                    } label: {
                        HStack { Spacer(); if submitting { ProgressView() }; Text("Sign in").bold(); Spacer() }
                    }.disabled(submitting || username.isEmpty || password.isEmpty).accessibilityIdentifier("signIn")
                }
            }.navigationTitle("Arrify")
        }
    }
}

struct MainView: View {
    @Bindable var model: AppModel
    @State private var showPlayer = false
    var body: some View {
        Group {
            if UIDevice.current.userInterfaceIdiom == .pad {
                VStack(spacing: 0) { tabs; miniPlayer.background(.regularMaterial) }
            } else if #available(iOS 26.0, *) { tabs.tabViewBottomAccessory { miniPlayer } }
            else { tabs }
        }.sheet(isPresented: $showPlayer) { NowPlayingView(playback: model.playback) }
    }
    private var tabs: some View {
        TabView {
            Tab("Library", systemImage: "square.stack") { withPlayer(LibraryHome(model: model)) }
            Tab("Channels", systemImage: "antenna.radiowaves.left.and.right") { withPlayer(NavigationStack { CollectionScreen(model: model, section: "channels", title: "Channels") }) }
            Tab("Moods", systemImage: "sparkles") { withPlayer(MoodScreen(model: model)) }
            Tab("Search", systemImage: "magnifyingglass", role: .search) { withPlayer(SearchScreen(model: model)) }
            Tab("Settings", systemImage: "gearshape") { withPlayer(SettingsView(model: model)) }
        }
        .tabViewStyle(.sidebarAdaptable)
    }
    private func withPlayer<Content: View>(_ content: Content) -> some View {
        content.safeAreaInset(edge: .bottom, spacing: 0) {
            if #unavailable(iOS 26.0) {
                if UIDevice.current.userInterfaceIdiom != .pad { miniPlayer.background(.regularMaterial) }
            }
        }
            .safeAreaInset(edge: .top, spacing: 0) {
                if model.playback.hasOtherOutput {
                    HStack { Text("Playing on another device").font(.footnote); Spacer(); Button("Play here") { Task { await model.playback.send("takeover") } } }.padding().background(.thinMaterial)
                }
                if let message = model.playback.message { Text(message).font(.footnote).padding().frame(maxWidth: .infinity).background(.thinMaterial) }
            }
    }
    @ViewBuilder private var miniPlayer: some View {
            if let track = model.playback.track {
                HStack(spacing: 12) {
                    Button { showPlayer = true } label: {
                        HStack {
                            ArtworkView(image: model.playback.artwork).frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 8))
                            VStack(alignment: .leading) {
                                Text(track.title).font(.headline).lineLimit(1)
                                Text(track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel("Now playing, \(track.title), \(track.artist)")
                    Button { Task { await model.playback.send(model.playback.showsPauseControl ? "pause" : "resume") } } label: {
                        Image(systemName: model.playback.showsPauseControl ? "pause.fill" : "play.fill").frame(width: 44, height: 44)
                    }.accessibilityLabel(model.playback.showsPauseControl ? "Pause" : "Play")
                }.padding(10).accessibilityIdentifier("miniPlayer")
            }
    }
}

struct NowPlayingView: View {
    @Bindable var playback: PlaybackCoordinator
    @Environment(\.dismiss) private var dismiss
    @State private var seeking = false
    @State private var seekPosition = 0.0
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    ArtworkView(image: playback.artwork).frame(maxWidth: 360).aspectRatio(1, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 16))
                    VStack(spacing: 8) {
                        Text(playback.track?.title ?? "Nothing playing").font(.title.bold()).multilineTextAlignment(.center)
                        Text(playback.track?.artist ?? "").foregroundStyle(.secondary)
                    }
                    if playback.busy { ProgressView("Preparing audio…") }
                    if let message = playback.message {
                        Text(message).font(.callout)
                        Button("Retry") { playback.retry() }
                    }
                    if playback.hasOtherOutput { Button("Play here") { Task { await playback.send("takeover") } } }
                    Slider(value: Binding(get: { seeking ? seekPosition : min(playback.position, max(1, playback.duration)) }, set: { seekPosition = $0 }), in: 0...max(1, playback.duration)) { editing in
                        seeking = editing
                        if !editing { Task { await playback.send("seek", seek: seekPosition) } }
                    }.disabled(playback.duration <= 0 || !playback.ownsOutput).accessibilityLabel("Playback position")
                    HStack {
                        Text(time(playback.position)); Spacer(); Text(time(playback.duration))
                    }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    HStack(spacing: 36) {
                        Button { Task { await playback.send("previous") } } label: { Image(systemName: "backward.end.fill").frame(width: 44, height: 44) }.accessibilityLabel("Previous song")
                        Button { Task { await playback.send(playback.showsPauseControl ? "pause" : "resume") } } label: {
                            Image(systemName: playback.showsPauseControl ? "pause.fill" : "play.fill").font(.title).frame(width: 72, height: 72).foregroundStyle(.black).background(.green, in: Circle())
                        }.accessibilityLabel(playback.showsPauseControl ? "Pause" : "Play")
                        Button { Task { await playback.send("next") } } label: { Image(systemName: "forward.end.fill").frame(width: 44, height: 44) }.accessibilityLabel("Next song")
                    }
                    HStack(spacing: 36) {
                        Button { Task { await playback.send("shuffle") } } label: {
                            Image(systemName: "shuffle").frame(width: 44, height: 44)
                        }.accessibilityLabel("Shuffle upcoming songs").disabled(playback.state?.queueControlsAvailable != true)
                        Button { Task { await playback.cycleRepeat() } } label: {
                            Image(systemName: playback.state?.repeatMode == "one" ? "repeat.1" : "repeat")
                                .foregroundStyle(playback.state?.repeatMode == "off" ? Color.secondary : Color.green)
                                .frame(width: 44, height: 44)
                        }.accessibilityLabel("Repeat").accessibilityValue(playback.state?.repeatMode ?? "off")
                            .disabled(playback.state?.queueControlsAvailable != true)
                    }
                    if playback.state?.queueControlsAvailable == false {
                        Text("Radio chooses the next songs. Shuffle and repeat are available for albums and playlists.")
                            .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    AirPlayPicker().frame(width: 64, height: 44).accessibilityLabel("Audio output")
                }.padding(24).frame(maxWidth: 600).frame(maxWidth: .infinity)
            }.background(.black).navigationTitle("Now Playing").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Done", systemImage: "chevron.down") { dismiss() } } }
        }
    }
    private func time(_ seconds: Double) -> String { let n = Int(max(0, seconds)); return "\(n / 60):\(String(format: "%02d", n % 60))" }
}

struct AirPlayPicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView { let view = AVRoutePickerView(); view.tintColor = .white; view.activeTintColor = .systemGreen; return view }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
struct SettingsView: View {
    @Bindable var model: AppModel
    var body: some View {
        NavigationStack {
            Form {
                Section { Link("Privacy policy", destination: URL(string: "https://arrify.robnets.com/privacy")!).accessibilityIdentifier("privacyPolicy") }
                Section("Connection") { Text(model.endpoint).textSelection(.enabled) }
                Section("Audio") {
                    Picker("Quality", selection: Binding(get: { model.playback.profile }, set: { model.playback.profile = $0 })) {
                        Text("Original").tag("original"); Text("AAC").tag("aac"); Text("MP3").tag("mp3")
                    }
                    Text("Applies to the next prepared song. This prototype streams audio; offline downloads are not included yet.").font(.footnote).foregroundStyle(.secondary)
                }
                Section { Button("Sign out", role: .destructive) { Task { await model.logout() } } }
                Section { Text("Arrify • Listening preview 0.2.1").foregroundStyle(.secondary) }
            }.navigationTitle("Settings")
        }
    }
}

struct ArtworkView: View {
    let image: UIImage?
    var body: some View {
        Group {
            if let image { Image(uiImage: image).resizable().scaledToFit() }
            else { Rectangle().fill(.white.opacity(0.05)).overlay { Image(systemName: "music.note").font(.largeTitle).foregroundStyle(.green) } }
        }.accessibilityHidden(true)
    }
}
