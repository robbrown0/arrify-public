import SwiftUI
import ArrifyCore

struct CreatePlaylistView: View {
    let model: AppModel
    var track: Track? = nil
    var onCreated: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var shared = false
    @State private var saving = false
    @State private var error: String?
    @State private var created: LibraryEntry?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Playlist name", text: $name).accessibilityIdentifier("playlistName")
                    Toggle("Share with other users", isOn: $shared)
                    Text(shared ? "Other signed-in users on this server can listen. Only you can edit." : "Only you can see this playlist.").font(.footnote).foregroundStyle(.secondary)
                }
                if let track { Section("First song") { Text(track.title); Text(track.artist).foregroundStyle(.secondary) } }
                if let error { Section { Text(error).foregroundStyle(.orange) } }
            }.navigationTitle("Create playlist")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button(saving ? "Creating…" : "Create") {
                            saving = true; error = nil
                            Task {
                                do {
                                    created = try await model.api.createPlaylist(name: name.trimmingCharacters(in: .whitespacesAndNewlines), shared: shared, trackId: track?.id)
                                    await model.library.refresh()
                                    onCreated?()
                                } catch {
                                    self.error = "Couldn’t confirm the save. Check your playlists before trying again; it may already have been created."
                                }
                                saving = false
                            }
                        }.disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 120)
                            .accessibilityIdentifier("savePlaylist")
                    }
                }
                .navigationDestination(item: $created) { item in
                    DetailScreen(model: model, entry: item)
                        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                }
        }.interactiveDismissDisabled(saving)
    }
}

struct PlaylistLibraryView: View {
    let model: AppModel
    @State private var creating = false
    var body: some View {
        List {
            Section("Your playlists") {
                Button("Create playlist", systemImage: "plus") { creating = true }
                ForEach(model.library.overview?.items.filter { $0.section == "personal" } ?? [], id: \.identity) { item in
                    NavigationLink { DetailScreen(model: model, entry: item) } label: { EntryRow(item: item, artwork: model.artwork) }
                }
            }
            Section("Made for listening") {
                ForEach(model.library.overview?.items.filter { $0.section == "playlists" } ?? [], id: \.identity) { item in
                    NavigationLink { DetailScreen(model: model, entry: item) } label: { EntryRow(item: item, artwork: model.artwork) }
                }
            }
            RefreshStatus(loading: model.library.loading, updated: model.library.updated, error: model.library.error)
        }.navigationTitle("Playlists").scrollContentBackground(.hidden).background(Color.black)
            .task { await model.library.refresh() }.refreshable { await model.library.refresh() }
            .sheet(isPresented: $creating) { CreatePlaylistView(model: model) }
    }
}

struct AddToPlaylistView: View {
    let model: AppModel
    let track: Track
    @Environment(\.dismiss) private var dismiss
    @State private var creating = false
    @State private var createdNewPlaylist = false
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            List {
                Button("Create playlist", systemImage: "plus") { creating = true }
                ForEach(model.library.overview?.items.filter { $0.section == "personal" } ?? [], id: \.identity) { item in
                    Button {
                        guard let id = Int(item.id) else { return }
                        saving = true
                        Task {
                            do { _ = try await model.api.addToPlaylist(id: id, trackId: track.id); await model.library.refresh(); dismiss() }
                            catch { self.error = "Couldn’t add this song. Please try again." }
                            saving = false
                        }
                    } label: { EntryRow(item: item, artwork: model.artwork) }
                }
                if let error { Text(error).foregroundStyle(.orange) }
            }.disabled(saving).navigationTitle("Add to playlist")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() }.disabled(saving) } }
                .task { await model.library.refresh() }
                .sheet(isPresented: $creating, onDismiss: { if createdNewPlaylist { dismiss() } }) { CreatePlaylistView(model: model, track: track, onCreated: { createdNewPlaylist = true }) }
        }.interactiveDismissDisabled(saving)
    }
}
