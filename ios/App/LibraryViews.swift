import SwiftUI
import ArrifyCore

struct RemoteCover: View {
    let path: String?
    let title: String
    let artwork: ArtworkStore
    var channel = false
    @State private var image: UIImage?
    var body: some View {
        ZStack {
            Color.black
            if let image {
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
            image = nil
            if let path { let result = await artwork.image(path); if !Task.isCancelled { image = result } }
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
        else if updated != nil { Label("Library updated", systemImage: "checkmark").font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("libraryUpdated") }
    }
}

struct LibraryHome: View {
    let model: AppModel
    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink { BrowserScreen(model: model, title: "Songs", kind: "tracks") } label: { Label("Songs", systemImage: "music.note") }
                    NavigationLink { BrowserScreen(model: model, title: "Albums", kind: "albums") } label: { Label("Albums", systemImage: "square.stack") }
                    NavigationLink { BrowserScreen(model: model, title: "Artists", kind: "artists") } label: { Label("Artists", systemImage: "music.mic") }
                    NavigationLink { CollectionScreen(model: model, section: "playlists", title: "Playlists") } label: { Label("Playlists", systemImage: "music.note.list") }
                }.font(.title3).listRowBackground(Color.white.opacity(0.04))
                if let overview = model.library.overview {
                    Section {
                        Text("\(overview.tracks.formatted()) songs · \(overview.albums.formatted()) albums · \(overview.artists.formatted()) artists").font(.footnote).foregroundStyle(.secondary)
                    }
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
            if item.kind != "channels" {
                Text(item.name).font(.headline).lineLimit(2)
                Text("\(item.trackCount) songs").font(.caption).foregroundStyle(.secondary)
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
    init(model: AppModel, title: String, kind: String) {
        self.model = model; self.title = title
        _browser = State(initialValue: LibraryBrowser(api: model.api, kind: kind))
    }
    var body: some View {
        List {
            Section { RefreshStatus(loading: browser.loading, updated: browser.updated, error: browser.error) }
            if browser.kind == "tracks" {
                ForEach(browser.tracks) { track in SongRow(track: track, artwork: model.artwork) { Task { await model.playback.play(track, in: browser.tracks) } } }
            } else {
                ForEach(browser.entries, id: \.identity) { item in
                    NavigationLink { DetailScreen(model: model, entry: item) } label: { EntryRow(item: item, artwork: model.artwork) }
                }
            }
            if !browser.loading && browser.tracks.isEmpty && browser.entries.isEmpty && browser.error == nil {
                ContentUnavailableView(query.isEmpty ? "No \(title.lowercased()) yet" : "No matching \(title.lowercased())", systemImage: "magnifyingglass", description: Text(query.isEmpty ? "Music added to your server will appear here." : "Try another name."))
            }
            if browser.nextOffset != nil { Button("Load more \(title.lowercased())") { Task { await browser.load(query: query, more: true) } }.disabled(browser.loading) }
            if browser.error != nil { Button("Retry") { Task { await browser.load(query: query) } } }
        }.scrollContentBackground(.hidden).background(Color.black)
        .navigationTitle(title).searchable(text: $query, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search \(title.lowercased())")
        .task(id: query) {
            do { try await Task.sleep(for: .milliseconds(query.isEmpty ? 0 : 300)) } catch { return }
            await browser.load(query: query)
        }
        .refreshable { await browser.load(query: query) }
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Refresh", systemImage: "arrow.clockwise") { Task { await browser.load(query: query) } }.accessibilityIdentifier("refreshSongs") } }
    }
}

struct SongRow: View {
    let track: Track
    let artwork: ArtworkStore
    let play: () -> Void
    var body: some View {
        Button(action: play) {
            HStack(spacing: 12) {
                RemoteCover(path: track.artwork ?? "/api/v1/artwork/\(track.id)", title: "", artwork: artwork).frame(width: 44, height: 44).clipShape(RoundedRectangle(cornerRadius: 6))
                VStack(alignment: .leading, spacing: 4) {
                    Text(track.title).foregroundStyle(.primary)
                    Text(track.artist).font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Image(systemName: "play.fill").font(.caption).foregroundStyle(.green)
            }.frame(minHeight: 48).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityLabel("Play \(track.title) by \(track.artist)")
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
                    SongRow(track: track, artwork: model.artwork) { Task { await model.playback.play(source: item.source, trackId: track.id) } }
                }
                if browser.nextOffset != nil { Button("Load more songs") { Task { await browser.load(more: true) } }.disabled(browser.loading) }
                if browser.error != nil { Button("Retry") { Task { await browser.load() } } }
                if !browser.loading && browser.total == 0 && browser.error == nil { Text("No local songs yet.").foregroundStyle(.secondary) }
            }
        }.scrollContentBackground(.hidden).background(Color.black)
        .navigationTitle(entry.kind == "channels" ? "Channel" : "Details").navigationBarTitleDisplayMode(.inline)
        .task { await browser.load() }.refreshable { await browser.load() }
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
