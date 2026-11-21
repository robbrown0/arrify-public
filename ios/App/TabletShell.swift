import SwiftUI

/// iPad keeps navigation and search outside the scrolling content and keyboard layout.
struct TabletShell: View {
    let model: AppModel
    let expandPlayer: () -> Void
    @State private var destination = "Home"
    @State private var searchRoot = UUID()
    @State private var libraryRoot = UUID()
    @State private var songsRoot = UUID()
    @State private var query = ""
    @State private var searchKind = "all"
    @State private var creatingPlaylist = false
    @FocusState private var searching: Bool
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 10) {
                            if let icon = model.brandIcon { Image(uiImage: icon).resizable().scaledToFit().frame(width: 34, height: 34) }
                            else { Image("BrandLogo").resizable().scaledToFit().frame(width: 34, height: 34) }
                            Text("Arrify").font(.title2.bold())
                        }.padding(.vertical, 24)
                        navigation("Home", "house")
                        navigation("Browse", "square.grid.2x2")
                        navigation("Songs", "music.note")
                        navigation("Albums", "square.stack")
                        navigation("Artists", "music.mic")
                        HStack {
                            Text("YOUR LIBRARY").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            Spacer()
                            Button { creatingPlaylist = true } label: { Image(systemName: "plus").frame(width: 44, height: 44) }.accessibilityLabel("Create playlist")
                        }.padding(.top, 24)
                        navigation("Library", "books.vertical")
                        navigation("Liked songs", "checkmark.circle.fill")
                        navigation("Playlists", "music.note.list")
                        navigation("Moods", "circle.lefthalf.filled")
                        navigation("Channels", "antenna.radiowaves.left.and.right")
                        ForEach(model.library.overview?.items.filter { $0.section == "personal" } ?? [], id: \.identity) { item in
                            navigation(item.name, "music.note.list", key: item.identity)
                        }
                        navigation("Settings", "gearshape")
                    }.padding(.horizontal, 12)
                }.frame(width: 190).background(Color.white.opacity(0.025)).accessibilityIdentifier("tabletSidebar")
                VStack(spacing: 0) {
                    HStack(spacing: 12) {
                        Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                        TextField(searchKind == "all" ? "Search your music" : "Search \(searchKind == "tracks" ? "songs" : searchKind)", text: $query).focused($searching)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().submitLabel(.search)
                            .accessibilityIdentifier("tabletSearch")
                            .simultaneousGesture(TapGesture().onEnded { searchRoot = UUID(); destination = "Search" })
                            .onSubmit { searching = false; destination = "Search" }
                        if !query.isEmpty {
                            Button { query = "" } label: { Image(systemName: "xmark.circle.fill").frame(width: 44, height: 44) }.accessibilityLabel("Clear search")
                        }
                        Button { creatingPlaylist = true } label: { Label("Create", systemImage: "plus").frame(minHeight: 44) }.accessibilityLabel("Create playlist")
                    }.padding(.horizontal, 16).padding(.vertical, 8).background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12)).padding(16)
                    if ["Search", "Songs", "Albums", "Artists"].contains(destination) {
                        SearchScopePicker(kind: Binding(get: { searchKind }, set: { searchKind = $0; searchRoot = UUID(); destination = "Search" }))
                            .padding(.horizontal, 16).padding(.bottom, 8)
                    }
                    content.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                // Keep navigation/scroll content from intercepting taps in the sidebar.
                .contentShape(.interaction, Rectangle())
            }
            if model.playback.needsTakeover {
                PlaybackTakeoverBanner(playback: model.playback)
                    .padding(.horizontal, 16).padding(.top, 8)
            }
            if let message = model.playback.message {
                HStack {
                    Text(message).font(.footnote)
                        .accessibilityIdentifier("playbackStatusMessage")
                    Spacer()
                    Button("Retry") { model.playback.retry() }
                        .frame(minWidth: 44, minHeight: 44)
                        .accessibilityIdentifier("retryPlayback")
                }.padding(.horizontal, 16)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    .padding(.horizontal, 16).padding(.bottom, 8)
            }
            if model.playback.track != nil {
                TabletPlayerBar(playback: model.playback, expand: expandPlayer)
            } else {
                EmptyPlayerView { searching = false; libraryRoot = UUID(); destination = "Library" }
                    .padding(.horizontal, 16).padding(.vertical, 8)
            }
        }.background(Color.black)
            .onChange(of: query) { if searching { destination = "Search" } }
            .onChange(of: searching) { PlaybackDiagnostics.record("search-focused=\(searching)"); if searching { destination = "Search" } }
            .sheet(isPresented: $creatingPlaylist) { CreatePlaylistView(model: model) }
    }
    private func navigation(_ title: String, _ icon: String, key: String? = nil) -> some View {
        let value = key ?? title
        return Button {
            searching = false
            if value == "Library" { libraryRoot = UUID() }
            if value == "Songs" { songsRoot = UUID() }
            searchKind = ["Songs": "tracks", "Albums": "albums", "Artists": "artists"][value] ?? "all"
            query = ""
            destination = value
        } label: {
            Label(title, systemImage: icon).font(.subheadline.weight(.medium)).lineLimit(2)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.horizontal, 10)
                .contentShape(Rectangle())
                .foregroundStyle(destination == value ? Color.green : Color.secondary)
                .background(destination == value ? Color.green.opacity(0.1) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
        }.buttonStyle(.plain)
    }
    @ViewBuilder private var content: some View {
        switch destination {
        case "Home": HomeScreen(model: model)
        case "Browse": BrowseScreen(model: model)
        case "Library": LibraryHome(model: model).id(libraryRoot)
        case "Moods": MoodScreen(model: model)
        case "Settings": SettingsView(model: model)
        case "Search":
            NavigationStack {
                if searchKind == "all" { AllSearchResults(model: model, query: query) }
                else {
                    BrowserScreen(model: model, title: searchKind == "tracks" ? "Songs" : searchKind.capitalized, kind: searchKind, externalQuery: query).id(searchKind)
                }
            }.id(searchRoot)

        default:
            NavigationStack {
                switch destination {
                case "Liked songs": LikedSongsView(model: model)
                case "Songs": BrowserScreen(model: model, title: "Songs", kind: "tracks", externalQuery: query).id(songsRoot)
                case "Albums": BrowserScreen(model: model, title: "Albums", kind: "albums", externalQuery: query)
                case "Artists": BrowserScreen(model: model, title: "Artists", kind: "artists", externalQuery: query)
                case "Channels": CollectionScreen(model: model, section: "channels", title: "Channels")
                case "Playlists": PlaylistLibraryView(model: model)
                default:
                    if let item = model.library.overview?.items.first(where: { $0.identity == destination }) { DetailScreen(model: model, entry: item) }
                }
            }.id(destination)
        }
    }
}
