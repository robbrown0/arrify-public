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
        return shelf.hasLoaded && !shelf.loading && displayed.isEmpty && shelf.next == nil && shelf.message == nil
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
                                VStack(alignment:.leading, spacing:7) {
                                    ZStack(alignment:.bottomTrailing) {
                                        NavigationLink { AudiobookDetail(model:model,initial:book) } label: {
                                        RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").aspectRatio(1,contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:12))
                                        }.buttonStyle(.plain).accessibilityLabel("Book details for " + book.title).accessibilityIdentifier("continue-audiobook-" + book.id)
                                        Button { model.books.play(book) } label: { Image(systemName:"play.fill").foregroundStyle(.black).frame(width:44,height:44).background(.green,in:Circle()) }
                                            .buttonStyle(.plain).padding(8).accessibilityLabel("Resume " + book.title).accessibilityIdentifier("resume-audiobook-" + book.id)
                                    }
                                    HStack(alignment:.top,spacing:4) {
                                        NavigationLink { AudiobookDetail(model:model,initial:book) } label: { Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2) }.buttonStyle(.plain)
                                        Spacer(minLength:0)
                                        AudiobookActionsMenu(model:model,initial:book,identifier:"continueBookActions-" + book.id)
                                    }
                                        Text("Resume · " + AudiobookTimeline.time(book.remaining) + " left").font(.caption).foregroundStyle(.secondary)
                                }.frame(width:180,alignment:.leading)
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
        }.accessibilityIdentifier("audiobookShelfScroll").background(Color.black).navigationTitle("Audiobooks")
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
        let title = ["downloads":"Take a story with you","started":"No books in progress","unstarted":"No unread books","finished":"No finished books"][shelf.filter] ?? "Your bookshelf"
        let detail = ["downloads":"Download a book to listen without a connection.","started":"Books you start will appear here until you finish or mark them as unread.","finished":"Finished books will appear here.","unstarted":"Books you haven’t started will appear here."][shelf.filter] ?? "Your books will appear here when available."
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
            .accessibilityIdentifier("audiobookFilter-" + value)
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
        return shelf.items.map(model.books.presentation).filter { $0.readingState.matches(shelf.filter) }
    }
    private func filterTitle(_ value: String) -> String { ["all":"All books", "unstarted":"Not started", "started":"In progress", "finished":"Finished", "downloads":"Downloaded"][value] ?? value }
}

private struct AudiobookShelfCard: View {
    let model: AppModel
    let book: Audiobook
    var body: some View {
        let book = model.books.presentation(self.book)
        VStack(alignment:.leading,spacing:4) {
        NavigationLink { AudiobookDetail(model:model, initial:book) } label: {
            VStack(alignment:.leading, spacing:7) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed")
                    .aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:12))
                Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
                if let series = book.seriesEntries?.first { Text(series.label).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                Text(book.shelfSummary).font(.caption).foregroundStyle(.secondary)
                AudiobookDownloadStatus(downloads:model.books.downloads, book:book)
            }.frame(maxWidth:.infinity, alignment:.leading)
        }.buttonStyle(.plain).accessibilityIdentifier("audiobook-" + book.id)
            HStack { Spacer(); AudiobookActionsMenu(model:model,initial:book,identifier:"gridBookActions-" + book.id) }
        }
    }
}

