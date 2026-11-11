import SwiftUI

/// iPad keeps navigation and search outside the scrolling content and keyboard layout.
struct TabletShell: View {
    let model: AppModel
    let expandPlayer: () -> Void
    @State private var destination = "Home"
    @State private var query = ""
    @State private var searchKind = "tracks"
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
                        navigation("Songs", "music.note")
                        navigation("Albums", "square.stack")
                        navigation("Artists", "music.mic")
                        HStack {
                            Text("YOUR LIBRARY").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                            Spacer()
                            Button { creatingPlaylist = true } label: { Image(systemName: "plus").frame(width: 44, height: 44) }.accessibilityLabel("Create playlist")
                        }.padding(.top, 24)
                        navigation("Library", "books.vertical")
                        navigation("Liked songs", "heart")
                        navigation("Playlists", "music.note.list")
                        navigation("Moods", "sparkles")
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
                        TextField("Artist, album, or song", text: $query).focused($searching)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().submitLabel(.search)
                            .accessibilityIdentifier("tabletSearch")
                            .onSubmit { searching = false; destination = "Search" }
                        if !query.isEmpty {
                            Button { query = "" } label: { Image(systemName: "xmark.circle.fill").frame(width: 44, height: 44) }.accessibilityLabel("Clear search")
                        }
                        Button { creatingPlaylist = true } label: { Label("Create", systemImage: "plus").frame(minHeight: 44) }.accessibilityLabel("Create playlist")
                    }.padding(.horizontal, 16).padding(.vertical, 8).background(Color.white.opacity(0.06), in: RoundedRectangle(cornerRadius: 12)).padding(16)
                    if model.playback.hasOtherOutput {
                        HStack {
                            Text("Playing on another device").font(.footnote)
                            Spacer()
                            Button("Play here") { Task { await model.playback.send("takeover") } }
                                .accessibilityIdentifier("takeOverPlayback")
                        }.padding(.horizontal, 16).padding(.bottom, 12)
                    }
                    content.frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            TabletPlayerBar(playback: model.playback, expand: expandPlayer)
        }.background(Color.black)
            .onChange(of: query) { destination = "Search" }
            .onChange(of: searching) { PlaybackDiagnostics.record("search-focused=\(searching)"); if searching { destination = "Search" } }
            .sheet(isPresented: $creatingPlaylist) { CreatePlaylistView(model: model) }
    }
    private func navigation(_ title: String, _ icon: String, key: String? = nil) -> some View {
        let value = key ?? title
        return Button { searching = false; destination = value } label: {
            Label(title, systemImage: icon).font(.subheadline.weight(.medium)).lineLimit(2)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading).padding(.horizontal, 10)
                .foregroundStyle(destination == value ? Color.green : Color.secondary)
                .background(destination == value ? Color.green.opacity(0.1) : Color.clear, in: RoundedRectangle(cornerRadius: 8))
        }.buttonStyle(.plain)
    }
    @ViewBuilder private var content: some View {
        switch destination {
        case "Home": HomeScreen(model: model)
        case "Library": LibraryHome(model: model)
        case "Moods": MoodScreen(model: model)
        case "Settings": SettingsView(model: model)
        case "Search":
            NavigationStack {
                VStack(spacing: 0) {
                    Picker("Search category", selection: $searchKind) {
                        Text("Songs").tag("tracks"); Text("Albums").tag("albums"); Text("Artists").tag("artists")
                    }.pickerStyle(.segmented).padding(.horizontal, 16).padding(.bottom, 12)
                    BrowserScreen(model: model, title: searchKind == "tracks" ? "Songs" : searchKind.capitalized, kind: searchKind, externalQuery: query).id(searchKind)
                }
            }.id(query)
        default:
            NavigationStack {
                switch destination {
                case "Liked songs": LikedSongsView(model: model)
                case "Songs": BrowserScreen(model: model, title: "Songs", kind: "tracks")
                case "Albums": BrowserScreen(model: model, title: "Albums", kind: "albums")
                case "Artists": BrowserScreen(model: model, title: "Artists", kind: "artists")
                case "Channels": CollectionScreen(model: model, section: "channels", title: "Channels")
                case "Playlists": PlaylistLibraryView(model: model)
                default:
                    if let item = model.library.overview?.items.first(where: { $0.identity == destination }) { DetailScreen(model: model, entry: item) }
                }
            }.id(destination)
        }
    }
}
