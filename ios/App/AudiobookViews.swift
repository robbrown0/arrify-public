import SwiftUI
import ArrifyCore
import AVKit

struct AudiobookShelf: View {
    let model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var section = "books"
    @AppStorage("audiobookListLayout") private var listLayout = true
    @AppStorage("audiobookSort") private var savedSort = "title"
    @Bindable private var shelf: AudiobookShelfStore
    init(model: AppModel) { self.model = model; _shelf = Bindable(wrappedValue:model.books.shelf) }
    private var downloaded: Bool { shelf.filter == "downloads" }
    private var showsLoading: Bool { !downloaded && (shelf.loading || !shelf.hasLoaded && shelf.message == nil) }
    private var showsEmpty: Bool {
        if downloaded { return displayed.isEmpty }
        return shelf.hasLoaded && !shelf.loading && displayed.isEmpty && shelf.message == nil
    }
    private var taskKey: String { shelf.requestKey + ":" + section + ":" + String(scenePhase == .active) }
    private var recoveryKey: String { taskKey + ":" + String(shelf.needsRecovery) }
    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading, spacing:20) {
                Picker("Browse audiobooks", selection:$section) {
                    Text("Books").tag("books"); Text("Authors").tag("authors"); Text("Series").tag("series")
                }.pickerStyle(.segmented).accessibilityIdentifier("audiobookBrowseSections")
                libraryPicker
                if section == "books" {
                    if shelf.filter == "all", shelf.query.isEmpty, !model.books.resumeBooks.isEmpty {
                        Text("Continue listening").font(.title2.bold())
                        ScrollView(.horizontal, showsIndicators:false) {
                            HStack(alignment:.top, spacing:16) { ForEach(model.books.resumeBooks) { book in
                                Button { model.books.play(book) } label: {
                                    VStack(alignment:.leading, spacing:7) {
                                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").aspectRatio(1,contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:12))
                                        Text(book.title).font(.headline).lineLimit(2)
                                        Text("Resume · " + AudiobookTimeline.time(book.remaining) + " left").font(.caption).foregroundStyle(.secondary)
                                    }.frame(width:155,alignment:.leading)
                                }.buttonStyle(.plain).accessibilityIdentifier("continue-audiobook-" + book.id)
                            } }
                        }
                    }
                    ScrollView(.horizontal, showsIndicators:false) { filterButtons }
                    HStack {
                        Picker("Sort books", selection:$savedSort) {
                            Text("Title").tag("title"); Text("Author").tag("author"); Text("Publication year · newest").tag("release")
                            Text("Recently added").tag("added"); Text("Length · shortest").tag("duration")
                        }.disabled(!shelf.query.isEmpty)
                        Spacer()
                        Button { listLayout.toggle() } label: { Image(systemName:listLayout ? "square.grid.2x2" : "list.bullet") }
                            .accessibilityLabel(listLayout ? "Show cover grid" : "Show book list")
                    }
                    recoveryStatus
                    bookGrid
                } else {
                    AudiobookGroupBrowser(model:model, kind:section, library:shelf.library, query:shelf.query).id(section + shelf.library)
                }
                if section == "books" && showsLoading { ProgressView().frame(maxWidth:.infinity) }
                if section == "books" && showsEmpty { emptyShelf }
                if section == "books" && shelf.next != nil && !downloaded { Button("More books") { Task { await shelf.load(more:true) } }.disabled(shelf.loading) }
            }.padding(24)
        }.background(Color.black).navigationTitle("Audiobooks")
            .searchable(text:$shelf.query, prompt:section == "books" ? "Books, authors, narrators" : "Search " + section)
            .onChange(of:savedSort) { shelf.sort = savedSort }
            .onAppear { if savedSort == "release_date" { savedSort = "release" }; shelf.sort = savedSort }
            .toolbar { NavigationLink { AudiobookSettings(books:model.books) } label: { Image(systemName:"slider.horizontal.3") }.accessibilityLabel("Listening settings") }
            .task(id:taskKey) {
                guard scenePhase == .active else { return }
                guard section == "books" else { return }
                do { if !shelf.query.isEmpty { try await Task.sleep(for:.milliseconds(250)) }; await shelf.load() } catch {}
            }
            .refreshable { await shelf.load() }
            .task(id:recoveryKey) {
                if scenePhase == .active && shelf.filter != "downloads" && shelf.needsRecovery { await shelf.reconnect() }
            }
    }
    @ViewBuilder private var libraryPicker: some View {
        if shelf.libraries.count > 1 {
            Picker("Library",selection:$shelf.library) { ForEach(shelf.libraries) { library in Text(library.name).tag(library.id) } }.pickerStyle(.menu)
        }
    }
    @ViewBuilder private var recoveryStatus: some View {
        if !downloaded, let message = shelf.message {
            Text(message).foregroundStyle(.secondary)
            Button("Try again") { Task { await shelf.retry() } }
        }
    }
    private var bookGrid: some View {
        Group {
            if listLayout { LazyVStack(spacing:16) { ForEach(displayed) { book in AudiobookShelfRow(model:model, book:book) } } }
            else { LazyVGrid(columns:[GridItem(.adaptive(minimum:145,maximum:230),alignment:.top)],alignment:.leading,spacing:24) {
                ForEach(displayed) { book in AudiobookShelfCard(model:model,book:book) }
            } }
        }
    }
    private var filterButtons: some View {
        HStack { ForEach(["all", "unstarted", "started", "finished", "downloads"], id:\.self) { value in filterButton(value) } }
    }
    private var emptyShelf: some View {
        let title: String = shelf.filter == "downloads" ? "Take a story with you" : "Your bookshelf"
        let detail: String = shelf.filter == "downloads" ? "Download a book to listen without a connection." : "Your books will appear here when available."
        return VStack(spacing:12) {
            Image(systemName:"book.closed").font(.largeTitle).foregroundStyle(.secondary)
            Text(title).font(.title2.bold())
            Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }.frame(maxWidth:.infinity).padding(.vertical,32)
    }
    private func filterButton(_ value: String) -> some View {
        let selected = shelf.filter == value
        return Button(action:{ shelf.filter = value }) { Text(filterTitle(value)) }
            .buttonStyle(.bordered).tint(selected ? Color.green : Color.gray)
    }
    private var displayed: [Audiobook] {
        if shelf.filter == "downloads" { return model.books.downloads.records.values.map { $0.book }.filter { shelf.query.isEmpty || ($0.title + " " + $0.author).localizedCaseInsensitiveContains(shelf.query) }.sorted { left, right in
            switch savedSort {
            case "author": return left.author.localizedStandardCompare(right.author) == .orderedAscending
            case "release": return (left.publishedYear ?? "") > (right.publishedYear ?? "")
            case "added": return (left.addedAt ?? 0) > (right.addedAt ?? 0)
            case "duration": return left.duration < right.duration
            default: return left.title.localizedStandardCompare(right.title) == .orderedAscending
            }
        } }
        return shelf.items
    }
    private func filterTitle(_ value: String) -> String { ["all":"All books", "unstarted":"Not started", "started":"In progress", "finished":"Finished", "downloads":"Downloaded"][value] ?? value }
}

