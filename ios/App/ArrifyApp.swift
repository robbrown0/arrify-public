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
    var homeEndpoint = ""
    var error: String?
    var accountDeleted = false
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
        artwork = playback.artworkStore
        playback.onHistoryRecorded = { [weak library] in
            library?.scheduleRefresh()
        }
        RemoteControlRuntime.api = api
    }
    func restore() async {
        defer { restoring = false }
        do {
            if let saved = try await api.restore() { endpoint = saved; homeEndpoint = await api.connectionAddresses()?.homeAddress ?? ""; signedIn = true; await playback.remoteActivity.restoreForSession(); playback.start() }
        } catch { self.error = error.localizedDescription }
    }
    func login(username: String, password: String) async {
        do {
            try await api.login(endpoint: endpoint, homeEndpoint: homeEndpoint, username: username, password: password, deviceName: UIDevice.current.model)
            signedIn = true; error = nil; await playback.remoteActivity.restoreForSession(); playback.start()
        } catch { self.error = error.localizedDescription }
    }
    func refreshBranding() async {
        let data = try? await api.artwork(path: "/api/v1/branding/icon")
        guard signedIn else { return }
        brandIcon = data.flatMap { ArtworkDecoder.image($0, pixels: 192) }
    }
    func finishAccountDeletion() async {
        await playback.remoteActivity.stop()
        playback.stop()
        await artwork.clearPersistentCache()
        UserDefaults.standard.set(false, forKey: "playbackDiagnostics")
        PlaybackDiagnostics.configure(active: false)
        await PlaybackDiagnostics.writer.clear()
        await logout()
        accountDeleted = true
    }
    func logout() async { await playback.remoteActivity.stop(); brandIcon = nil; playback.stop(); library.clear(); favorites.clear(); artwork.clear(); signedIn = false; await api.logout() }
}

@main struct ArrifyApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            Group {
                if model.restoring { ProgressView("Opening Arrify…") }
                else if model.signedIn { MainView(model: model) }
                else { SignInView(model: model) }
            }
            .tint(.green).preferredColorScheme(.dark)
            .alert("Account deleted", isPresented: $model.accountDeleted) {
                Button("OK", role: .cancel) {}
            } message: { Text("Your account and personal data have been deleted. You are signed out on all devices.") }
            .task {
                PlaybackDiagnostics.event("launch build=" + (Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"))
                PlaybackDiagnostics.configure(active: scenePhase == .active)
                await model.restore()
            }
            .onChange(of: scenePhase) {
                PlaybackDiagnostics.event("scene=\(scenePhase)")
                PlaybackDiagnostics.configure(active: scenePhase == .active)
            }
            .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillChangeFrameNotification)) { _ in PlaybackDiagnostics.record("keyboard-frame-change") }
            .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardDidHideNotification)) { _ in PlaybackDiagnostics.record("keyboard-hidden") }
        }
    }
}

struct SignInView: View {
    @Bindable var model: AppModel
    @State private var username = ""
    @State private var password = ""
    @State private var submitting = false
    @State private var creatingAccount = false
    @State private var recoveringAccount = false
    @State private var deletingAccount = false
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
                    TextField("Public server address", text: $model.endpoint)
                        .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never)
                        .autocorrectionDisabled().accessibilityIdentifier("serverAddress")
                    DisclosureGroup("Local network address (optional)") {
                        TextField("https://local.example.com", text: $model.homeEndpoint)
                            .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        Text("Use HTTPS. Arrify verifies and prefers the local network address when reachable, then falls back to public.").font(.footnote).foregroundStyle(.secondary)
                    }
                }
                Section("Account") {
                    TextField("Username", text: $username).textContentType(.username)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("username")
                    AccountPasswordField(title: "Password", text: $password, identifier: "password", newPassword: false)
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
                Section {
                    Button("Forgot password?") { recoveringAccount = true }
                    Button("Create account") { creatingAccount = true }.accessibilityIdentifier("createAccount")
                    Button("Delete an existing account", role: .destructive) { deletingAccount = true }.accessibilityIdentifier("deleteExistingAccount")
                }
            }.navigationTitle("Arrify")
                .sheet(isPresented: $recoveringAccount) { AccountRecoveryView(model: model) }
                .sheet(isPresented: $creatingAccount) { CreateAccountView(model: model) }
                .sheet(isPresented: $deletingAccount) { NavigationStack { DeleteAccountView(model: model, signedIn: false) } }
        }
    }
}

