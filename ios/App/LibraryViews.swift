import SwiftUI
import ArrifyCore

struct RemoteCover: View {
    let path: String?
    let title: String
    let artwork: ArtworkStore
    var channel = false
    @State private var image: UIImage?
    @State private var loadedPath: String?
    var body: some View {
        ZStack {
            Color.black
            if let image = path.flatMap({ artwork.cachedImage($0) }) ?? (loadedPath == path ? image : nil) {
                if channel {
                    GeometryReader { geometry in
                        Image(uiImage: image).resizable().scaledToFit()
                            .grayscale(1).colorInvert().contrast(1.5)
                            .padding(geometry.size.width * 0.14)
                    }
                } else { Image(uiImage: image).resizable().scaledToFit() }
            }
            else {
                VStack(spacing: 12) {
                    Image(systemName: "music.note").font(.title).foregroundStyle(.green)
                    Text(title).font(.headline).multilineTextAlignment(.center).lineLimit(3).padding(.horizontal, 12)
                }
            }
        }.aspectRatio(1, contentMode: .fit)
        .task(id: path) {
            guard let path else { image = nil; loadedPath = nil; return }
            if let cached = artwork.cachedImage(path) { image = cached; loadedPath = path; return }
            let result = await artwork.image(path)
            if !Task.isCancelled { image = result; loadedPath = path }
        }.accessibilityHidden(true)
    }
}

struct RefreshStatus: View {
    let loading: Bool
    let updated: Date?
    let error: String?
    var body: some View {
        if let error { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.orange).font(.footnote) }
        else if loading { ProgressView("Updating…").font(.footnote) }
    }
}

struct LibraryHome: View {
    let model: AppModel
    var body: some View {
        NavigationStack {
            List {
                Section("Your library") {
                    NavigationLink { BrowseScreen(model: model) } label: { Label("Browse", systemImage: "square.grid.2x2") }
                    NavigationLink { LikedSongsView(model: model) } label: { Label("Liked songs", systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
                    NavigationLink { BrowserScreen(model: model, title: "Songs", kind: "tracks") } label: { Label("Songs", systemImage: "music.note") }
                    NavigationLink { BrowserScreen(model: model, title: "Albums", kind: "albums") } label: { Label("Albums", systemImage: "square.stack") }
                    NavigationLink { BrowserScreen(model: model, title: "Artists", kind: "artists") } label: { Label("Artists", systemImage: "music.mic") }
                    NavigationLink { PlaylistLibraryView(model: model) } label: { Label("Playlists", systemImage: "music.note.list") }
                    NavigationLink { MoodScreen(model: model) } label: { Label("Moods", systemImage: "circle.lefthalf.filled") }
                }.font(.title3).listRowBackground(Color.white.opacity(0.04))
                if let overview = model.library.overview {
                    let featured = Array(overview.items.filter { $0.section == "playlists" }.prefix(8))
                    if !featured.isEmpty {
                        Section("Made for listening") {
                            ScrollView(.horizontal, showsIndicators: false) {
                                LazyHStack(alignment: .top, spacing: 18) {
                                    ForEach(featured, id: \.identity) { item in
                                        NavigationLink { DetailScreen(model: model, entry: item) } label: {
                                            VStack(alignment: .leading, spacing: 8) {
                                                RemoteCover(path: item.artwork, title: item.name, artwork: model.artwork)
                                                    .frame(width: 160, height: 160).clipShape(RoundedRectangle(cornerRadius: 12))
                                                Text(item.name).font(.headline).lineLimit(2).multilineTextAlignment(.leading)
                                                Text("\(item.trackCount) songs").font(.caption).foregroundStyle(.secondary)
                                            }.frame(width: 160, alignment: .leading)
                                        }.buttonStyle(.plain)
                                    }
                                }.padding(.vertical, 8)
                            }
                        }.listRowBackground(Color.clear)
                    }
                    let personal = overview.items.filter { $0.section == "personal" }
                    if !personal.isEmpty {
                        Section("Your playlists") {
                            ForEach(personal, id: \.identity) { item in
                                NavigationLink { DetailScreen(model: model, entry: item) } label: { EntryRow(item: item, artwork: model.artwork) }
                            }
                        }
                    }
                }
                Section { RefreshStatus(loading: model.library.loading, updated: model.library.updated, error: model.library.error) }
            }
            .scrollContentBackground(.hidden).background(Color.black)
            .navigationTitle("Library")
            .toolbar { ToolbarItem(placement: .topBarLeading) {
                if let image = model.brandIcon { Image(uiImage: image).resizable().scaledToFit().frame(width: 32, height: 32).clipShape(RoundedRectangle(cornerRadius: 7)).accessibilityLabel("Server icon") }
            } }
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.library.refresh() } }.accessibilityIdentifier("refreshLibrary") } }
            .task { if model.library.overview == nil { await model.library.refresh() } }
            .refreshable { await model.library.refresh() }
        }
    }
}