private struct AudiobookShelfCard: View {
    let model: AppModel
    let book: Audiobook
    var body: some View {
        NavigationLink { AudiobookDetail(model:model, initial:book) } label: {
            VStack(alignment:.leading, spacing:7) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed")
                    .aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:12))
                Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                if let series = book.seriesEntries?.first { Text(series.label).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                if book.progress?.isFinished == true { Label("Finished", systemImage:"checkmark.circle").font(.caption) }
                else { Text(AudiobookTimeline.time(book.remaining) + " left").font(.caption).foregroundStyle(.secondary) }
                AudiobookDownloadStatus(downloads:model.books.downloads, book:book)
            }.frame(maxWidth:.infinity, alignment:.leading)
        }.buttonStyle(.plain).accessibilityIdentifier("audiobook-" + book.id)
    }
}

struct AudiobookDetail: View {
    let model: AppModel
    let initial: Audiobook
    @State private var detail: Audiobook?
    @State private var error: String?
    @State private var markingUnread = false
    @State private var detailGeneration = UUID()
    private var book: Audiobook { detail ?? initial }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:20) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(maxWidth:300).aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:16))
                    .overlay(alignment:.bottomTrailing) { AudiobookDownloadIndicator(downloads:model.books.downloads,book:book).padding(10) }.frame(maxWidth:.infinity)
                Text(book.title).font(.largeTitle.bold())
                if let authors = book.authors, !authors.isEmpty {
                    ForEach(authors) { author in NavigationLink(author.name) { AudiobookScopedShelf(model:model, name:author.name, library:book.libraryId, author:author.id) }.font(.title3) }
                } else { Text(book.author).font(.title3).foregroundStyle(.secondary) }
                ForEach(book.seriesEntries ?? []) { series in
                    NavigationLink(series.label) { AudiobookScopedShelf(model:model, name:series.name, library:book.libraryId, series:series.id) }
                }
                if let release = book.publishedDate ?? book.publishedYear { Text("Published " + release).font(.subheadline).foregroundStyle(.secondary) }
                if !book.narrator.isEmpty { Text("Narrated by " + book.narrator).foregroundStyle(.secondary) }
                Text(AudiobookTimeline.time(book.duration)).foregroundStyle(.secondary)
                Button { model.books.play(book) } label: { Label(listenTitle, systemImage:"play.fill").frame(maxWidth:.infinity, minHeight:44) }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("listenAudiobook")
                    .disabled(markingUnread)
                HStack {
                    Button("Play next") { Task { await model.books.queueBook(book.id, action:"next") } }
                    Spacer()
                    Button("Add to Up Next") { Task { await model.books.queueBook(book.id, action:"last") } }
                }.frame(minHeight:44)
                downloadControls
                if (book.progress?.currentTime ?? 0) > 0 { Button("Play from beginning") { model.books.play(book, restart:true) } }
                Button {
                    markingUnread = true; error = nil; detailGeneration = UUID()
                    Task {
                        defer { markingUnread = false }
                        do { detail = try await model.books.markUnread(book) }
                        catch { self.error = AudiobookPlaybackIssue.classify(error).message }
                    }
                } label: {
                    HStack { if markingUnread { ProgressView() }; Label("Mark as unread",systemImage:"arrow.counterclockwise") }.frame(minHeight:44)
                }.disabled(markingUnread).accessibilityIdentifier("markAudiobookUnread")
                    .accessibilityHint("Reset the saved position to the beginning. Keep downloads and bookmarks.")
                if let error { Text(error).foregroundStyle(.secondary) }
                if let message = model.books.message { Text(message).font(.footnote).foregroundStyle(.secondary) }
                Text(plainDescription).textSelection(.enabled)
                if let chapters = book.chapters {
                    Text("Chapters").font(.title2.bold())
                    ForEach(chapters) { chapter in
                        AudiobookChapterRow(chapter:chapter, current:model.books.book?.id == book.id && model.books.chapter?.id == chapter.id, playing:model.books.playing, waiting:model.books.showsPauseControl && !model.books.playing) {
                            if model.books.active, model.books.book?.id == book.id { model.books.seek(chapter.start,resumePlayback:true) }
                            else { model.books.play(book,startingAt:chapter.start) }
                        }
                    }
                }
            }.padding(24)
        }.background(Color.black).navigationTitle("Audiobook").navigationBarTitleDisplayMode(.inline)
            .task {
                let generation = detailGeneration
                do {
                    let value = try await model.api.audiobook(initial.id)
                    guard generation == detailGeneration, !Task.isCancelled else { return }
                    detail = value
                } catch {
                    guard generation == detailGeneration, !Task.isCancelled else { return }
                    detail = model.books.downloads.records[initial.id]?.book
                    if detail == nil { self.error = "Connect to load this book’s chapters." }
                }
            }
    }
    private var plainDescription: String { book.description.replacingOccurrences(of:"<[^>]*>", with:" ", options:.regularExpression) }
    private var listenTitle: String { (book.progress?.currentTime ?? 0) > 0 ? "Continue listening" : "Listen now" }
    @ViewBuilder private var downloadControls: some View {
        let downloads = model.books.downloads
        if downloads.complete(book.id) {
            HStack { Label("Downloaded", systemImage:"checkmark.circle.fill").foregroundStyle(.green); Spacer(); Button("Remove download", role:.destructive) { Task { await model.books.removeDownload(book.id) } } }.frame(minHeight:44)
        } else if downloads.working.contains(book.id) {
            VStack(alignment:.leading) {
                ProgressView(value:downloads.fraction(book.id))
                HStack { Text("Downloading · \(Int(downloads.fraction(book.id)*100))%"); Spacer(); Button("Pause") { Task { await downloads.pause(book.id) } } }.frame(minHeight:44)
            }
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
                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(width:52, height:52).clipShape(RoundedRectangle(cornerRadius:8))
                        VStack(alignment:.leading) { Text(book.title).font(.headline).lineLimit(1); Text(model.books.chapter?.title ?? book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1) }
                    }.frame(maxWidth:.infinity, alignment:.leading).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityLabel("Open audiobook player").accessibilityIdentifier("audiobookMiniPlayer")
                Button { model.books.seek(model.books.position-Double(model.books.preferences.backSeconds)) } label: { Image(systemName:"gobackward").frame(width:44, height:44) }.accessibilityLabel("Skip backward")
                Button { model.books.toggle() } label: { Image(systemName:model.books.showsPauseControl ? "pause.fill" : "play.fill").frame(width:44, height:44) }.accessibilityLabel(model.books.playbackControlLabel)
            }.padding(8).background(.regularMaterial, in:RoundedRectangle(cornerRadius:16))
        }
    }
}

