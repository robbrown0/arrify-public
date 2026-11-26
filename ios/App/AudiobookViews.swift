import SwiftUI
import ArrifyCore
import AVKit

struct AudiobookShelf: View {
    let model: AppModel
    @State private var items: [Audiobook] = []
    @State private var libraries: [AudiobookLibrary.Library] = []
    @State private var library = ""
    @State private var query = ""
    @State private var filter = "all"
    @State private var next: Int?
    @State private var loading = false
    @State private var message: String?
    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading, spacing:20) {
                Text("Your next good listen").font(.subheadline).foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators:false) { filterButtons }
                if libraries.count > 1 {
                    Picker("Library", selection:$library) { ForEach(libraries) { Text($0.name).tag($0.id) } }.pickerStyle(.menu)
                }
                if let message { Text(message).foregroundStyle(.secondary); Button("Try again") { Task { await load() } } }
                LazyVGrid(columns:[GridItem(.adaptive(minimum:145, maximum:230), alignment:.top)], alignment:.leading, spacing:24) {
                    ForEach(displayed) { book in AudiobookShelfCard(model:model, book:book) }
                }
                if loading { ProgressView().frame(maxWidth:.infinity) }
                if !loading && displayed.isEmpty && message == nil {
                    ContentUnavailableView(filter == "downloads" ? "Take a story with you" : "Your bookshelf", systemImage:"book.closed", description:Text(filter == "downloads" ? "Download a book to listen without a connection." : "Your books will appear here when available."))
                }
                if next != nil && filter != "downloads" { Button("More books") { Task { await load(more:true) } }.disabled(loading) }
            }.padding(24)
        }.background(Color.black).navigationTitle("Audiobooks")
            .searchable(text:$query, prompt:"Books, authors, narrators")
            .toolbar { NavigationLink { AudiobookSettings(books:model.books) } label: { Image(systemName:"slider.horizontal.3") }.accessibilityLabel("Listening settings") }
            .task(id:query + ":" + filter + ":" + library) {
                do { if !query.isEmpty { try await Task.sleep(for:.milliseconds(250)) }; await load() } catch {}
            }
            .refreshable { await load() }
    }
    private var filterButtons: some View {
        HStack { ForEach(["all", "started", "finished", "downloads"], id:\.self) { value in filterButton(value) } }
    }
    private func filterButton(_ value: String) -> some View {
        let selected = filter == value
        return Button(action:{ filter = value }) { Text(filterTitle(value)) }
            .buttonStyle(.bordered).tint(selected ? Color.green : Color.gray)
    }
    private var displayed: [Audiobook] {
        if filter == "downloads" { return model.books.downloads.records.values.map { $0.manifest.book }.filter { query.isEmpty || ($0.title + " " + $0.author).localizedCaseInsensitiveContains(query) }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending } }
        return items
    }
    private func filterTitle(_ value: String) -> String { ["all":"All books", "started":"In progress", "finished":"Finished", "downloads":"Downloaded"][value] ?? value }
    private func load(more: Bool = false) async {
        guard filter != "downloads" else { message = nil; return }
        let search = query, selected = filter, currentLibrary = library
        loading = true; defer { loading = false }
        do {
            let value = try await model.api.audiobookLibrary(query:search, library:currentLibrary, page:more ? next ?? 0 : 0, filter:selected)
            guard !Task.isCancelled, search == query, selected == filter, currentLibrary == library else { return }
            items = more ? items + value.items : value.items; libraries = value.libraries; next = value.nextPage; message = nil
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch { message = "Your bookshelf is reconnecting. Downloaded books are ready to listen." }
    }
}

private struct AudiobookShelfCard: View {
    let model: AppModel
    let book: Audiobook
    var body: some View {
        NavigationLink { AudiobookDetail(model:model, initial:book) } label: {
            VStack(alignment:.leading, spacing:7) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork)
                    .aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:12))
                Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                if book.progress?.isFinished == true { Label("Finished", systemImage:"checkmark.circle").font(.caption) }
                else { Text(AudiobookTimeline.time(book.remaining) + " left").font(.caption).foregroundStyle(.secondary) }
                if model.books.downloads.complete(book.id) { Label("Downloaded", systemImage:"arrow.down.circle.fill").font(.caption).foregroundStyle(.green) }
            }.frame(maxWidth:.infinity, alignment:.leading)
        }.buttonStyle(.plain).accessibilityIdentifier("audiobook-" + book.id)
    }
}

