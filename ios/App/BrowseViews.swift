import SwiftUI
import ArrifyCore

struct BrowseScreen: View {
    let model: AppModel
    @State private var overview: BrowseOverview?
    @State private var error: String?
    @State private var section = "All"
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack { ForEach(["All", "What’s New", "What’s Hot", "Decades", "Genres"], id: \.self) { name in
                            Button(name) { section = name }.buttonStyle(.bordered).tint(section == name ? .green : .gray)
                        } }
                    }
                    if let error { Text(error).foregroundStyle(.orange); Button("Retry") { Task { await load() } } }
                    if let overview {
                        if overview.indexing { Text("Organizing your library in the background. Refresh to see new categories.").font(.footnote).foregroundStyle(.secondary) }
                        if section == "What’s New" {
                            BrowseChartView(model: model, genres: overview.genres, recent: true)
                        } else if section == "What’s Hot" {
                            BrowseChartView(model: model, genres: overview.genres, recent: false)
                        } else {
                            if section == "All" {
                                Text("Discover").font(.title2.bold())
                                Button { section = "What’s New" } label: { discovery("What’s New", "Recent releases in the charts · past 90 days", "sparkles") }
                                Button { section = "What’s Hot" } label: { discovery("What’s Hot", "U.S. Top 40 and genre rankings · refreshed daily", "flame") }
                            }
                            if section != "Genres" { categories("Decades", overview.decades, decade: true) }
                            if section != "Decades" { categories("Genres", overview.genres, decade: false) }
                        }
                    } else if error == nil { ProgressView("Loading Browse…") }
                }.padding(16)
            }.navigationTitle("Browse").refreshable { await load() }
                .toolbar { Button("Refresh", systemImage: "arrow.clockwise") { Task { await load() } } }
                .task { await load() }
        }
    }
    private func discovery(_ name: String, _ description: String, _ icon: String) -> some View {
        HStack { Image(systemName: icon).font(.title); VStack(alignment: .leading) { Text(name).font(.headline); Text(description).font(.subheadline).foregroundStyle(.secondary) }; Spacer(); Image(systemName: "chevron.right") }
            .padding().frame(maxWidth: .infinity, alignment: .leading).background(.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
    }
    private func categories(_ title: String, _ values: [BrowseCategory], decade: Bool) -> some View {
        VStack(alignment: .leading) {
            Text(title).font(.title2.bold())
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 145), spacing: 12)], spacing: 12) {
                ForEach(Array(values.enumerated()), id: \.element.id) { index, item in
                    NavigationLink { BrowseCategoryView(model: model, title: item.name, initial: BrowseFilters(genre: decade ? "" : item.id, decade: decade ? item.id : "")) } label: {
                        VStack(alignment: .leading, spacing: 12) { Text(item.name).font(.headline); Text("\(item.count) songs").font(.subheadline); Spacer(); Image(systemName: decade ? "clock" : "music.note").font(.title) }
                            .frame(maxWidth: .infinity, minHeight: 110, alignment: .leading).padding()
                            .background([Color.teal, .purple, .blue, .orange, .pink, .indigo][index % 6].opacity(0.3), in: RoundedRectangle(cornerRadius: 12))
                    }.buttonStyle(.plain)
                }
            }
        }
    }
    private func load() async {
        do { let result = try await model.api.browse(); guard !Task.isCancelled else { return }; overview = result; error = nil }
        catch where RequestCancellation.matches(error) {} catch { self.error = error.localizedDescription }
    }
}

struct BrowseCategoryView: View {
    let model: AppModel
    let title: String
    @State private var filters: BrowseFilters
    @State private var page: BrowsePage?
    @State private var loading = false
    @State private var error: String?
    @State private var generation = UUID()
    init(model: AppModel, title: String, initial: BrowseFilters) { self.model = model; self.title = title; _filters = State(initialValue: initial) }
    var body: some View {
        List {
            if !filters.decade.isEmpty {
                Picker("Genre", selection: $filters.genre) { Text("All genres").tag(""); ForEach(page?.genreCounts ?? []) { Text($0.name).tag($0.id) } }
            }
            Picker("Content type", selection: $filters.kind) {
                Text("Essentials").tag("hits"); Text("Songs").tag("songs"); Text("Albums").tag("albums"); Text("Artists").tag("artists")
            }.pickerStyle(.segmented)
            if let error { Text(error).foregroundStyle(.orange); Button("Retry") { Task { await load() } } }
            if let page {
                Text("\(page.total) \(filters.kind == "albums" ? "albums" : filters.kind == "artists" ? "artists" : "songs")").font(.subheadline).foregroundStyle(.secondary)
                if filters.kind == "hits" { Text("Up to 200 familiar recordings, selected using curated appearances and available listen counts. This is not a historical chart.").font(.footnote).foregroundStyle(.secondary) }
                if let tracks = page.tracks, !tracks.isEmpty {
                    HStack { Button("Play", systemImage: "play.fill") { play() }; Spacer(); Button("Shuffle", systemImage: "shuffle") { play(shuffle: true) } }
                    ForEach(tracks) { track in SongRow(track: track, model: model) { play(track: track.id) } }
                } else if let items = page.items, !items.isEmpty {
                    ForEach(items) { item in NavigationLink { DetailScreen(model: model, entry: item.libraryEntry(kind: filters.kind)) } label: {
                        HStack { RemoteCover(path: item.artwork, title: "", artwork: model.artwork).frame(width: 54, height: 54); VStack(alignment: .leading) { Text(item.name); Text(item.artist ?? "").font(.subheadline).foregroundStyle(.secondary) } }
                    } }
                } else { ContentUnavailableView("No matching music yet", systemImage: "music.note", description: Text(page.indexing ? "Your library is still being organized. Refresh shortly." : "Try Songs or another category.")) }
                if page.nextOffset < page.total { Button("Load more") { Task { await load(more: true) } }.disabled(loading) }
            }
            if loading { ProgressView() }
        }.navigationTitle(title).task(id: filters) { await load() }.refreshable { await load() }
    }
    private func play(track: Int? = nil, shuffle: Bool = false) { let source = filters.source; Task { await model.playback.send("play", source: source, trackId: track, shuffle: shuffle) } }
    private func load(more: Bool = false) async {
        if more && loading { return }
        let token = UUID(); generation = token; loading = true
        let previous = more ? page : nil
        if !more { page = nil }
        defer { if generation == token { loading = false } }
        do {
            var result = try await model.api.browsePage(filters, offset: previous?.nextOffset ?? 0)
            guard generation == token, !Task.isCancelled else { return }
            if let previous { if let tracks = result.tracks { result.tracks = (previous.tracks ?? []) + tracks }; if let items = result.items { result.items = (previous.items ?? []) + items } }
            page = result; error = nil
        } catch where RequestCancellation.matches(error) {} catch { if generation == token { self.error = error.localizedDescription } }
    }
}