struct EntryRow: View {
    let item: LibraryEntry
    let artwork: ArtworkStore
    var body: some View {
        HStack(spacing: 12) {
            RemoteCover(path: item.artwork, title: item.name, artwork: artwork).frame(width: 56, height: 56).clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name).foregroundStyle(.primary)
                if !item.subtitle.isEmpty { Text(item.subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                Text("\(item.trackCount) songs").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 4)
    }
}

struct CollectionScreen: View {
    let model: AppModel
    let section: String
    let title: String
    var mood: MoodGroup? = nil
    @Environment(\.dynamicTypeSize) private var typeSize
    var items: [LibraryEntry] {
        let all = model.library.overview?.items ?? []
        if let mood { return mood.slugs.compactMap { slug in all.first { $0.id == slug && $0.kind == "collections" } } }
        return all.filter { section == "channels" ? $0.kind == "channels" : $0.kind != "channels" && $0.section != "moods" }
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let mood { MoodCover(mood: mood).frame(height: 150).clipShape(RoundedRectangle(cornerRadius: 16)); Text(mood.description).foregroundStyle(.secondary) }
                RefreshStatus(loading: model.library.loading, updated: model.library.updated, error: model.library.error)
                if items.isEmpty && !model.library.loading {
                    ContentUnavailableView("Nothing here yet", systemImage: "music.note.list", description: Text("Your available \(title.lowercased()) appear here after the library updates."))
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: typeSize.isAccessibilitySize ? 260 : 150), spacing: 18)], spacing: 24) {
                    ForEach(items, id: \.identity) { item in CollectionCard(model: model, item: item) }
                }
            }.padding()
        }.background(Color.black).navigationTitle(title)
        .task { if model.library.overview == nil { await model.library.refresh() } }
        .refreshable { await model.library.refresh() }
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.library.refresh() } } } }
    }
}

struct CollectionCard: View {
    let model: AppModel
    let item: LibraryEntry
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack(alignment: .bottomTrailing) {
                NavigationLink { DetailScreen(model: model, entry: item) } label: {
                    RemoteCover(path: item.artwork, title: item.name, artwork: model.artwork, channel: item.kind == "channels").clipShape(RoundedRectangle(cornerRadius: 12))
                }.buttonStyle(.plain).accessibilityLabel("Details for \(item.name)")
                Button { Task { await model.playback.play(source: item.source) } } label: {
                    Image(systemName: "play.fill").font(.system(size: 13, weight: .semibold)).foregroundStyle(.black)
                        .frame(width: 34, height: 34).background(.green, in: Circle()).frame(width: 44, height: 44)
                }.padding(6).disabled(item.trackCount == 0).accessibilityLabel("Play \(item.name)")
            }
            .overlay {
                if item.kind == "channels" {
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(.white.opacity(0.16), lineWidth: 1)
                        .allowsHitTesting(false)
                }
            }
            if item.kind != "channels" {
                Text(item.name).font(.headline).lineLimit(2)
                if item.kind == "albums" { Text(item.subtitle).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                else { Text("\(item.trackCount) songs").font(.caption).foregroundStyle(.secondary) }
            } else if item.trackCount == 0 { Text("Preparing songs…").font(.caption).foregroundStyle(.secondary) }
        }
    }
}