struct AudiobookDetail: View {
    let model: AppModel
    let initial: Audiobook
    @State private var detail: Audiobook?
    @State private var error: String?
    private var book: Audiobook { detail ?? initial }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:20) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork).frame(maxWidth:300).aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:16)).frame(maxWidth:.infinity)
                Text(book.title).font(.largeTitle.bold()); Text(book.author).font(.title3).foregroundStyle(.secondary)
                if !book.narrator.isEmpty { Text("Narrated by " + book.narrator).foregroundStyle(.secondary) }
                Text(AudiobookTimeline.time(book.duration)).foregroundStyle(.secondary)
                Button { model.books.play(book) } label: { Label((book.progress?.currentTime ?? 0) > 0 ? "Continue listening" : "Listen now", systemImage:"play.fill").frame(maxWidth:.infinity, minHeight:44) }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("listenAudiobook")
                HStack {
                    Button("Play next") { Task { await model.books.queueBook(book.id, action:"next") } }
                    Spacer()
                    Button("Add to Up Next") { Task { await model.books.queueBook(book.id, action:"last") } }
                }.frame(minHeight:44)
                downloadControls
                if (book.progress?.currentTime ?? 0) > 0 { Button("Play from beginning") { model.books.play(book, restart:true) } }
                if let error { Text(error).foregroundStyle(.secondary) }
                if let message = model.books.message { Text(message).font(.footnote).foregroundStyle(.secondary) }
                Text(plainDescription).textSelection(.enabled)
                if let chapters = book.chapters {
                    Text("Chapters").font(.title2.bold())
                    ForEach(chapters) { chapter in HStack { Text(chapter.title); Spacer(); Text(AudiobookTimeline.time(chapter.end-chapter.start)).foregroundStyle(.secondary) }.frame(minHeight:44) }
                }
            }.padding(24)
        }.background(Color.black).navigationTitle("Audiobook").navigationBarTitleDisplayMode(.inline)
            .task {
                do { detail = try await model.api.audiobook(initial.id) }
                catch { detail = model.books.downloads.records[initial.id]?.manifest.book; if detail == nil { self.error = "Connect to load this book’s chapters." } }
            }
    }
    private var plainDescription: String { book.description.replacingOccurrences(of:"<[^>]*>", with:" ", options:.regularExpression) }
    @ViewBuilder private var downloadControls: some View {
        let downloads = model.books.downloads
        if downloads.complete(book.id) {
            HStack { Label("Downloaded", systemImage:"checkmark.circle.fill").foregroundStyle(.green); Spacer(); Button("Remove download", role:.destructive) { Task { await downloads.remove(book.id) } } }.frame(minHeight:44)
        } else if downloads.working.contains(book.id) {
            HStack { ProgressView(); Text("Downloading · \(downloads.completedParts(book.id)) of \(book.parts?.count ?? 1) parts"); Spacer(); Button("Pause") { Task { await downloads.pause(book.id) } } }.frame(minHeight:44)
        } else {
            Button { Task { await downloads.download(book.id) } } label: { Label(downloads.records[book.id] == nil ? "Download for offline listening" : "Resume download", systemImage:"arrow.down.circle").frame(minHeight:44) }
        }
        if let error = downloads.errors[book.id] { Text(error).font(.footnote).foregroundStyle(.secondary) }
    }
}

