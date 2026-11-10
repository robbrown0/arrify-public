import SwiftUI
import ArrifyCore

@MainActor @Observable final class FavoritesStore {
    let api: APIClient
    var ids: Set<Int> = []
    var pending: Set<Int> = []
    var error: String?
    private var generation = UUID()
    private var revision = 0
    init(api: APIClient) { self.api = api }
    func refresh() async {
        let token = generation, version = revision
        do {
            let result = try await api.favorites()
            guard token == generation, version == revision, !Task.isCancelled else { return }
            ids = Set(result.trackIds); error = nil
        } catch is CancellationError {} catch { if token == generation { self.error = "Saved songs couldn’t be refreshed. Pull to retry." } }
    }
    func toggle(_ id: Int) async {
        guard !pending.contains(id) else { return }
        let token = generation, liked = !ids.contains(id)
        pending.insert(id); revision += 1
        defer { if token == generation { pending.remove(id) } }
        do {
            _ = try await api.setFavorite(id, liked: liked)
            guard token == generation, !Task.isCancelled else { return }
            // Apply only this operation; another in-flight save may have a newer response.
            if liked { ids.insert(id) } else { ids.remove(id) }
            revision += 1; error = nil
        } catch { if token == generation { self.error = "Couldn’t save this change. Please try again." } }
    }
    func clear() { generation = UUID(); revision += 1; ids = []; pending = []; error = nil }
}

struct FavoriteButton: View {
    @Bindable var favorites: FavoritesStore
    let track: Track
    var body: some View {
        Button { Task { await favorites.toggle(track.id) } } label: {
            Image(systemName: favorites.ids.contains(track.id) ? "checkmark.circle.fill" : "plus.circle")
                .font(.title3).foregroundStyle(favorites.ids.contains(track.id) ? Color.green : Color.secondary)
                .frame(width: 44, height: 44)
        }.buttonStyle(.plain).disabled(favorites.pending.contains(track.id))
            .accessibilityLabel("\(favorites.ids.contains(track.id) ? "Remove from" : "Add to") Liked songs: \(track.title)")
    }
}

struct LikedSongsView: View {
    let model: AppModel
    @State private var browser: LibraryBrowser
    private let source = LibrarySource(kind: "favorites", id: "liked")
    init(model: AppModel) {
        self.model = model
        _browser = State(initialValue: LibraryBrowser(api: model.api, source: LibrarySource(kind: "favorites", id: "liked")))
    }
    var body: some View {
        List {
            Section {
                HStack {
                    Button("Play", systemImage: "play.fill") { Task { await model.playback.play(source: source) } }
                    Spacer()
                    Button("Shuffle", systemImage: "shuffle") { Task { await model.playback.play(source: source, shuffle: true) } }
                }.disabled(browser.tracks.isEmpty)
            }
            if let error = model.favorites.error { Text(error).foregroundStyle(.orange) }
            RefreshStatus(loading: browser.loading, updated: browser.updated, error: browser.error)
            ForEach(browser.tracks) { track in
                SongRow(track: track, model: model) {
                    Task { await model.playback.play(track, in: browser.tracks) }
                }
            }
            if !browser.loading && browser.tracks.isEmpty && browser.error == nil {
                ContentUnavailableView("A place for the keepers", systemImage: "plus.circle", description: Text("Tap + beside a song or in the player to save it here."))
            }
            if browser.nextOffset != nil { Button("Load more songs") { Task { await browser.load(more: true) } } }
        }.navigationTitle("Liked songs").scrollContentBackground(.hidden).background(Color.black)
            .task { await model.favorites.refresh(); await browser.load() }
            .refreshable { await model.favorites.refresh(); await browser.load() }
    }
}