struct AudiobookDetail: View {
    let model: AppModel
    let initial: Audiobook
    var onListen: (() -> Void)? = nil
    @State private var detail: Audiobook?
    @State private var error: String?
    @State private var detailGeneration = UUID()
    private var book: Audiobook { model.books.presentation(detail ?? initial) }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading, spacing:20) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(maxWidth:300).aspectRatio(1, contentMode:.fit).clipShape(RoundedRectangle(cornerRadius:16))
                    .frame(maxWidth:.infinity)
                HStack(alignment:.center,spacing:16) {
                    Text(book.title).font(.largeTitle.bold()).frame(maxWidth:.infinity,alignment:.leading)
                    AudiobookDownloadControl(books:model.books,book:book,identifier:"audiobookDetailDownload").id(book.id)
                }.padding(.trailing,8)
                if let authors = book.authors, !authors.isEmpty {
                    ForEach(authors) { author in NavigationLink(author.name) { AudiobookScopedShelf(model:model, name:author.name, library:book.libraryId, author:author.id) }.font(.title3) }
                } else { Text(book.author).font(.title3).foregroundStyle(.secondary) }
                ForEach(book.seriesEntries ?? []) { series in
                    NavigationLink(series.label) { AudiobookScopedShelf(model:model, name:series.name, library:book.libraryId, series:series.id) }
                }
                if let release = book.publishedDate ?? book.publishedYear { Text("Published " + release).font(.subheadline).foregroundStyle(.secondary) }
                if !book.narrator.isEmpty { Text("Narrated by " + book.narrator).foregroundStyle(.secondary) }
                Text(AudiobookTimeline.time(book.duration)).foregroundStyle(.secondary)
                Button { model.books.play(book); onListen?() } label: { Label(book.listenTitle, systemImage:"play.fill").frame(maxWidth:.infinity, minHeight:44) }.buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("listenAudiobook")
                    .disabled(model.books.isUpdatingReadingState(book.id))
                downloadControls
                if model.books.hasPendingReadingState(book.id) { Label("Saved on this device · Waiting to sync",systemImage:"icloud").font(.caption).foregroundStyle(.secondary) }
                if let error { Text(error).foregroundStyle(.secondary) }
                Text(plainDescription).textSelection(.enabled)
                if let chapters = book.chapters {
                    Text("Chapters").font(.title2.bold())
                    ForEach(chapters) { chapter in
                        AudiobookChapterRow(chapter:chapter, current:model.books.book?.id == book.id && model.books.chapter?.id == chapter.id, playing:model.books.playing, waiting:model.books.showsPauseControl && !model.books.playing) {
                            if model.books.conflict, model.books.book?.id == book.id { model.books.play(book,useRemote:true,startingAt:chapter.start); onListen?() }
                            else if model.books.active, model.books.book?.id == book.id { model.books.seek(chapter.start,resumePlayback:true); onListen?() }
                            else { model.books.play(book,startingAt:chapter.start) }
                        }
                    }
                }
            }.padding(24)
        }.accessibilityIdentifier("audiobookDetailScroll").background(Color.black).navigationTitle("Audiobook").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement:.topBarTrailing) { AudiobookActionsMenu(model:model,initial:book,showsDetails:false,onListen:onListen,identifier:"detailBookActions") } }
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
    @ViewBuilder private var downloadControls: some View {
        let downloads = model.books.downloads
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
    @State private var showingDetails = false
    @State private var reviewingPlace = false
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing:20) {
                    if let book = model.books.book {
                        Button { showingDetails = true } label: {
                            RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(width:UIDevice.current.userInterfaceIdiom == .pad ? 320 : 230, height:UIDevice.current.userInterfaceIdiom == .pad ? 320 : 230).clipShape(RoundedRectangle(cornerRadius:18))
                        }.buttonStyle(.plain).accessibilityLabel("Book details for " + book.title).accessibilityIdentifier("audiobookPlayerCoverDetails")
                        VStack(alignment:.leading, spacing:8) {
                            Text(book.author).foregroundStyle(.secondary)
                            HStack(alignment:.center,spacing:16) {
                                Button { showingDetails = true } label: {
                                    Text(book.title).font(.title.bold()).foregroundStyle(.primary).frame(maxWidth:.infinity,minHeight:44,alignment:.leading)
                                }.buttonStyle(.plain).accessibilityHint("Open book details").accessibilityIdentifier("audiobookPlayerTitle")
                                AudiobookDownloadControl(books:model.books,book:book,identifier:"audiobookPlayerDownload").id(book.id)
                            }.padding(.trailing,8)
                            if let chapter = model.books.chapter { Text(chapter.title).foregroundStyle(.secondary) }
                        }.frame(maxWidth:.infinity, alignment:.leading)
                        Slider(value:Binding(get:{ seeking ?? model.books.position }, set:{ seeking = $0 }), in:0...max(1,book.duration)) { editing in
                            if !editing, let target = seeking { model.books.seek(target); seeking = nil }
                        }.accessibilityLabel("Book listening position").accessibilityValue(AudiobookTimeline.time(model.books.position)).accessibilityIdentifier("audiobookPosition")
                            .disabled(model.books.conflict)
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
                        if model.books.conflict {
                            HStack(spacing:12) {
                                Image(systemName:model.books.recovery == .edition ? "arrow.triangle.2.circlepath" : "bookmark.circle").font(.title3).foregroundStyle(.green)
                                VStack(alignment:.leading,spacing:3) {
                                    Text(model.books.recovery == .edition ? "Book update available" : "Choose where to resume").font(.subheadline.weight(.semibold))
                                    Text("Your saved place is safe.").font(.caption).foregroundStyle(.secondary)
                                }.frame(maxWidth:.infinity,alignment:.leading)
                                Button("Review") { reviewingPlace = true }.buttonStyle(.bordered).tint(.green).frame(minHeight:44).accessibilityIdentifier("reviewAudiobookPlace")
                            }.padding(14).background(.white.opacity(0.06),in:RoundedRectangle(cornerRadius:14))
                        } else if let message = model.books.message {
                            HStack(alignment:.top,spacing:10) { Image(systemName:"info.circle").foregroundStyle(.green); Text(message).font(.footnote).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading) }
                                .padding(12).background(.white.opacity(0.06),in:RoundedRectangle(cornerRadius:12)).accessibilityIdentifier("audiobookMessage")
                        }
                        Picker("Book details", selection:$tab) { Text("Chapters").tag("Chapters"); Text("Up Next").tag("Up Next"); Text("Bookmarks").tag("Bookmarks") }.pickerStyle(.segmented)
                        panel(book)
                    } else { ProgressView(model.books.preparing ? "Opening your audiobook…" : "Choose a book to begin") }
                }.padding(24).frame(maxWidth:760).frame(maxWidth:.infinity)
            }.accessibilityIdentifier("audiobookPlayerScroll").background(LinearGradient(colors:[Color.green.opacity(0.12),.black], startPoint:.top, endPoint:.bottom))
                .navigationTitle("Audiobook").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement:.topBarLeading) { Button { model.books.presented = false } label: { Image(systemName:"chevron.down").frame(width:44, height:44) }.accessibilityLabel("Close audiobook player") }
                    ToolbarItemGroup(placement:.topBarTrailing) {
                        Button { Task { await model.books.addBookmark() } } label: { Image(systemName:"bookmark").frame(width:44, height:44) }.accessibilityLabel("Save bookmark").disabled(model.books.conflict || model.books.preparing)
                        if let book = model.books.book { AudiobookActionsMenu(model:model,initial:book,onDetails:{ showingDetails = true },identifier:"playerBookActions") }
                    }
                }
                .navigationDestination(isPresented:$showingDetails) {
                    if let book = model.books.book { AudiobookDetail(model:model,initial:book,onListen:{ showingDetails = false }) }
                }
        }.onChange(of:tab) { if tab == "Up Next" { Task { await model.books.refreshQueue() } } }
            .sheet(isPresented:$reviewingPlace) { AudiobookPlaceReview(books:model.books) }
    }
    @ViewBuilder private func panel(_ book: Audiobook) -> some View {
        switch tab {
        case "Chapters":
            ForEach(book.chapters ?? []) { chapter in
                AudiobookChapterRow(chapter:chapter,current:!model.books.conflict && model.books.chapter?.id == chapter.id,playing:model.books.playing,waiting:model.books.showsPauseControl && !model.books.playing,identifierPrefix:"audiobookPlayerChapter") {
                    if model.books.conflict { model.books.play(book,useRemote:true,startingAt:chapter.start) }
                    else { model.books.seek(chapter.start,resumePlayback:true) }
                }
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
            if downloads.needsUpdate(book) { Label("Update available",systemImage:"arrow.triangle.2.circlepath").foregroundStyle(.secondary) }
            else if downloads.complete(book) { Label("Downloaded", systemImage:"checkmark.circle.fill").foregroundStyle(.green) }
            else if downloads.working.contains(book.id) { Text("Downloading · \(Int(downloads.fraction(book.id)*100))%") }
            else if downloads.records[book.id] != nil { Label("Download paused", systemImage:"pause.circle") }
        }.font(.caption)
    }
}
private struct AudiobookDownloadControl: View {
    let books: AudiobookStore
    let book: Audiobook
    let identifier: String
    @State private var pendingAction: String?
    @State private var actionError: String?
    private var downloads: AudiobookDownloads { books.downloads }
    var body: some View {
        let update = downloads.needsUpdate(book)
        let complete = downloads.complete(book), working = downloads.working.contains(book.id) && !update
        let progress = update ? 0 : downloads.fraction(book.id), paused = downloads.records[book.id] != nil && !working && !complete && !update
        let action = update ? "Update download" : working ? "Pause download" : paused ? "Resume download" : "Download"
        let status = pendingAction ?? (update ? "A newer version is available" : working ? "Downloading · \(Int(progress*100))%" : paused ? "Paused · \(Int(progress*100))%" : "Listen offline")
        Group {
            if complete {
                circle(complete:true,paused:false,progress:1).frame(width:44,height:44)
                    .accessibilityElement(children:.ignore).accessibilityLabel("Downloaded").accessibilityValue("Available offline")
            } else {
                Button {
                    let shouldPause = downloads.working.contains(book.id) && !downloads.needsUpdate(book)
                    pendingAction = shouldPause ? "Pausing download…" : "Preparing download…"
                    Task {
                        defer { pendingAction = nil }
                        if shouldPause { await downloads.pause(book.id) }
                        else {
                            do { try books.preservePlaceBeforeDownload(book); await downloads.download(book.id) }
                            catch { actionError = AudiobookPlaybackIssue.classify(error).message }
                        }
                    }
                } label: {
                    circle(complete:false,paused:paused,progress:progress).frame(width:44,height:44).contentShape(Circle())
                }.buttonStyle(AudiobookDownloadButtonStyle()).disabled(pendingAction != nil)
                    .accessibilityLabel(action).accessibilityValue(status)
            }
        }.accessibilityIdentifier(identifier)
            .alert("Couldn’t update the download",isPresented:Binding(get:{ actionError != nil },set:{ if !$0 { actionError = nil } })) { Button("OK",role:.cancel) {} } message: { Text(actionError ?? "") }
    }
    private func circle(complete: Bool, paused: Bool, progress: Double) -> some View {
        ZStack {
            Circle().fill(complete ? Color.green : Color.primary.opacity(0.12))
            if !complete {
                AudiobookDownloadPie(progress:progress).fill(Color.green.opacity(0.35))
                    .animation(.linear(duration:0.2),value:progress)
            }
            if complete {
                Image(systemName:"checkmark").font(.subheadline.bold()).foregroundStyle(.black)
            } else if pendingAction != nil {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName:paused ? "pause.fill" : "arrow.down").font(.subheadline.bold()).foregroundStyle(paused ? Color.yellow : Color.green)
            }
        }.frame(width:36,height:36).foregroundStyle(.green).accessibilityElement(children:.ignore)
    }
}
private struct AudiobookDownloadButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? 0.6 : 1)
    }
}
private struct AudiobookDownloadPie: Shape {
    var progress: Double
    var animatableData: Double { get { progress } set { progress = newValue } }
    func path(in rect: CGRect) -> Path {
        let center = CGPoint(x:rect.midX,y:rect.midY)
        var path = Path(); path.move(to:center)
        path.addArc(center:center,radius:min(rect.width,rect.height)/2,startAngle:.degrees(-90),endAngle:.degrees(-90+360*min(1,max(0,progress))),clockwise:false)
        path.closeSubpath(); return path
    }
}