struct AudiobookMiniPlayer: View {
    let model: AppModel
    var body: some View {
        if let book = model.books.book {
            HStack(spacing:12) {
                Button { model.books.presented = true } label: {
                    HStack(spacing:12) {
                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork).frame(width:52, height:52).clipShape(RoundedRectangle(cornerRadius:8))
                        VStack(alignment:.leading) { Text(book.title).font(.headline).lineLimit(1); Text(model.books.chapter?.title ?? book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                    }.frame(maxWidth:.infinity, alignment:.leading).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel("Open audiobook player")
                Button { model.books.seek(model.books.position-Double(model.books.preferences.backSeconds)) } label: { Image(systemName:"gobackward").frame(width:44, height:44) }.accessibilityLabel("Skip backward")
                Button { model.books.toggle() } label: { Image(systemName:model.books.playing || model.books.preparing ? "pause.fill" : "play.fill").frame(width:44, height:44) }.accessibilityLabel(model.books.playing ? "Pause audiobook" : "Play audiobook")
            }.padding(8).background(.regularMaterial, in:RoundedRectangle(cornerRadius:16)).accessibilityIdentifier("audiobookMiniPlayer")
        }
    }
}

struct AudiobookPlayer: View {
    let model: AppModel
    @State private var tab = "Chapters"
    @State private var seeking: Double?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing:20) {
                    if let book = model.books.book {
                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork).frame(maxWidth:320).aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:18))
                        VStack(alignment:.leading, spacing:8) {
                            Text(book.author).foregroundStyle(.secondary)
                            Text(book.title).font(.title.bold())
                            Text(model.books.chapter?.title ?? "Audiobook").foregroundStyle(.secondary)
                        }.frame(maxWidth:.infinity, alignment:.leading)
                        Slider(value:Binding(get:{ seeking ?? model.books.position }, set:{ seeking = $0 }), in:0...max(1,book.duration)) { editing in
                            if !editing, let target = seeking { model.books.seek(target); seeking = nil }
                        }.accessibilityLabel("Book listening position").accessibilityValue(AudiobookTimeline.time(model.books.position)).accessibilityIdentifier("audiobookPosition")
                        HStack { Text(AudiobookTimeline.time(model.books.position)); Spacer(); Text("−" + AudiobookTimeline.time(book.duration-model.books.position)) }.font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        HStack(spacing:36) {
                            Button { model.books.seek(model.books.position-Double(model.books.preferences.backSeconds)) } label: { VStack { Image(systemName:"gobackward").font(.title); Text("\(model.books.preferences.backSeconds)").font(.caption) }.frame(width:60, height:60) }.accessibilityLabel("Skip backward \(model.books.preferences.backSeconds) seconds")
                            Button { model.books.toggle() } label: { Image(systemName:model.books.playing || model.books.preparing ? "pause.fill" : "play.fill").font(.largeTitle).frame(width:84, height:84).foregroundStyle(.black).background(.white, in:Circle()) }.accessibilityLabel(model.books.playing ? "Pause audiobook" : "Play audiobook").accessibilityIdentifier("audiobookPlayPause")
                            Button { model.books.seek(model.books.position+Double(model.books.preferences.forwardSeconds)) } label: { VStack { Image(systemName:"goforward").font(.title); Text("\(model.books.preferences.forwardSeconds)").font(.caption) }.frame(width:60, height:60) }.accessibilityLabel("Skip forward \(model.books.preferences.forwardSeconds) seconds")
                        }
                        HStack {
                            Picker("Speed", selection:Binding(get:{ model.books.preferences.speed }, set:{ speed in var value = model.books.preferences; value.speed = speed; Task { await model.books.updatePreferences(value) } })) {
                                ForEach([0.5,0.75,1,1.1,1.25,1.5,1.75,2,2.5,3], id:\.self) { speed in Text(String(format:"%g×",speed)).tag(speed) }
                            }.accessibilityIdentifier("audiobookSpeed")
                            Spacer()
                            Picker("Sleep timer", selection:Binding(get:{ model.books.sleepChoice }, set:{ model.books.setSleep($0) })) {
                                Text("Off").tag("off"); Text("15 minutes").tag("15"); Text("30 minutes").tag("30"); Text("45 minutes").tag("45"); Text("60 minutes").tag("60"); Text("End of chapter").tag("chapter")
                            }.accessibilityIdentifier("audiobookSleep")
                            AirPlayPicker(enabled:true).frame(width:44, height:44)
                        }.frame(minHeight:44)
                        if let message = model.books.message { Text(message).font(.footnote).foregroundStyle(.secondary).accessibilityIdentifier("audiobookMessage") }
                        if model.books.conflict {
                            Text("Your earlier offline place is kept on this device.").font(.footnote)
                            Button("Resume server position") { model.books.play(book, useRemote:true) }
                        }
                        Picker("Book details", selection:$tab) { Text("Chapters").tag("Chapters"); Text("Up Next").tag("Up Next"); Text("Bookmarks").tag("Bookmarks") }.pickerStyle(.segmented)
                        panel(book)
                    } else { ProgressView(model.books.preparing ? "Opening your audiobook…" : "Choose a book to begin") }
                }.padding(24).frame(maxWidth:760).frame(maxWidth:.infinity)
            }.background(LinearGradient(colors:[Color.green.opacity(0.12),.black], startPoint:.top, endPoint:.bottom))
                .navigationTitle("Audiobook").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement:.topBarLeading) { Button { model.books.presented = false } label: { Image(systemName:"chevron.down").frame(width:44, height:44) }.accessibilityLabel("Close audiobook player") }
                    ToolbarItem(placement:.topBarTrailing) { Button { Task { await model.books.addBookmark() } } label: { Image(systemName:"bookmark").frame(width:44, height:44) }.accessibilityLabel("Save bookmark") }
                }
        }.onChange(of:tab) { if tab == "Up Next" { Task { await model.books.refreshQueue() } } }
    }
    @ViewBuilder private func panel(_ book: Audiobook) -> some View {
        switch tab {
        case "Chapters":
            ForEach(book.chapters ?? []) { chapter in
                Button { model.books.seek(chapter.start) } label: { HStack { Text(chapter.title).multilineTextAlignment(.leading); Spacer(); Text(AudiobookTimeline.time(chapter.end-chapter.start)).font(.caption).foregroundStyle(.secondary) }.frame(maxWidth:.infinity, minHeight:44) }.foregroundStyle(model.books.chapter?.start == chapter.start ? .green : .primary)
            }
        case "Up Next":
            Toggle("Continue to the next queued book", isOn:Binding(get:{ model.books.preferences.continuous }, set:{ enabled in var value = model.books.preferences; value.continuous = enabled; Task { await model.books.updatePreferences(value) } }))
            if model.books.queue.isEmpty { Text("Your chapters stay in this book. Add another book with Play next or Add to Up Next.").foregroundStyle(.secondary) }
            ForEach(model.books.queue) { entry in
                HStack {
                    VStack(alignment:.leading) { Text(entry.title); Text(entry.author).font(.caption).foregroundStyle(.secondary) }
                    Spacer()
                    Menu {
                        Button("Listen now") { Task { await model.books.playQueued(entry.id) } }
                        Button("Move earlier") { Task { await model.books.editQueue(entry.id, delta:-1) } }
                        Button("Move later") { Task { await model.books.editQueue(entry.id, delta:1) } }
                        Button("Remove from Up Next", role:.destructive) { Task { await model.books.editQueue(entry.id) } }
                    } label: { Image(systemName:"ellipsis").frame(width:44,height:44) }.accessibilityLabel("Queue actions for " + entry.title)
                }.frame(minHeight:44)
            }
        default:
            if model.books.bookmarks.isEmpty { Text("Save a moment worth coming back to with the bookmark button.").foregroundStyle(.secondary) }
            ForEach(model.books.bookmarks) { mark in
                HStack {
                    Button { model.books.seek(mark.time) } label: { HStack { Text(mark.title); Spacer(); Text(AudiobookTimeline.time(mark.time)) }.frame(maxWidth:.infinity, minHeight:44) }
                    Button(role:.destructive) { Task { await model.books.removeBookmark(mark) } } label: { Image(systemName:"trash").frame(width:44,height:44) }.accessibilityLabel("Delete bookmark " + mark.title)
                }
            }
        }
    }
}