struct MainView: View {
    @Bindable var model: AppModel
    @State private var showPlayer = false
    @State private var selectedTab = "Home"
    @State private var libraryRoot = UUID()
    var body: some View {
        ZStack {
            if UIDevice.current.userInterfaceIdiom == .pad {
                TabletShell(model: model) { showPlayer = true }
            } else { tabs }
        }.onChange(of: showPlayer) { PlaybackDiagnostics.record("player-presented=\(showPlayer)") }.onOpenURL { url in if url.scheme == "arrify", url.host == "player" { showPlayer = true } }
        .task { await model.refreshBranding(); await model.favorites.refresh() }.fullScreenCover(isPresented: $showPlayer) { NowPlayingView(model: model) }
    }
    private var tabs: some View {
        TabView(selection: Binding(get: { selectedTab }, set: { value in
            if value == "Library" { libraryRoot = UUID() }; selectedTab = value
        })) {
            Tab("Home", systemImage: "house", value: "Home") { withPlayer(HomeScreen(model: model)) }
            Tab("Library", systemImage: "square.stack", value: "Library") { withPlayer(LibraryHome(model: model).id(libraryRoot)) }
            Tab("Channels", systemImage: "antenna.radiowaves.left.and.right", value: "Channels") { withPlayer(NavigationStack { CollectionScreen(model: model, section: "channels", title: "Channels") }) }
            Tab("Search", systemImage: "magnifyingglass", value: "Search", role: .search) { withPlayer(SearchScreen(model: model)) }
            Tab("Settings", systemImage: "gearshape", value: "Settings") { withPlayer(SettingsView(model: model)) }
        }
        .tabViewStyle(.sidebarAdaptable)
    }
    private func withPlayer<Content: View>(_ content: Content) -> some View {
        // Reserve the footer's actual height outside navigation/scroll content.
        // A fixed scroll margin cannot cover banners, wrapping text, and errors.
        VStack(spacing: 0) {
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
            VStack(spacing: 8) {
                // Keep status beside playback controls and out of search fields.
                if model.playback.hasOtherOutput {
                    PlaybackTakeoverBanner(playback: model.playback)
                }
                if model.playback.connectionInterrupted {
                    Label("Reconnecting… Playback continues where possible.", systemImage: "wifi.exclamationmark")
                        .font(.footnote).padding().frame(maxWidth: .infinity).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                }
                if let message = model.playback.message {
                    Text(message).font(.footnote).padding().frame(maxWidth: .infinity)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                        .accessibilityIdentifier("playbackStatusMessage")
                }
                if UIDevice.current.userInterfaceIdiom != .pad, model.playback.track != nil {
                    miniPlayer.background(.regularMaterial, in: Capsule())
                }
            }.padding(.horizontal, 16).padding(.bottom, 8)
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
                Section("Connection") { NavigationLink("Server addresses") { ServerAddressesView(model: model) } }
                Section("Audio") {
                    Picker("Quality", selection: Binding(get: { model.playback.profile }, set: { model.playback.profile = $0 })) {
                        Text("Original").tag("original"); Text("AAC").tag("aac"); Text("MP3").tag("mp3")
                    }
                    Text("Applies to the next song.").font(.footnote).foregroundStyle(.secondary)
                    NavigationLink("Playback diagnostics") { PlaybackDiagnosticsView() }
                    NavigationLink("Audio output diagnostics") { AudioRouteDiagnosticsView(playback: model.playback) }
                }
                Section("Lock Screen") {
                    Toggle("Automatic Lock Screen controls", isOn: Binding(get: { model.playback.remoteActivity.automatic }, set: { model.playback.remoteActivity.automatic = $0 }))
                    Text("Follow playback from another device on the same account while connected to Wi-Fi on the same local network. Open Arrify once to connect; iOS manages Lock Screen availability.").font(.footnote)
                    Text(model.playback.remoteActivity.status).font(.footnote).foregroundStyle(.secondary)
                    if model.playback.remoteActivity.active {
                        Button("Stop Lock Screen controls") { model.playback.remoteActivity.automatic = false }
                    }
                    if let error = model.playback.remoteActivity.error { Text(error).font(.footnote).foregroundStyle(.orange) }
                }
                Section("Artwork cache") {
                    Text("Recently viewed covers are kept on this device, up to 100 MB. They can be reused after signing back into the same account.").font(.footnote)
                    Button("Clear artwork cache") { Task { await model.artwork.clearPersistentCache() } }
                }
                Section("Device cache") { CacheStatusView(cache: model.playback.cache) }
                Section("Account") {
                    NavigationLink { DeleteAccountView(model: model, signedIn: true) } label: { Text("Delete account").foregroundStyle(.red) }.accessibilityIdentifier("deleteAccountSettings")
                    Button("Sign out", role: .destructive) { Task { await model.logout() } }
                }
                Section { Text("Arrify • \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "Unknown") (build \(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "Unknown"))").foregroundStyle(.secondary).accessibilityIdentifier("settingsBuildVersion") }
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

struct ServerAddressesView: View {
    @Bindable var model: AppModel
    @State private var publicAddress = ""
    @State private var homeAddress = ""
    @State private var saving = false
    @State private var result: String?
    var body: some View {
        Form {
            Section("Public server") {
                TextField("https://music.example.com", text: $publicAddress)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            Section("Local network server · optional") {
                TextField("https://local.example.com", text: $homeAddress)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                Text("Arrify uses this address only after verifying it is the same server. Both addresses need a valid HTTPS certificate.").font(.footnote)
            }
            Section {
                HStack { ServerConnectionBadge(connection: model.playback.serverConnection); Text(model.playback.serverConnection.detail).font(.footnote) }
                if let notice = model.playback.serverConnection.homeNotice { Text(notice).font(.footnote) }
                if let result { Text(result).font(.footnote) }
                Button(saving ? "Verifying…" : "Save addresses") {
                    saving = true; result = nil
                    Task {
                        do {
                            try await model.api.updateAddresses(publicAddress: publicAddress, homeAddress: homeAddress)
                            model.endpoint = publicAddress; model.homeEndpoint = homeAddress
                            await model.playback.reconcile(); result = "Addresses saved."
                        } catch { result = error.localizedDescription }
                        saving = false
                    }
                }.disabled(saving)
            }
        }.navigationTitle("Server addresses")
            .onAppear { publicAddress = model.endpoint; homeAddress = model.homeEndpoint }
    }
}