struct BrowseChartView: View {
    let model: AppModel
    let genres: [BrowseCategory]
    let recent: Bool
    @State private var genre = "overall"
    @State private var chart: BrowseChart?
    @State private var error: String?
    @State private var generation = UUID()
    private var requestKey: String { genre + (recent ? ":new" : ":hot") }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(recent ? "What’s New" : "What’s Hot — Top 40").font(.title2.bold())
            Picker("Chart genre", selection: $genre) { Text("All genres · U.S.").tag("overall"); ForEach(genres.filter { $0.id != "other" }) { Text($0.name).tag($0.id) } }
            Button("Refresh chart", systemImage: "arrow.clockwise") { Task { await load() } }
            if let error { Text(error).foregroundStyle(.orange) }
            if let chart {
                DisclosureGroup("Chart info") {
                    Text(chart.source ?? "Chart snapshot is being prepared.")
                    if let fetched = chart.fetched { Text("Updated \(Date(timeIntervalSince1970: fetched).formatted())") }
                    Text(recent ? "Songs released in the past 90 days appearing in these rankings, in chart order. Not a complete new-release catalog." : "U.S. rankings refreshed daily. Includes releases from any year.")
                    Text("Play songs in your library, or request their official album.")
                }.font(.footnote).foregroundStyle(.secondary)
                if chart.pending { ContentUnavailableView("Charts are being prepared", systemImage: "chart.bar", description: Text("Browse your library, then refresh the chart shortly.")) }
                else {
                    if chart.stale { Text("Showing the last available chart while updates retry.").font(.footnote) }
                    let ids = chart.items.compactMap { $0.track?.id }
                    if !ids.isEmpty { HStack { Button("Play available songs") { Task { await model.playback.send("play", ids: ids) } }; Spacer(); Button("Shuffle", systemImage: "shuffle") { Task { await model.playback.send("play", ids: ids.shuffled()) } } } }
                    ForEach(Array(chart.items.enumerated()), id: \.element.id) { index, item in
                        HStack(alignment: .top) {
                            Text("\(recent ? index + 1 : item.rank)").font(.caption).foregroundStyle(.secondary).frame(width: 28)
                            VStack(alignment: .leading) {
                                if let track = item.track { SongRow(track: track, model: model) { Task { await model.playback.send("play", ids: ids, trackId: track.id) } } }
                                else { BrowseRequestRow(model: model, entry: item, genre: genre) }
                                if recent { Text("Chart #\(item.rank)").font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                        Divider()
                    }
                    if chart.items.isEmpty { Text("No recent releases in this chart. Try another genre or the full Top 40.").foregroundStyle(.secondary) }
                }
            } else if error == nil { ProgressView("Loading chart…") }
        }.task(id: requestKey) { await load() }
    }
    private func load() async {
        let token = UUID(); generation = token; chart = nil; error = nil
        do { let result = try await model.api.browseChart(genre: genre, recent: recent); guard generation == token, !Task.isCancelled else { return }; chart = result }
        catch where RequestCancellation.matches(error) {} catch { if generation == token { self.error = error.localizedDescription } }
    }
}

private struct BrowseRequestRow: View {
    let model: AppModel
    let entry: BrowseChart.Entry
    let genre: String
    @State private var busy = false
    @State private var submitted = false
    @State private var message: String?
    var body: some View {
        VStack(alignment: .leading) {
            HStack { VStack(alignment: .leading) { Text(entry.title); Text(entry.artist).font(.subheadline).foregroundStyle(.secondary) }; Spacer()
                Button(submitted || entry.requested ? "Requested" : busy ? "Requesting…" : "Request") {
                    busy = true
                    Task {
                        do { let result = try await model.api.requestChartSong(genre: genre, entry: entry.requestKey); submitted = true; message = result.available == true ? "Already available in your library. Refresh the chart." : "Album requested." }
                        catch { message = error.localizedDescription }
                        busy = false
                    }
                }.disabled(busy || submitted || entry.requested)
            }
            if let message { Text(message).font(.footnote).foregroundStyle(.secondary) }
        }
    }
}