private extension Audiobook {
    var listenTitle: String { readingState == .started ? "Continue listening" : readingState == .finished ? "Listen again" : "Listen now" }
    var shelfSummary: String { readingState == .finished ? "Finished" : AudiobookTimeline.time(readingState == .started ? remaining : duration) + (readingState == .started ? " left" : "") }
}

/// Home shares book navigation and commands with the audiobook library.
struct AudiobookContinueRow: View {
    let model: AppModel
    let initial: Audiobook
    private var book: Audiobook { model.books.presentation(initial) }
    var body: some View {
        HStack(spacing:12) {
            NavigationLink { AudiobookDetail(model:model,initial:book) } label: {
                HStack(spacing:16) {
                    RemoteCover(path:book.cover,title:book.title,artwork:model.artwork,placeholderSymbol:"book.closed")
                        .frame(width:72,height:72).clipShape(RoundedRectangle(cornerRadius:10))
                    VStack(alignment:.leading,spacing:5) {
                        Text(book.title).font(.headline).foregroundStyle(.primary)
                        Text(book.author).font(.subheadline).foregroundStyle(.secondary)
                        Text(book.shelfSummary).font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth:.infinity,alignment:.leading)
                }.contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityLabel("Book details for " + book.title).accessibilityIdentifier("homeContinueBook-" + book.id)
            Button { model.books.play(book) } label: { Image(systemName:"play.circle.fill").font(.largeTitle).frame(width:44,height:44) }
                .buttonStyle(.plain).tint(.green).accessibilityLabel("Resume " + book.title).accessibilityIdentifier("homeResumeBook-" + book.id)
            AudiobookActionsMenu(model:model,initial:book,identifier:"homeContinueBookActions-" + book.id)
        }.frame(maxWidth:.infinity,minHeight:80)
    }
}

/// Every book surface uses the same commands and the same live progress model.
/// Cover/title navigation is independent of the explicit listening action.
private struct AudiobookActionsMenu: View {
    let model: AppModel
    let initial: Audiobook
    var showsDetails = true
    var onListen: (() -> Void)? = nil
    var onDetails: (() -> Void)? = nil
    let identifier: String
    @State private var showingDetails = false
    @State private var showingPlaces = false
    @State private var error: String?
    private var book: Audiobook { model.books.presentation(initial) }
    var body: some View {
        Menu {
            if showsDetails { Button("Book details",systemImage:"info.circle") { if let onDetails { onDetails() } else { showingDetails = true } } }
            Button(book.listenTitle,systemImage:"play.fill") { model.books.play(book); onListen?() }
            Section {
                Button("Play next",systemImage:"text.line.first.and.arrowtriangle.forward") { perform { await model.books.queueBook(book.id,action:"next") } }
                Button("Add to Up Next",systemImage:"text.badge.plus") { perform { await model.books.queueBook(book.id,action:"last") } }
            }
            Section {
                if book.readingState != .finished {
                    Button("Mark as finished",systemImage:"checkmark.circle") { perform { _ = try await model.books.markFinished(book) } }.accessibilityIdentifier("markAudiobookFinished")
                }
                Button("Mark as unread",systemImage:"arrow.counterclockwise") { perform { _ = try await model.books.markUnread(book) } }
                    .accessibilityIdentifier("markAudiobookUnread").accessibilityHint("Reset the saved position. Keep downloads and bookmarks.")
                if book.readingState != .unstarted {
                    Button("Play from beginning",systemImage:"backward.end") { perform { try model.books.restart(book); onListen?() } }
                }
                if !model.books.savedPlaces(for:book.id).isEmpty { Button("Saved places",systemImage:"clock.arrow.circlepath") { showingPlaces = true } }
            }
            Section {
                if model.books.downloads.needsUpdate(book) {
                    Button("Update download",systemImage:"arrow.down.circle") { perform { try model.books.preservePlaceBeforeDownload(book); await model.books.downloads.download(book.id) } }
                } else if model.books.downloads.working.contains(book.id) {
                    Button("Pause download",systemImage:"pause.circle") { perform { await model.books.downloads.pause(book.id) } }
                } else if !model.books.downloads.complete(book) {
                    Button(model.books.downloads.records[book.id] == nil ? "Download" : "Resume download",systemImage:"arrow.down.circle") { perform { await model.books.downloads.download(book.id) } }
                }
                if model.books.downloads.records[book.id] != nil {
                    Button("Remove download",systemImage:"trash",role:.destructive) { perform { await model.books.removeDownload(book.id) } }
                }
            }
        } label: {
            Group { if model.books.isUpdatingReadingState(book.id) { ProgressView() } else { Image(systemName:"ellipsis") } }.frame(width:44,height:44).contentShape(Rectangle())
        }.tint(.primary).accessibilityLabel("More options for " + book.title).accessibilityIdentifier(identifier)
            .disabled(model.books.isUpdatingReadingState(book.id))
            .navigationDestination(isPresented:$showingDetails) { AudiobookDetail(model:model,initial:book) }
            .sheet(isPresented:$showingPlaces) { AudiobookSavedPlaces(books:model.books,book:book) }
            .alert("Couldn’t update the book",isPresented:Binding(get:{ error != nil },set:{ if !$0 { error = nil } })) { Button("OK",role:.cancel) {} } message: { Text(error ?? "") }
    }
    private func perform(_ action: @escaping @MainActor () async throws -> Void) {
        Task { do { try await action() } catch { self.error = AudiobookPlaybackIssue.classify(error).message } }
    }
}

private struct AudiobookSavedPlaces: View {
    let books: AudiobookStore
    let book: Audiobook
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(books.savedPlaces(for:book.id)) { place in
                        let matches = AudiobookEdition.matches(place.checkpoint,book)
                        Button { books.resumeSavedPlace(place,in:book); dismiss() } label: {
                            HStack(spacing:12) {
                                Image(systemName:matches ? "play.circle" : "bookmark").foregroundStyle(.green)
                                VStack(alignment:.leading,spacing:4) {
                                    Text(AudiobookTimeline.time(place.checkpoint.position)).font(.headline).monospacedDigit()
                                    Text(place.savedAt,format:.dateTime.month(.abbreviated).day().hour().minute()).font(.caption).foregroundStyle(.secondary)
                                    if !matches { Text("Earlier version · Choose a chapter in the updated book").font(.caption).foregroundStyle(.secondary) }
                                }
                            }.frame(minHeight:44)
                        }.buttonStyle(.plain).disabled(!matches)
                    }
                } footer: { Text("Recent places are kept on this device before you reset a book or choose another saved position.") }
            }.scrollContentBackground(.hidden).background(.black).navigationTitle("Saved places").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.confirmationAction) { Button("Done") { dismiss() } } }
        }.tint(.green).preferredColorScheme(.dark)
    }
}