struct MoodScreen: View {
    let model: AppModel
    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 20)], spacing: 20) {
                    ForEach(model.library.overview?.moods ?? []) { mood in
                        NavigationLink { CollectionScreen(model: model, section: "moods", title: mood.name, mood: mood) } label: {
                            VStack(alignment: .leading, spacing: 10) {
                                MoodCover(mood: mood).frame(height: 160).clipShape(RoundedRectangle(cornerRadius: 14))
                                Text(mood.name).font(.title2.bold()).foregroundStyle(.primary)
                                Text(mood.description).font(.subheadline).foregroundStyle(.secondary)
                                Text("\(mood.slugs.count) playlists").font(.caption).foregroundStyle(.secondary)
                            }
                        }.buttonStyle(.plain).accessibilityIdentifier("mood-" + mood.id)
                    }
                }.padding()
                RefreshStatus(loading: model.library.loading, updated: model.library.updated, error: model.library.error).padding()
                if model.library.overview?.moods.isEmpty == true { ContentUnavailableView("Moods are on their way", systemImage: "sparkles", description: Text("Playable mood playlists appear here as your library grows.")) }
            }.background(Color.black).navigationTitle("Moods")
            .task { if model.library.overview == nil { await model.library.refresh() } }
            .refreshable { await model.library.refresh() }
        }
    }
}

struct MoodCover: View {
    let mood: MoodGroup
    private var symbol: String {
        ["feel-good": "sun.max.fill", "unwind": "water.waves", "focus": "circle.hexagongrid.fill", "sleep": "moon.stars.fill", "romance": "heart.fill", "reflection": "sun.horizon.fill", "energy": "waveform", "on-the-road": "road.lanes"][mood.id] ?? "sparkles"
    }
    private var tint: Color {
        let n = UInt(mood.color.dropFirst(), radix: 16) ?? 0x243b46
        return Color(red: Double((n >> 16) & 255) / 255, green: Double((n >> 8) & 255) / 255, blue: Double(n & 255) / 255)
    }
    var body: some View {
        ZStack {
            LinearGradient(colors: [tint.opacity(0.6), tint, .black], startPoint: .topLeading, endPoint: .bottomTrailing)
            Circle().fill(.white.opacity(0.05)).frame(width: 240).offset(x: 80, y: 60)
            Image(systemName: symbol).font(.system(size: 66, weight: .light)).foregroundStyle(.white.opacity(0.85))
        }.clipped().accessibilityHidden(true)
    }
}

