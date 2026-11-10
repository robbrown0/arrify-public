import SwiftUI
import ArrifyCore
import AVKit

@MainActor @Observable final class AppModel {
    let api: APIClient
    let playback: PlaybackCoordinator
    let library: LibraryStore
    let favorites: FavoritesStore
    let artwork: ArtworkStore
    var brandIcon: UIImage?
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
        favorites = FavoritesStore(api: api)
        library = LibraryStore(api: api)
        artwork = ArtworkStore(api: api)
    }
    func restore() async {
        defer { restoring = false }
        do {
            if let saved = try await api.restore() { endpoint = saved; signedIn = true; await playback.remoteActivity.clearRestoredActivities(); playback.start() }
        } catch { self.error = error.localizedDescription }
    }
    func login(username: String, password: String) async {
        do {
            try await api.login(endpoint: endpoint, username: username, password: password, deviceName: UIDevice.current.model)
            signedIn = true; error = nil; await playback.remoteActivity.clearRestoredActivities(); playback.start()
        } catch { self.error = error.localizedDescription }
    }
    func refreshBranding() async {
        let data = try? await api.artwork(path: "/api/v1/branding/icon")
        guard signedIn else { return }
        brandIcon = data.flatMap { ArtworkDecoder.image($0, pixels: 192) }
    }
    func logout() async { await playback.remoteActivity.stop(); brandIcon = nil; playback.stop(); library.clear(); favorites.clear(); artwork.clear(); signedIn = false; await api.logout() }
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
                    Image("BrandLogo").resizable().scaledToFit().frame(height: 96).frame(maxWidth: .infinity).accessibilityHidden(true)
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
                VStack(spacing: 0) {
                    tabs
                    ViewThatFits(in: .horizontal) {
                        TabletPlayerBar(playback: model.playback) { showPlayer = true }.frame(minWidth: 760)
                        miniPlayer.background(.regularMaterial)
                    }
                }
            } else if #available(iOS 26.1, *) { tabs.tabViewBottomAccessory(isEnabled: model.playback.track != nil) { miniPlayer } }
            else if #available(iOS 26.0, *) { tabs.tabViewBottomAccessory { miniPlayer } }
            else { tabs }
        }.onOpenURL { url in if url.scheme == "arrify", url.host == "player" { showPlayer = true } }
        .task { await model.refreshBranding(); await model.favorites.refresh() }.fullScreenCover(isPresented: $showPlayer) { NowPlayingView(playback: model.playback, favorites: model.favorites) }
    }
    private var tabs: some View {
        TabView {
            Tab("Home", systemImage: "house") { withPlayer(HomeScreen(model: model)) }
            Tab("Library", systemImage: "square.stack") { withPlayer(LibraryHome(model: model)) }
            Tab("Channels", systemImage: "antenna.radiowaves.left.and.right") { withPlayer(NavigationStack { CollectionScreen(model: model, section: "channels", title: "Channels") }) }
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
                if model.playback.connectionInterrupted {
                    Label("Reconnecting… Playback continues where possible.", systemImage: "wifi.exclamationmark")
                        .font(.footnote).padding().frame(maxWidth: .infinity).background(.thinMaterial)
                }
                if let message = model.playback.message { Text(message).font(.footnote).padding().frame(maxWidth: .infinity).background(.thinMaterial) }
            }
    }
    private var miniPlayer: some View {
        MiniPlayerView(playback: model.playback) { showPlayer = true }
    }
}

struct SettingsView: View {
    @Bindable var model: AppModel
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 12) {
                        if let image = model.brandIcon { Image(uiImage: image).resizable().scaledToFit().frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 10)) }
                        Text("Arrify").font(.title2.bold())
                    }
                }
                Section { Link("Privacy policy", destination: URL(string: "https://arrify.robnets.com/privacy")!).accessibilityIdentifier("privacyPolicy") }
                Section("Connection") { Text(model.endpoint).textSelection(.enabled) }
                Section("Audio") {
                    Picker("Quality", selection: Binding(get: { model.playback.profile }, set: { model.playback.profile = $0 })) {
                        Text("Original").tag("original"); Text("AAC").tag("aac"); Text("MP3").tag("mp3")
                    }
                    Text("Applies to the next song.").font(.footnote).foregroundStyle(.secondary)
                    NavigationLink("Audio output diagnostics") { AudioRouteDiagnosticsView(playback: model.playback) }
                }
                Section("Lock Screen") {
                    Text("Follow playback from another device on this account using a read-only Live Activity. Start it from that device’s player in Arrify.").font(.footnote)
                    if model.playback.remoteActivity.active {
                        Button("Stop Lock Screen status") { Task { await model.playback.remoteActivity.stop() } }
                    }
                    if let error = model.playback.remoteActivity.error { Text(error).font(.footnote).foregroundStyle(.orange) }
                }
                Section("Device cache") { CacheStatusView(cache: model.playback.cache) }
                Section { Button("Sign out", role: .destructive) { Task { await model.logout() } } }
                Section { Text("Arrify • 0.3.0").foregroundStyle(.secondary) }
            }.navigationTitle("Settings").task { await model.refreshBranding() }
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