private struct AudiobookPlaceReview: View {
    let books: AudiobookStore
    @Environment(\.dismiss) private var dismiss
    @State private var loading = false
    @State private var choosing = false
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(books.recovery == .edition ? "An updated book is available" : "Choose where to resume").font(.title2.bold())
                    Text(books.recovery == .edition ? "Choose the saved place to use with the current book. Your earlier place stays saved on this device." : "This book has two saved places. Choose the one you want to continue from.").foregroundStyle(.secondary)
                }
                Section("Saved places") {
                    if let position = books.deviceSavedPosition { place("On this device",position:position) }
                    if let remote = books.recoveryBook { place("Synced place",position:remote.progress?.currentTime ?? 0) }
                    else { Label(loading ? "Checking saved places…" : "Connect to compare saved places",systemImage:loading ? "icloud" : "wifi.slash").foregroundStyle(.secondary) }
                }
                if let remote = books.recoveryBook {
                    Section("Continue listening") {
                        Button { choosing = true; Task { if books.recovery == .edition { books.useDevicePlaceInUpdatedBook() } else { await books.keepLocalPosition() }; choosing = false; if !books.conflict { dismiss() } } } label: { Label("Use this device’s place",systemImage:"play.circle") }
                            .disabled(choosing || !books.canKeepDevicePlace).accessibilityIdentifier("useDeviceAudiobookPlace")
                        Button { books.play(remote,useRemote:true); dismiss() } label: { Label("Use synced place",systemImage:"play.circle") }.disabled(choosing).accessibilityIdentifier("useRemoteAudiobookPlace")
                    }
                    if books.downloads.needsUpdate(remote) {
                        Section { Button { Task { await books.updateCurrentDownload() } } label: { Label("Update download",systemImage:"arrow.down.circle") }.accessibilityIdentifier("updateAudiobookEdition") }
                    }
                } else { Button("Try again") { Task { await checkPlaces() } }.disabled(loading) }
                if choosing { ProgressView().frame(maxWidth:.infinity) }
            }.scrollContentBackground(.hidden).background(.black).navigationTitle("Saved place").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.cancellationAction) { Button("Done") { dismiss() } } }
                .task { await checkPlaces() }
        }.tint(.green).preferredColorScheme(.dark)
    }
    private func place(_ title: String,position:Double) -> some View {
        HStack { Text(title).foregroundStyle(.secondary); Spacer(); Text(AudiobookTimeline.time(position)).font(.headline).monospacedDigit() }.frame(minHeight:44)
    }
    private func checkPlaces() async { loading = true; await books.refreshRecovery(); loading = false }
}
private struct AudiobookShelfRow: View {
    let model: AppModel
    let book: Audiobook
    var selectedSeries: String = ""
    var body: some View {
        let book = model.books.presentation(self.book)
        HStack(spacing:8) {
        NavigationLink { AudiobookDetail(model:model, initial:book) } label: {
            HStack(alignment:.center, spacing:16) {
                RemoteCover(path:book.cover, title:book.title, artwork:model.artwork, placeholderSymbol:"book.closed").frame(width:76,height:76).clipShape(RoundedRectangle(cornerRadius:10))
                VStack(alignment:.leading, spacing:5) {
                    Text(book.title).font(.headline).foregroundStyle(.primary).lineLimit(2)
                    Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    if let series = book.seriesEntries?.first(where:{ $0.id == selectedSeries }) ?? book.seriesEntries?.first { Text(series.label).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                    Text(book.shelfSummary).font(.caption).foregroundStyle(.secondary)
                    AudiobookDownloadStatus(downloads:model.books.downloads, book:book)
                }.frame(maxWidth:.infinity, alignment:.leading)
            }.padding(.vertical,4).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("audiobook-" + book.id)
            AudiobookActionsMenu(model:model,initial:book,identifier:"shelfBookActions-" + book.id)
        }
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