struct BrowserScreen: View {
    let model: AppModel
    let title: String
    @State private var browser: LibraryBrowser
    @State private var query = ""
    let externalQuery: String?
    private var activeQuery: String { externalQuery ?? query }
    init(model: AppModel, title: String, kind: String, externalQuery: String? = nil) {
        self.model = model; self.title = title; self.externalQuery = externalQuery
        _browser = State(initialValue: LibraryBrowser(api: model.api, kind: kind))
    }
    var body: some View {
        Group {
            if externalQuery != nil || UIDevice.current.userInterfaceIdiom == .pad { results }
            else { results.searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search \(title.lowercased())") }
        }
    }
    private var results: some View {
        List {
            Section { RefreshStatus(loading: browser.loading, updated: browser.updated, error: browser.error) }
            if browser.kind == "tracks" {
                ForEach(browser.tracks) { track in SongRow(track: track, model: model) { Task { await model.playback.play(track, in: browser.tracks) } } }
            } else {
                ForEach(browser.entries, id: \.identity) { item in
                    NavigationLink { DetailScreen(model: model, entry: item) } label: { EntryRow(item: item, artwork: model.artwork) }
                }
            }
            if !browser.loading && browser.tracks.isEmpty && browser.entries.isEmpty && browser.error == nil {
                ContentUnavailableView(activeQuery.isEmpty ? "No \(title.lowercased()) yet" : "No matching \(title.lowercased())", systemImage: "magnifyingglass", description: Text(activeQuery.isEmpty ? "Music added to your server will appear here." : "Try another name."))
            }
            if browser.nextOffset != nil { Button("Load more \(title.lowercased())") { Task { await browser.load(query: activeQuery, more: true) } }.disabled(browser.loading) }
            if browser.error != nil { Button("Retry") { Task { await browser.load(query: activeQuery) } } }
        }.scrollContentBackground(.hidden).background(Color.black)
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .task(id: activeQuery) {
            browser.resetSearch()
            do { try await Task.sleep(for: .milliseconds(activeQuery.isEmpty ? 0 : 300)) } catch { return }
            await browser.load(query: activeQuery)
            await browser.refreshPopularity(query: activeQuery)
        }
        .refreshable { await browser.load(query: activeQuery) }
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Refresh", systemImage: "arrow.clockwise") { Task { await browser.load(query: activeQuery) } }.accessibilityIdentifier("refreshSongs") } }
    }
}

struct SongRow: View {
    let track: Track
    let model: AppModel
    private var artwork: ArtworkStore { model.artwork }
    private var favorites: FavoritesStore { model.favorites }
    @State private var showPopularity = false
    let play: () -> Void
    var body: some View {
        HStack(spacing: 0) {
        Button(action: play) {
            HStack(spacing: 12) {
                RemoteCover(path: track.artwork ?? "/api/v1/artwork/\(track.id)", title: "", artwork: artwork).frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 6))
                VStack(alignment: .leading, spacing: 4) {
                    Text(track.title).foregroundStyle(.primary)
                    Text(track.artist).font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)

            }.frame(minHeight: 48).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel("Play \(track.title) by \(track.artist)")
        if let popularity = track.popularity, popularity.doubloons > 0 {
            Button { showPopularity = true } label: {
                Text(String(repeating: "🟡", count: min(3, max(0, popularity.doubloons))) + (popularity.rising == true ? " ↗" : ""))
                    .font(.caption).frame(minHeight: 44)
            }.buttonStyle(.plain).accessibilityLabel("Popularity: \(popularity.doubloons) of 3. View sources")
        }
        FavoriteButton(favorites: favorites, track: track)
        SongActionsMenu(track: track, model: model)
        }
        .sheet(isPresented: $showPopularity) {
            NavigationStack {
                List {
                    Text("1–3 coins reflect streaming reach, charts and airplay. Blank means below threshold or insufficient evidence.")
                    ForEach(Array((track.popularity?.evidence ?? []).enumerated()), id: \.offset) { _, evidence in
                        if let value = evidence.url, let url = URL(string: value), ["https", "http"].contains(url.scheme ?? "") {
                            Link(evidence.source ?? "Source", destination: url)
                        } else { Text(evidence.source ?? "Popularity evidence") }
                    }
                }.navigationTitle("Popularity").toolbar { Button("Done") { showPopularity = false } }
            }
        }
    }
}

struct SongActionsMenu: View {
    let track: Track
    let model: AppModel
    private var favorites: FavoritesStore { model.favorites }
    @State private var addingToPlaylist = false
    @State private var showQueue = false
    @State private var destination: LibraryEntry?
    @State private var showOriginalAlbum = false
    var body: some View {
        Menu {
            Button(favorites.ids.contains(track.id) ? "Remove from Liked songs" : "Add to Liked songs", systemImage: "heart") { Task { await favorites.toggle(track.id) } }
                .disabled(favorites.pending.contains(track.id))
            Button("Add to playlist", systemImage: "music.note.list") { addingToPlaylist = true }
            Button("Add to queue", systemImage: "text.badge.plus") { Task { await model.playback.send("queue", ids: [track.id]) } }
            Button("Go to song radio", systemImage: "dot.radiowaves.left.and.right") {
                destination = LibraryEntry(id: String(track.id), kind: "radio-track", name: track.title + " Radio")
            }
            if let id = track.artistId {
                Button("Go to artist radio", systemImage: "dot.radiowaves.left.and.right") { destination = LibraryEntry(id: id, kind: "radio-artist", name: track.artist + " Radio") }
                Button("Go to artist", systemImage: "person") { destination = LibraryEntry(id: id, kind: "artists", name: track.artist) }
            }
            Button("Go to queue", systemImage: "list.bullet") { showQueue = true }
            if let id = track.albumId {
                Button("Go to album", systemImage: "square.stack") { destination = LibraryEntry(id: id, kind: "albums", name: track.album) }
            }
            Button("Browse original album", systemImage: "square.stack") { showOriginalAlbum = true }
        } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44).foregroundStyle(.secondary) }
            .accessibilityLabel("More options for \(track.title)")
        .sheet(isPresented: $addingToPlaylist) { AddToPlaylistView(model: model, track: track) }
        .sheet(isPresented: $showQueue) { UpNextView(playback: model.playback) }
        .sheet(item: $destination) { entry in
            NavigationStack { DetailScreen(model: model, entry: entry).toolbar { Button("Done") { destination = nil } } }
        }
        .sheet(isPresented: $showOriginalAlbum) { OriginalAlbumView(model: model, track: track) }
    }
}