struct AudiobookPlayer: View {
    let model: AppModel
    @State private var tab = "Chapters"
    @State private var seeking: Double?
    @State private var keepLocal = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing:20) {
                    if let book = model.books.book {
                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(width:UIDevice.current.userInterfaceIdiom == .pad ? 320 : 230, height:UIDevice.current.userInterfaceIdiom == .pad ? 320 : 230).clipShape(RoundedRectangle(cornerRadius:18))
                            .overlay(alignment:.bottomTrailing) { AudiobookDownloadIndicator(downloads:model.books.downloads,book:book).padding(10) }
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
                            Button { model.books.toggle() } label: { ZStack { Circle().fill(.white).frame(width:84,height:84); if model.books.preparing { ProgressView().tint(.black).scaleEffect(1.4) } else { Image(systemName:model.books.showsPauseControl ? "pause.fill" : "play.fill").font(.largeTitle).foregroundStyle(.black) } } }.accessibilityLabel(model.books.playbackControlLabel).accessibilityIdentifier("audiobookPlayPause")
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
                            Button("Keep this device’s position") { keepLocal = true }
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
            .confirmationDialog("Replace the saved listening position with this device’s position?", isPresented:$keepLocal, titleVisibility:.visible) {
                Button("Keep this device’s position") { Task { await model.books.keepLocalPosition() } }
            }
    }
    @ViewBuilder private func panel(_ book: Audiobook) -> some View {
        switch tab {
        case "Chapters":
            ForEach(book.chapters ?? []) { chapter in
                AudiobookChapterRow(chapter:chapter,current:model.books.chapter?.id == chapter.id,playing:model.books.playing,waiting:model.books.showsPauseControl && !model.books.playing,identifierPrefix:"audiobookPlayerChapter") { model.books.seek(chapter.start,resumePlayback:true) }
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

private struct AudiobookChapterRow: View {
    let chapter: AudiobookChapter
    let current: Bool
    let playing: Bool
    var waiting = false
    var identifierPrefix = "audiobookChapter"
    let action: () -> Void
    var body: some View {
        Button(action:action) {
            HStack(spacing:12) {
                Image(systemName:current ? (playing ? "waveform" : waiting ? "ellipsis.circle" : "pause.circle.fill") : "play.circle")
                    .frame(width:24).accessibilityHidden(true)
                VStack(alignment:.leading,spacing:3) {
                    Text(chapter.title).multilineTextAlignment(.leading)
                    if current { Text(playing ? "Playing" : waiting ? "Starting…" : "Current chapter · Paused").font(.caption) }
                }
                Spacer()
                Text(AudiobookTimeline.time(chapter.end-chapter.start)).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal,10).frame(maxWidth:.infinity,minHeight:52).contentShape(Rectangle())
                .background(current ? Color.green.opacity(0.12) : Color.clear,in:RoundedRectangle(cornerRadius:10))
        }.buttonStyle(.plain).foregroundStyle(current ? Color.green : Color.primary)
            .accessibilityLabel("Play chapter: " + chapter.title)
            .accessibilityValue(current ? (playing ? "Playing" : waiting ? "Starting" : "Paused") : "")
            .accessibilityAddTraits(current ? .isSelected : [])
            .accessibilityIdentifier("\(identifierPrefix)-\(chapter.start)")
    }
}

struct AudiobookSettings: View {
    @Bindable var books: AudiobookStore
    var body: some View {
        Form {
            Toggle("Show Audiobooks", isOn:Binding(get:{ books.preferences.visible }, set:{ update(visible:$0) }))
            Section {
                Toggle("Download when listening",isOn:$books.automaticDownloads)
                Text("Start listening immediately and save the book on this device in the background. Downloads may use cellular data. Paused downloads stay paused.").font(.footnote).foregroundStyle(.secondary)
            }
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


private struct AudiobookDownloadStatus: View {
    let downloads: AudiobookDownloads
    let book: Audiobook
    var body: some View {
        Group {
            if downloads.complete(book.id) { Label("Downloaded", systemImage:"checkmark.circle.fill").foregroundStyle(.green) }
            else if downloads.working.contains(book.id) { Text("Downloading · \(Int(downloads.fraction(book.id)*100))%") }
            else if downloads.records[book.id] != nil { Label("Download paused", systemImage:"pause.circle") }
        }.font(.caption)
    }
}
private struct AudiobookDownloadIndicator: View {
    let downloads: AudiobookDownloads
    let book: Audiobook
    var body: some View {
        ZStack {
            Circle().fill(.ultraThinMaterial)
            if downloads.working.contains(book.id) {
                Circle().stroke(Color.primary.opacity(0.2),lineWidth:3).padding(3)
                Circle().trim(from:0,to:CGFloat(downloads.fraction(book.id))).stroke(Color.green,style:StrokeStyle(lineWidth:3,lineCap:.round)).rotationEffect(.degrees(-90)).padding(3)
                Image(systemName:"arrow.down").font(.caption.bold())
            } else {
                Image(systemName:downloads.complete(book.id) ? "checkmark.circle.fill" : downloads.records[book.id] == nil ? "arrow.down.circle" : "pause.circle")
                    .font(.title3).foregroundStyle(downloads.complete(book.id) ? Color.green : Color.primary)
            }
        }.frame(width:36,height:36).accessibilityHidden(true)
    }
}
private struct AudiobookShelfRow: View {
    let model: AppModel
    let book: Audiobook
    var selectedSeries: String = ""
    var body: some View {
        NavigationLink { AudiobookDetail(model:model, initial:book) } label: {
            HStack(alignment:.center, spacing:16) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(width:76,height:76).clipShape(RoundedRectangle(cornerRadius:10))
                VStack(alignment:.leading, spacing:5) {
                    Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                    Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    if let series = book.seriesEntries?.first(where:{ $0.id == selectedSeries }) ?? book.seriesEntries?.first { Text(series.label).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                    Text(book.progress?.isFinished == true ? "Finished" : AudiobookTimeline.time(book.remaining) + " left").font(.caption).foregroundStyle(.secondary)
                    AudiobookDownloadStatus(downloads:model.books.downloads, book:book)
                }.frame(maxWidth:.infinity, alignment:.leading)
                Image(systemName:"chevron.right").font(.caption).foregroundStyle(.secondary)
            }.padding(.vertical,4).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("audiobook-" + book.id)
    }
}
private struct AudiobookGroupBrowser: View {
    let model: AppModel
    let kind: String
    let library: String
    let query: String
    var author: String = ""
    @State private var items: [AudiobookPerson] = []
    @State private var next: Int?
    @State private var loaded = false
    @State private var loading = false
    @State private var message: String?
    @State private var identity = UUID()
    private var key: String { kind + "\u{0}" + library + "\u{0}" + query + "\u{0}" + author }
    var body: some View {
        LazyVStack(spacing:0) {
            ForEach(items) { item in
                NavigationLink {
                    AudiobookScopedShelf(model:model, name:item.name, library:library, author:kind == "authors" ? item.id : "", series:kind == "series" ? item.id : "")
                } label: {
                    HStack(spacing:14) { Image(systemName:kind == "authors" ? "person.crop.square" : "books.vertical").font(.title2).frame(width:44,height:52)
                        Text(item.name).font(.headline).frame(maxWidth:.infinity,alignment:.leading); Image(systemName:"chevron.right").font(.caption)
                    }.padding(.vertical,9).contentShape(Rectangle())
                }.buttonStyle(.plain)
                Divider()
            }
            if loading { ProgressView().padding() }
            if loaded && items.isEmpty && message == nil { ContentUnavailableView.search(text:query) }
            if let message { Text(message).foregroundStyle(.secondary).padding(); Button("Try again") { Task { await load(more:false) } } }
            if next != nil { Button("More " + kind) { Task { await load(more:true) } }.padding().disabled(loading) }
        }.task(id:key) { await load(more:false) }
    }
    @MainActor private func load(more: Bool) async {
        let ticket = UUID(); identity = ticket; loading = true; message = nil
        defer { if identity == ticket { loading = false } }
        do {
            if !query.isEmpty { try await Task.sleep(for:.milliseconds(250)) }
            let result = try await model.api.audiobookGroups(kind, library:library, page:more ? next ?? 0 : 0, query:query, author:author)
            guard identity == ticket, !Task.isCancelled else { return }
            items = more ? items + result.items : result.items; next = result.nextPage; loaded = true
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            guard identity == ticket else { return }; message = "This view couldn’t be updated. Try again."
        }
    }
}
private struct AudiobookScopedShelf: View {
    let model: AppModel
    let name: String
    @State private var shelf: AudiobookShelfStore
    init(model: AppModel, name: String, library: String, author: String = "", series: String = "") {
        self.model = model; self.name = name
        let store = AudiobookShelfStore(api:model.api); store.library = library; store.author = author; store.series = series
        if !series.isEmpty { store.sort = "sequence" }
        _shelf = State(initialValue:store)
    }
    var body: some View {
        ScrollView {
            LazyVStack(alignment:.leading, spacing:18) {
                if !shelf.author.isEmpty {
                    Text("Series").font(.title2.bold())
                    AudiobookGroupBrowser(model:model, kind:"series", library:shelf.library, query:"", author:shelf.author)
                    Text("All books").font(.title2.bold())
                }
                ForEach(shelf.items) { book in AudiobookShelfRow(model:model, book:book, selectedSeries:shelf.series) }
                if shelf.loading { ProgressView().frame(maxWidth:.infinity) }
                if let message = shelf.message { Text(message).foregroundStyle(.secondary); Button("Try again") { Task { await shelf.retry() } } }
                if shelf.hasLoaded && shelf.items.isEmpty && !shelf.loading && shelf.message == nil { ContentUnavailableView("No books yet", systemImage:"books.vertical", description:Text("Available books in this library will appear here.")) }
                if shelf.next != nil { Button("More books") { Task { await shelf.load(more:true) } }.disabled(shelf.loading) }
            }.padding(24)
        }.navigationTitle(name).task { await shelf.load() }.refreshable { await shelf.load() }
    }
}
