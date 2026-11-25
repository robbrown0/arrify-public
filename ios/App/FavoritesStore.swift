import SwiftUI
import ArrifyCore

@MainActor @Observable final class FavoritesStore {
    let api: APIClient
    var ids: Set<Int> = []
    var pending: Set<Int> = []
    var error: String?
    static let refreshError = "Saved songs couldn’t be refreshed."
    var needsRecovery = false
    var refreshing = false
    private let retryWait: @MainActor (Double) async throws -> Void
    private var generation = UUID()
    private var revision = 0
    init(api: APIClient, retryWait: @escaping @MainActor (Double) async throws -> Void = ReadRecovery.wait) { self.api = api; self.retryWait = retryWait }
    func refresh() async {
        guard !refreshing else { return }
        let token = generation, version = revision
        refreshing = true
        if error == Self.refreshError { error = nil }
        defer { if token == generation { refreshing = false } }
        do {
            let result = try await ReadRecovery.load(wait: retryWait, valid: { self.generation == token && self.revision == version }) { try await self.api.favorites() }
            guard token == generation, version == revision, !Task.isCancelled else { return }
            ids = Set(result.trackIds); error = nil; needsRecovery = false
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            if token == generation, version == revision {
                needsRecovery = ReadRecovery.delay(error, attempt: 0) != nil
                if case APIError.retryDeferred = error { needsRecovery = true }
                self.error = Self.refreshError
            }
        }
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
    func clear() { generation = UUID(); revision += 1; ids = []; pending = []; error = nil; needsRecovery = false; refreshing = false }
}

struct FavoritesRefreshStatus: View {
    let favorites: FavoritesStore
    @State private var retrying = false
    var body: some View {
        if let error = favorites.error {
            VStack(spacing: 4) {
                Text(error).font(.caption).foregroundStyle(.orange)
                if error == FavoritesStore.refreshError {
                    Button(retrying ? "Retrying…" : "Retry") {
                        guard !retrying else { return }
                        retrying = true
                        Task {
                            defer { retrying = false }
                            await favorites.refresh()
                        }
                    }.disabled(retrying)
                        .accessibilityLabel("Retry saved songs refresh")
                        .accessibilityIdentifier("retrySavedSongsRefresh")
                }
            }
        }
    }
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
                    Button(model.playback.sourceShowsPause(source) ? "Pause" : "Play", systemImage: model.playback.sourceShowsPause(source) ? "pause.fill" : "play.fill") { Task { await model.playback.toggleSource(source) } }
                    Spacer()
                    Button("Shuffle", systemImage: "shuffle") { Task { await model.playback.shuffleSource(source) } }
                }.disabled(model.favorites.ids.isEmpty)
            }
            FavoritesRefreshStatus(favorites: model.favorites)
            RefreshStatus(loading: browser.loading, updated: browser.updated, error: browser.error)
            ForEach(browser.tracks) { track in
                SongRow(track: track, model: model) {
                    Task { await model.playback.play(source: source, trackId: track.id) }
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