struct DetailScreen: View {
    let model: AppModel
    let entry: LibraryEntry
    @State private var browser: LibraryBrowser
    init(model: AppModel, entry: LibraryEntry) {
        self.model = model; self.entry = entry
        _browser = State(initialValue: LibraryBrowser(api: model.api, source: entry.source))
    }
    var body: some View {
        let item = browser.item ?? entry
        List {
            Section {
                VStack(spacing: 16) {
                    RemoteCover(path: item.artwork ?? entry.artwork, title: item.name, artwork: model.artwork, channel: item.kind == "channels").frame(maxWidth: 260).clipShape(RoundedRectangle(cornerRadius: 16))
                    Text(item.name).font(.title.bold()).multilineTextAlignment(.center)
                    if !item.subtitle.isEmpty { Text(item.subtitle).foregroundStyle(.secondary).multilineTextAlignment(.center) }
                    Text("\(item.trackCount) songs").font(.subheadline).foregroundStyle(.secondary)
                    HStack(spacing: 16) {
                        Button { Task { await model.playback.play(source: item.source) } } label: { Label("Play", systemImage: "play.fill").frame(maxWidth: .infinity, minHeight: 36) }.buttonStyle(.borderedProminent).tint(.green).foregroundStyle(.black).accessibilityIdentifier("playCollection")
                        if item.kind != "channels" {
                            Button { Task { await model.playback.play(source: item.source, shuffle: true) } } label: { Label("Shuffle", systemImage: "shuffle").frame(maxWidth: .infinity, minHeight: 36) }.buttonStyle(.bordered).accessibilityIdentifier("shuffleCollection")
                        }
                    }.disabled(item.trackCount == 0)
                }.frame(maxWidth: .infinity).padding(.vertical)
            }.listRowBackground(Color.clear)
            Section {
                RefreshStatus(loading: browser.loading, updated: browser.updated, error: browser.error)
                ForEach(Array(browser.tracks.enumerated()), id: \.offset) { _, track in
                    SongRow(track: track, model: model) { Task { await model.playback.play(source: item.source, trackId: track.id) } }
                }
                if browser.nextOffset != nil { Button("Load more songs") { Task { await browser.load(more: true) } }.disabled(browser.loading) }
                if browser.error != nil { Button("Retry") { Task { await browser.load() } } }
                if !browser.loading && browser.total == 0 && browser.error == nil { Text("No local songs yet.").foregroundStyle(.secondary) }
            }
        }.scrollContentBackground(.hidden).background(Color.black)
        .navigationTitle(entry.kind == "channels" ? "Channel" : "Details").navigationBarTitleDisplayMode(.inline)
        .task { await browser.load(); await browser.refreshPopularity() }.refreshable { await browser.load() }
    }
}

struct SearchScreen: View {
    let model: AppModel
    @State private var kind = "tracks"
    var body: some View {
        NavigationStack {
            BrowserScreen(model: model, title: kind == "tracks" ? "Songs" : kind.capitalized, kind: kind).id(kind)
                .safeAreaInset(edge: .top) {
                    Picker("Search category", selection: $kind) {
                        Text("Songs").tag("tracks"); Text("Albums").tag("albums"); Text("Artists").tag("artists")
                    }.pickerStyle(.segmented).padding().background(.bar)
                }
        }
    }
}