struct AudiobookSettings: View {
    @Bindable var books: AudiobookStore
    var body: some View {
        Form {
            Toggle("Show Audiobooks", isOn:Binding(get:{ books.preferences.visible }, set:{ update(visible:$0) }))
            Section("Skip intervals") {
                Picker("Backward", selection:Binding(get:{ books.preferences.backSeconds }, set:{ update(back:$0) })) { ForEach([10,15,20,30,60,90], id:\.self) { Text("\($0) seconds").tag($0) } }
                Picker("Forward", selection:Binding(get:{ books.preferences.forwardSeconds }, set:{ update(forward:$0) })) { ForEach([10,15,20,30,60,90], id:\.self) { Text("\($0) seconds").tag($0) } }
            }
            Section {
                Toggle("Continue listening", isOn:Binding(get:{ books.preferences.continuous }, set:{ update(continuous:$0) }))
                Text("When a book finishes, play the next book in Up Next. Off by default.").font(.footnote).foregroundStyle(.secondary)
            }
        }.navigationTitle("Audiobooks")
    }
    private func update(visible: Bool? = nil, back: Int? = nil, forward: Int? = nil, continuous: Bool? = nil) {
        var value = books.preferences
        if let visible { value.visible = visible }; if let back { value.backSeconds = back }; if let forward { value.forwardSeconds = forward }; if let continuous { value.continuous = continuous }
        Task { await books.updatePreferences(value) }
    }
}