/// Home shows account-scoped listening history and playlists.
struct HomeScreen: View {
    let model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.horizontalSizeClass) private var sizeClass
    private var cardWidth: CGFloat { sizeClass == .regular ? 210 : 165 }
    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 32) {
                    Text("On the record.").font(.largeTitle.bold())
                    if UIDevice.current.userInterfaceIdiom != .pad {
                        HStack(spacing: 20) {
                            NavigationLink("Songs") { BrowserScreen(model: model, title: "Songs", kind: "tracks") }
                            NavigationLink("Albums") { BrowserScreen(model: model, title: "Albums", kind: "albums") }
                            NavigationLink("Artists") { BrowserScreen(model: model, title: "Artists", kind: "artists") }
                        }.frame(minHeight: 44)
                    }
                    if let overview = model.library.overview {
                        recentSongs(overview.recentTracks ?? [])
                        rail("Playlists", items: overview.items.filter { $0.section == "personal" } + overview.items.filter { $0.section == "playlists" }, albums: false)
                    }
                    RefreshStatus(loading: model.library.loading, updated: model.library.updated, error: model.library.error)
                }.padding(24)
            }.background(Color.black).navigationTitle("Home").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) {
                    Button("Refresh", systemImage: "arrow.clockwise") { Task { await model.library.refresh() } }.accessibilityIdentifier("refreshLibrary")
                } }
                .task { await model.library.refresh() }
                .onChange(of: scenePhase) { if scenePhase == .active { model.library.scheduleRefresh() } }
                .refreshable { await model.library.refresh() }
        }
    }
    private func recentSongs(_ tracks: [Track]) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Another spin").font(.title2.bold()); Spacer()
                NavigationLink("View all") { DetailScreen(model: model, entry: LibraryEntry(id: "played", kind: "recent", name: "Recently played")) }
                    .accessibilityIdentifier("recentlyPlayedAll")
            }
            if tracks.isEmpty { Text("Songs you listen to will appear here.").foregroundStyle(.secondary).font(.subheadline) }
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 18) {
                    ForEach(tracks.prefix(6)) { track in
                        Button { Task { await model.playback.play(track, in: tracks) } } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                RemoteCover(path: track.artwork ?? "/api/v1/artwork/\(track.id)", title: "", artwork: model.artwork)
                                    .frame(width: cardWidth, height: cardWidth).clipShape(RoundedRectangle(cornerRadius: 12))
                                Text(track.title).font(.headline).lineLimit(2)
                                Text(track.artist).foregroundStyle(.secondary).lineLimit(1)
                            }.frame(width: cardWidth, alignment: .leading)
                        }.buttonStyle(.plain).accessibilityLabel("Play recently played song \(track.title) by \(track.artist)")
                    }
                }
            }
        }
    }
    private func rail(_ title: String, items: [LibraryEntry], albums: Bool) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text(title).font(.title2.bold()); Spacer()
                if albums { NavigationLink("View all") { BrowserScreen(model: model, title: "Albums", kind: "albums") } }
                else { NavigationLink("View all") { PlaylistLibraryView(model: model) }.accessibilityIdentifier("homePlaylistsAll") }
            }
            if items.isEmpty { Text(albums ? "Albums you listen to will appear here." : "Create a playlist to keep your favorite songs together.").foregroundStyle(.secondary).font(.subheadline) }
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 18) {
                    ForEach(Array(items.prefix(6)), id: \.identity) { item in
                        CollectionCard(model: model, item: item).frame(width: cardWidth)
                    }
                }
            }
        }
    }
}

struct OriginalAlbumView: View {
    let model: AppModel
    let track: Track
    @Environment(\.dismiss) private var dismiss
    @State private var result: OriginalAlbum?
    @State private var error: String?
    @State private var loading = false
    var body: some View {
        NavigationStack {
            List {
                if loading { ProgressView("Finding original album…") }
                if let error { Text(error); Button("Retry") { Task { await load() } } }
                if let result {
                    Section(result.album.title ?? result.album.name ?? "Original album") {
                        ForEach(Array(result.items.enumerated()), id: \.offset) { _, song in
                            Button {
                                if let id = song.localTrackId { Task { await model.playback.send("play", ids: [id]) } }
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(song.title)
                                    Text(song.artist).font(.subheadline).foregroundStyle(.secondary)
                                    if song.localTrackId == nil { Text("Not in your library").font(.caption).foregroundStyle(.secondary) }
                                }
                            }.buttonStyle(.plain).disabled(song.localTrackId == nil)
                        }
                    }
                }
            }.navigationTitle("Original album").toolbar { Button("Done") { dismiss() } }
            .task { await load() }
        }
    }
    private func load() async {
        loading = true; error = nil
        defer { loading = false }
        do { result = try await model.api.originalAlbum(trackId: track.id) }
        catch { self.error = "Couldn’t load the original album. Please try again." }
    }
}
