import SwiftUI
import ArrifyCore
import CryptoKit
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer

@MainActor @Observable final class AudiobookShelfStore {
    let api: APIClient
    var query = ""
    var filter = "all"
    var library = ""
    var sort = "title"
    var author = ""
    var series = ""
    private(set) var items: [Audiobook] = []
    private(set) var libraries: [AudiobookLibrary.Library] = []
    private(set) var next: Int?
    private(set) var loading = false
    private(set) var hasLoaded = false
    private(set) var needsRecovery = false
    private(set) var message: String?
    private var generation = UUID()
    private var account = UUID()
    private var scope = ""
    private var failedMore = false
    private var progressOverrides: [String:AudiobookProgress] = [:]
    private let wait: @MainActor (Double) async throws -> Void
    var requestKey: String { query + "\u{0}" + filter + "\u{0}" + library + "\u{0}" + sort + "\u{0}" + author + "\u{0}" + series }
    init(api: APIClient, wait: @escaping @MainActor (Double) async throws -> Void = ReadRecovery.wait) { self.api = api; self.wait = wait }
    func clear() {
        generation = UUID(); account = UUID(); query = ""; filter = "all"; library = ""; sort = "title"; author = ""; series = ""; scope = ""
        items = []; libraries = []; next = nil; loading = false; hasLoaded = false; needsRecovery = false; message = nil; failedMore = false
        progressOverrides = [:]
    }
    func load(more: Bool = false) async {
        guard filter != "downloads" else { return }
        let key = requestKey, owner = account
        // Geometry can report a boundary repeatedly before/after a task starts.
        // A paging request never joins a load and then drains the following page.
        if more { guard !loading, hasLoaded, next != nil, scope == key else { return } }
        if loading && scope == key {
            let pending = generation
            while loading && scope == key && generation == pending && !Task.isCancelled {
                do { try await Task.sleep(for:.milliseconds(20)) } catch { return }
            }
            guard !Task.isCancelled, owner == account, key == requestKey else { return }
            if !more && (hasLoaded || message != nil) { return }
        }
        if scope != key { items = []; next = nil; hasLoaded = false; scope = key; needsRecovery = false; failedMore = false }
        let token = UUID(); generation = token
        let query = query, filter = filter, library = library, sort = sort, author = author, series = series, page = more ? next ?? 0 : 0
        loading = true
        if !needsRecovery { message = nil }
        defer { if generation == token { loading = false } }
        do {
            let value = try await ReadRecovery.load(wait:wait, valid:{ self.generation == token && self.account == owner && self.requestKey == key }) {
                try await self.api.audiobookLibrary(query:query, library:library, page:page, filter:filter, sort:query.isEmpty ? sort : "title", author:author, series:series)
            }
            guard generation == token, owner == account, key == requestKey, !Task.isCancelled else { return }
            if let nextPage = value.nextPage, nextPage <= page { throw APIError.invalidResponse }
            let incoming = value.items.map(presentation).filter { $0.readingState.matches(filter) }
            if more {
                for book in incoming {
                    if let index = items.firstIndex(where:{ $0.id == book.id }) { items[index] = book }
                    else { items.append(book) }
                }
            } else { items = incoming }
            libraries = value.libraries; next = value.nextPage; hasLoaded = true; needsRecovery = false; failedMore = false; message = nil
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            guard generation == token, owner == account, key == requestKey else { return }
            failedMore = more
            needsRecovery = ReadRecovery.delay(error, attempt:0) != nil
            if case APIError.retryDeferred = error { needsRecovery = true }
            message = needsRecovery ? "Reconnecting to your bookshelf…" : "Your bookshelf couldn’t be updated."
            PlaybackDiagnostics.event("bookshelf-read-deferred reason=\(ReadRecovery.reason(error))")
        }
    }
    var failedNextPage: Bool { failedMore && message != nil }
    func loadNextPage(_ expectedPage: Int) async {
        // A departing footer can deliver its final geometry/loading callback
        // after a read failed. Only explicit Retry/reconnect may retry that page.
        guard next == expectedPage, !loading, !failedNextPage, scope == requestKey else { return }
        await load(more:true)
    }
    func retry() async { await load(more:failedMore) }
    func presentation(_ book: Audiobook) -> Audiobook {
        var value = book
        if let progress = progressOverrides[book.id], (progress.lastUpdate ?? 0) >= (book.progress?.lastUpdate ?? 0) { value.progress = progress }
        return value
    }
    func applyProgress(_ book: Audiobook, refreshMembership: Bool = true) {
        if let progress = book.progress { rememberProgress(book.id,progress:progress) }
        if !refreshMembership {
            items = items.map { $0.id == book.id ? presentation($0) : $0 }.filter { $0.readingState.matches(filter) }
            return
        }
        generation = UUID(); loading = false
        if !book.readingState.matches(filter) {
            items.removeAll { $0.id == book.id }
            next = nil
            Task { [weak self] in await self?.load() }
        } else if let index = items.firstIndex(where:{ $0.id == book.id }) { items[index] = book }
        else if ["unstarted","started","finished"].contains(filter) { next = nil; Task { [weak self] in await self?.load() } }
    }
    func recordProgress(_ id: String, progress: AudiobookProgress) {
        rememberProgress(id,progress:progress)
        items = items.map(presentation).filter { $0.readingState.matches(filter) }
    }
    private func rememberProgress(_ id: String, progress: AudiobookProgress) {
        guard (progress.lastUpdate ?? 0) >= (progressOverrides[id]?.lastUpdate ?? 0) else { return }
        progressOverrides[id] = progress
        if progressOverrides.count > 512,
           let oldest = progressOverrides.filter({ $0.key != id }).min(by:{ ($0.value.lastUpdate ?? 0) < ($1.value.lastUpdate ?? 0) })?.key {
            progressOverrides[oldest] = nil
        }
    }
    func reconnect() async {
        let owner = account, key = requestKey
        var attempt = 0
        while needsRecovery, owner == account, key == requestKey, !Task.isCancelled {
            do { try await wait(min(30,pow(2,Double(min(attempt + 1,5)))) * Double.random(in:0.8...1.2)) } catch { return }
            guard owner == account, key == requestKey, !Task.isCancelled else { return }
            if !loading { await retry() }; attempt += 1
        }
    }
}

enum AudiobookRecoveryKind: Equatable { case position, edition }

@MainActor @Observable final class AudiobookStore {
    private typealias Pending = AudiobookJournalEntry
    let api: APIClient
    let downloads: AudiobookDownloads
    let shelf: AudiobookShelfStore
    private weak var music: PlaybackCoordinator?
    private let audioSession: any PlaybackAudioSession
    private let localMedia: ((Audiobook, AudiobookPart) -> URL?)?
    private var player = AVPlayer()
    private var observer: Any?
    private var notifications: [NSObjectProtocol] = []
    private var generation = UUID()
    private var accountGeneration = UUID()
    private var operation: Task<Void, Never>?
    private var heartbeat: Task<Void, Never>?
    private var pending: [String: Pending] = [:]
    private var journal: URL?
    private var preferencesFile: URL?
    private var placesFile: URL?
    private(set) var savedPlaces: [AudiobookSavedPlace] = []
    private var preferencesPending = false
    private var savingPreferences = false
    private struct SavedPreferences: Codable { let value: AudiobookPreferences; let pending: Bool }
    private var part: AudiobookPart?
    private var currentURL: URL?
    private var streamingOverride: UUID?
    private var wantsPlayback = false
    private let syncGate = AudiobookSyncGate()
    private var syncIssues: [String: AudiobookPlaybackIssue] = [:]
    private var resolvingRemote: [String: UUID] = [:]
    private var retryDates: [String: Date] = [:]
    private var syncFailures: [String: Int] = [:]
    private var lastSaved = Date.distantPast
    private var interrupted = false
    private var resumeAfterInterruption = false
    private var sleepDeadline: Date?
    private var sleepChapter: Double?
    private var finishing = false
    private var lastAudioPosition = 0.0
    private var lastAudioProgress = Date()
    private var recoveryAttempts = 0
    private var systemArtwork: MPMediaItemArtwork?
    private var receiverArtwork: Data?
    private var canonicalLoaded = false
    private(set) var enabled = false
    var preferences = AudiobookPreferences()
    var automaticDownloads = UserDefaults.standard.object(forKey:"audiobookAutomaticDownloads") as? Bool ?? true {
        didSet { UserDefaults.standard.set(automaticDownloads,forKey:"audiobookAutomaticDownloads") }
    }
    private var automaticDownloadCandidate: String?
    private var unreadBooks: Set<String> = []
    private var loadingRecovery = false
    private(set) var continueListening: [Audiobook] = []
    private(set) var book: Audiobook?
    private(set) var manifest: AudiobookManifest?
    private(set) var position = 0.0
    private(set) var playing = false
    private(set) var preparing = false
    private(set) var active = false
    private(set) var offline = false
    private(set) var conflict = false
    private(set) var recovery: AudiobookRecoveryKind?
    private(set) var recoveryBook: Audiobook?
    private(set) var recoveryCheckpoint: AudiobookCheckpoint?
    private(set) var message: String?
    private(set) var queue: [AudiobookQueueEntry] = []
    private(set) var bookmarks: [AudiobookBookmark] = []
    var presented = false
    var sleepChoice = "off"
    var showsPauseControl: Bool { wantsPlayback || preparing }
    var playbackControlLabel: String { preparing ? "Cancel opening audiobook" : showsPauseControl ? "Pause audiobook" : "Play audiobook" }
    var chapter: AudiobookChapter? { AudiobookTimeline.chapter(book?.chapters ?? [], at:position) }
    var visible: Bool { enabled && preferences.visible }
    var resumeBooks: [Audiobook] {
        var values = continueListening.map(presentation)
        for record in downloads.records.values where record.finished != true {
            guard !canonicalLoaded || pending[record.book.id]?.dirty == true else { continue }
            let book = presentation(record.book)
            if let index = values.firstIndex(where:{ $0.id == book.id }) { values[index] = book }
            else { values.append(book) }
        }
        if let book, pending[book.id]?.dirty == true, !values.contains(where:{ $0.id == book.id }) { values.insert(presentation(book),at:0) }
        return values.filter { $0.readingState == .started }
    }
    func presentation(_ value: Audiobook) -> Audiobook {
        var result = shelf.presentation(value)
        if let entry = pending[value.id], entry.dirty {
            result.progress = AudiobookProgress(currentTime:entry.checkpoint.position,lastUpdate:entry.checkpoint.baseRemoteStamp,isFinished:entry.checkpoint.finished)
        }
        return result
    }
    func isUpdatingReadingState(_ id: String) -> Bool { unreadBooks.contains(id) }
    func hasPendingReadingState(_ id: String) -> Bool { pending[id]?.dirty == true && pending[id]?.checkpoint.isStatusChange == true }
    var deviceSavedPosition: Double? { book.flatMap { pending[$0.id]?.checkpoint.position ?? recoveryCheckpoint?.position ?? downloads.records[$0.id]?.book.progress?.currentTime ?? position } }
    var canKeepDevicePlace: Bool {
        guard let book, let fresh = recoveryBook else { return false }
        if recovery == .edition { return deviceSavedPosition != nil }
        guard let checkpoint = pending[book.id]?.checkpoint ?? recoveryCheckpoint else { return false }
        return AudiobookEdition.matches(checkpoint,fresh)
    }
    func savedPlaces(for id: String) -> [AudiobookSavedPlace] { savedPlaces.filter { $0.checkpoint.bookId == id } }
    init(api: APIClient, music: PlaybackCoordinator, audioSession: (any PlaybackAudioSession)? = nil, localMedia: ((Audiobook, AudiobookPart) -> URL?)? = nil, downloads: AudiobookDownloads? = nil) {
        self.api = api; self.music = music; self.downloads = downloads ?? AudiobookDownloads(api:api); shelf = AudiobookShelfStore(api:api)
        self.localMedia = localMedia
        self.audioSession = audioSession ?? SystemPlaybackAudioSession()
        installTimeObserver()
        notifications.append(NotificationCenter.default.addObserver(forName:.AVPlayerItemDidPlayToEndTime, object:nil, queue:.main) { @Sendable [weak self] note in
            let identity = (note.object as? AVPlayerItem).map(ObjectIdentifier.init)
            MainActor.assumeIsolated { if let self, identity == self.player.currentItem.map(ObjectIdentifier.init) { self.ended() } }
        })
        notifications.append(NotificationCenter.default.addObserver(forName:AVAudioSession.interruptionNotification, object:nil, queue:.main) { @Sendable [weak self] note in
            let kind = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let options = note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            MainActor.assumeIsolated {
                guard let self, self.active else { return }
                if kind == AVAudioSession.InterruptionType.began.rawValue {
                    self.resumeAfterInterruption = self.wantsPlayback; self.pause(); self.interrupted = true; self.audioSession.invalidate()
                } else {
                    self.interrupted = false
                    if self.resumeAfterInterruption && AVAudioSession.InterruptionOptions(rawValue:options).contains(.shouldResume) { self.resume() }
                    self.resumeAfterInterruption = false
                }
            }
        })
        notifications.append(NotificationCenter.default.addObserver(forName:AVAudioSession.routeChangeNotification, object:nil, queue:.main) { @Sendable [weak self] note in
            let reason = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            MainActor.assumeIsolated { if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue { self?.pause() } }
        })
        for name in [AVAudioSession.mediaServicesWereLostNotification, AVAudioSession.mediaServicesWereResetNotification] {
            notifications.append(NotificationCenter.default.addObserver(forName:name, object:nil, queue:.main) { @Sendable [weak self] _ in
                MainActor.assumeIsolated { guard let self, self.active else { return }; self.pause(); self.resetPlayer(); self.audioSession.invalidate(); self.message = "Audio was reset. Tap Play to continue from your saved place." }
            })
        }
    }
    private func installTimeObserver() {
        observer = player.addPeriodicTimeObserver(forInterval:CMTime(seconds:0.5, preferredTimescale:600), queue:.main) { @Sendable [weak self] time in
            let seconds = time.seconds
            MainActor.assumeIsolated { self?.receivePlaybackTime(seconds) }
        }
    }
    private func resetPlayer() {
        if let observer { player.removeTimeObserver(observer) }
        player = AVPlayer(); part = nil; currentURL = nil; installTimeObserver()
    }

    func restore() async {
        let ticket = accountGeneration
        guard let scope = try? await api.sessionScope() else { return }
        let key = SHA256.hash(data:Data(scope.utf8)).map { String(format:"%02x", $0) }.joined()
        let directory = URL.applicationSupportDirectory.appending(path:"AudiobookProgress")
        do {
            try FileManager.default.createDirectory(at:directory, withIntermediateDirectories:true,
                attributes:[.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication])
            var url = directory; var values = URLResourceValues(); values.isExcludedFromBackup = true; try url.setResourceValues(values)
            guard ticket == accountGeneration else { return }
            journal = directory.appending(path:key + ".json")
            preferencesFile = directory.appending(path:key + ".preferences")
            placesFile = directory.appending(path:key + ".places")
            if let placesFile, let data = try? Data(contentsOf:placesFile) { savedPlaces = (try? JSONDecoder().decode([AudiobookSavedPlace].self,from:data)) ?? [] }
            if let preferencesFile, let data = try? Data(contentsOf:preferencesFile), let saved = try? JSONDecoder().decode(SavedPreferences.self, from:data) { preferences = saved.value; preferencesPending = saved.pending }
            if let journal, let data = try? Data(contentsOf:journal) { pending = (try? JSONDecoder().decode([String:Pending].self, from:data)) ?? [:] }
            await downloads.configure()
            guard ticket == accountGeneration else { return }
            enabled = !downloads.records.isEmpty
            // Optional provider reads never gate music startup or an offline launch.
            await refresh(); startHeartbeat()
        } catch { message = "Your device could not open saved audiobook progress." }
    }
    private func persist() {
        guard let journal else { return }
        do { try JSONEncoder().encode(pending).write(to:journal, options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication]) }
        catch { message = "Your device could not save your listening position. Keep Arrify open until connected." }
    }
    func refresh() async {
        let ticket = accountGeneration
        do {
            let status = try await api.audiobookStatus()
            guard ticket == accountGeneration else { return }
            enabled = status.enabled
            if preferencesPending { await synchronizePreferences() }
            else { preferences = status.preferences }
            savePreferences()
            if enabled {
                let values = try await api.continueAudiobooks()
                guard ticket == accountGeneration else { return }
                continueListening = values.map(presentation).filter { $0.readingState == .started }; canonicalLoaded = true
            }
        } catch { /* Books are optional; keep already downloaded books available. */
            guard ticket == accountGeneration else { return }
            canonicalLoaded = false
            if !downloads.records.isEmpty { enabled = true }
        }
        await synchronize()
    }
    private func startHeartbeat() {
        guard heartbeat == nil else { return }
        heartbeat = Task { [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(for:.seconds(10)) } catch { return }
                guard let self else { return }
                if self.active { self.save(); self.recoverStall() }
                await self.synchronize()
                if self.active && self.offline { await self.connectLocalPlayback(token:self.generation, account:self.accountGeneration) }
            }
        }
    }
    func play(_ value: Audiobook, restart: Bool = false, useRemote: Bool = false, previousLease: String? = nil, startingAt: Double? = nil) {
        guard !unreadBooks.contains(value.id) else { return }
        let earlierPlace = book?.id == value.id ? recoveryCheckpoint : nil
        pause(); active = false; book = value
        automaticDownloadCandidate = automaticDownloads && downloads.records[value.id] == nil ? value.id : nil
        let saved = pending[value.id]?.dirty == true ? pending[value.id]?.checkpoint.position : value.progress?.currentTime
        let chapterStart = startingAt.map { min(value.duration,max(0,$0)) }
        position = min(value.duration, max(0, chapterStart ?? (restart ? 0 : saved ?? 0)))
        let ticket = UUID(); generation = ticket; preparing = true; message = nil; conflict = false; recovery = nil; recoveryBook = nil; recoveryCheckpoint = nil; presented = true
        resolvingRemote = useRemote ? [value.id:ticket] : [:]
        operation = Task { [weak self] in
            guard let self else { return }
            let account = self.accountGeneration
            defer { if self.resolvingRemote[value.id] == ticket { self.resolvingRemote[value.id] = nil } }
            var stage = "local-check"
            let started = Date()
            PlaybackDiagnostics.event("audiobook-start")
            do {
                if !useRemote, let record = self.downloads.records[value.id], self.downloads.complete(value.id),
                   value.sourceRevision == nil || AudiobookEdition.matches(value,record.manifest.book) {
                    if self.syncIssues[value.id]?.isConflict == true { throw self.syncIssues[value.id]! }
                    if self.syncIssues[value.id] == .authentication { throw AudiobookPlaybackIssue.authentication }
                    var local = record.manifest.book
                    if let progress = value.progress { local.progress = progress }
                    if self.pending[value.id]?.dirty != true {
                        stage = "resume-read"
                        do {
                            let api = self.api, id = value.id
                            let fresh = try await AudiobookReadDeadline.load { try await api.audiobook(id) }
                            guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                            if fresh.sourceRevision != nil, !AudiobookEdition.matches(fresh,local) {
                                throw AudiobookPlaybackIssue.editionChanged
                            }
                            local = fresh
                        } catch where RequestCancellation.matches(error) || Task.isCancelled { throw CancellationError() }
                        catch {
                            let issue = AudiobookPlaybackIssue.classify(error)
                            if issue == .authentication { throw issue }
                            if issue.isConflict { throw issue }
                            PlaybackDiagnostics.event("audiobook-resume-read-deferred reason=\(issue.category)")
                        }
                    }
                    guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                    // A download can be removed while the bounded resume read is
                    // suspended. Recheck before installing its captured manifest.
                    if self.downloads.complete(value.id) {
                        let cursor = chapterStart ?? AudiobookResume.position(remote:local.progress, local:record.lastPosition,
                            localStamp:record.remoteStamp, pending:self.pending[value.id], restart:restart, finished:record.finished == true)
                        self.install(local, manifest:nil, position:cursor, offline:true)
                        stage = "local-open"
                        try await self.open(at:cursor, token:ticket)
                        guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                        PlaybackDiagnostics.event("audiobook-start-ready source=local ms=\(Int(Date().timeIntervalSince(started)*1000))")
                        await self.connectLocalPlayback(token:ticket, account:account)
                        return
                    }
                }
                stage = "progress"
                if useRemote {
                    // Finish any old progress request before reading the server
                    // cursor, and suppress new writes from the history being replaced.
                    await self.syncGate.run {}
                    if let earlier = self.pending[value.id] { try self.rememberPlace(earlier.checkpoint,title:value.title) }
                    else if let earlierPlace { try self.rememberPlace(earlierPlace,title:value.title) }
                    else if let stored = self.downloads.records[value.id]?.book { try self.rememberPlace(AudiobookCheckpoint(book:stored,deviceId:self.music?.deviceId ?? ""),title:value.title) }
                } else { await self.synchronize(force:true, bookID:value.id) }
                guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                if self.pending[value.id]?.dirty == true && !useRemote {
                    if self.conflict { self.preparing = false; self.message = "Your place changed on another device. Choose which position to keep."; return }
                    // Connectivity failure is not a progress conflict. Let the
                    // downloaded fallback keep playing from the durable journal.
                    throw self.syncIssues[value.id] ?? AudiobookPlaybackIssue.progressPending
                }
                stage = "ownership"
                if !useRemote, let earlierPlace { try self.rememberPlace(earlierPlace,title:value.title) }
                let musicState = try await self.api.state()
                guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                stage = "begin"
                let result = try await self.begin(value.id, deviceId:self.music?.deviceId ?? "", revision:musicState.queueRevision, requestId:ticket.uuidString.lowercased(), restart:restart, useRemote:useRemote, previousLease:previousLease)
                guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                if chapterStart != nil, value.sourceRevision != nil, !AudiobookEdition.matches(value,result.book) { throw AudiobookPlaybackIssue.editionChanged }
                if useRemote {
                    self.pending.removeValue(forKey:value.id); self.syncIssues[value.id] = nil; self.retryDates[value.id] = nil
                    self.resolvingRemote[value.id] = nil; self.persist()
                }
                let startPosition = chapterStart ?? result.position
                self.install(result.book, manifest:result, position:startPosition, offline:false)
                stage = "stream-open"
                try await self.open(at:startPosition, token:ticket)
                PlaybackDiagnostics.event("audiobook-start-ready source=server ms=\(Int(Date().timeIntervalSince(started)*1000))")
                await self.refreshQueue()
            } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
                guard ticket == self.generation, account == self.accountGeneration else { return }
                let issue = AudiobookPlaybackIssue.classify(error)
                PlaybackDiagnostics.event("audiobook-start-failed stage=\(stage) reason=\(issue.category) ms=\(Int(Date().timeIntervalSince(started)*1000))")
                if issue.isConflict { self.reportConflict(issue); return }
                if issue == .authentication || issue == .storage { self.pause(); self.message = issue.message; return }
                if !useRemote, self.downloads.complete(value.id), let stored = self.downloads.records[value.id]?.manifest, value.sourceRevision == nil || AudiobookEdition.matches(value,stored.book) {
                    let downloaded = self.downloads.records[value.id]
                    let cursor = chapterStart ?? (restart || downloaded?.finished == true ? 0 : self.pending[value.id]?.checkpoint.position ?? downloaded?.lastPosition ?? stored.book.progress?.currentTime ?? stored.position)
                    self.install(stored.book, manifest:nil, position:cursor, offline:true)
                    do { try await self.open(at:cursor, token:ticket) }
                    catch where RequestCancellation.matches(error) || Task.isCancelled { return }
                    catch {
                        guard ticket == self.generation, account == self.accountGeneration else { return }
                        self.pause(); self.message = "This downloaded book could not be opened."
                    }
                } else { self.pause(); self.message = issue.message }
            }
        }
    }
    private func install(_ value: Audiobook, manifest: AudiobookManifest?, position: Double, offline: Bool) {
        music?.suspendForAudiobook(); self.book = value; self.manifest = manifest; self.position = position
        self.offline = offline; active = true; wantsPlayback = true; part = nil; finishing = false
        bookmarks = value.bookmarks ?? []; sleepChoice = "off"; sleepDeadline = nil; sleepChapter = nil
        systemArtwork = nil; receiverArtwork = nil; recoveryAttempts = 0; lastAudioPosition = position; lastAudioProgress = Date()
        Task { [weak self] in
            guard let self, let image = await self.music?.artworkStore.image(value.cover, pixels:1024), self.book?.id == value.id, self.active else { return }
            self.systemArtwork = NowPlayingArtwork.make(image:image)
            self.receiverArtwork = image.jpegData(compressionQuality:0.9); self.publish()
        }
        if pending[value.id]?.dirty != true {
            var checkpoint = AudiobookCheckpoint(book:value, deviceId:music?.deviceId ?? "")
            checkpoint.position = position
            if offline, let stamp = downloads.records[value.id]?.remoteStamp { checkpoint.baseRemoteStamp = max(checkpoint.baseRemoteStamp, stamp) }
            pending[value.id] = Pending(checkpoint:checkpoint, lease:manifest?.lease, playing:true, dirty:false)
        }
        persist(); systemControls()
        PlaybackDiagnostics.event(offline ? "audiobook-open-offline" : "audiobook-open-stream")
    }
    private func open(at target: Double, token: UUID) async throws {
        guard let book, let selected = AudiobookTimeline.part(book.parts ?? [], at:target) else { throw APIError.invalidEndpoint }
        if streamingOverride != token, part?.index == selected.index, let item = player.currentItem, item.status == .readyToPlay {
            try await seekAndPlay(selected:selected, target:target, token:token); return
        }
        var address: URL
        if streamingOverride != token, let local = localMedia?(book, selected) ?? downloads.file(book, part:selected) { address = local }
        else {
            guard let path = selected.url else { throw APIError.invalidEndpoint }
            address = try await api.audiobookMediaURL(path)
        }
        for attempt in 0..<3 {
            do {
                guard token == generation, !Task.isCancelled else { throw CancellationError() }
                let item = AVPlayerItem(url:address); item.audioTimePitchAlgorithm = .spectral; part = selected; currentURL = address
                player.replaceCurrentItem(with:item)
                publish()
                let deadline = Date().addingTimeInterval(15)
                while item.status == .unknown, Date() < deadline {
                    try await Task.sleep(for:.milliseconds(100))
                    guard token == generation, !Task.isCancelled else { throw CancellationError() }
                }
                guard item.status == .readyToPlay else { throw item.error ?? URLError(.cannotDecodeContentData) }
                try await seekAndPlay(selected:selected, target:target, token:token); return
            } catch where RequestCancellation.matches(error) || Task.isCancelled { throw CancellationError() }
            catch {
                PlaybackDiagnostics.event("audiobook-prepare-failed attempt=\(attempt+1) code=\((error as NSError).code)")
                if address.isFileURL || attempt == 2 { throw error }
                if let alternate = try? await api.alternateMediaURL(address) { address = alternate }
                try await Task.sleep(for:.milliseconds(Int(Double(400 * (1 << attempt))*Double.random(in:0.8...1.2))))
            }
        }
    }
    private func seekAndPlay(selected: AudiobookPart, target: Double, token: UUID) async throws {
        let local = max(0, min(selected.duration, target-selected.start))
        let seeked = await withCheckedContinuation { continuation in
            player.seek(to:CMTime(seconds:local, preferredTimescale:600), toleranceBefore:.zero, toleranceAfter:.zero) { done in continuation.resume(returning:done) }
        }
        guard seeked, token == generation, !Task.isCancelled else { throw CancellationError() }
        preparing = false; position = target; lastAudioPosition = target; lastAudioProgress = Date()
        if wantsPlayback, !interrupted {
            try await audioSession.activate()
            guard token == generation, wantsPlayback, !interrupted else { return }
            player.playImmediately(atRate:Float(preferences.speed)); playing = true
        }
        publish(); save()
        PlaybackDiagnostics.event("audiobook-audio-ready source=\(offline ? "local" : "server")")
        await synchronize(bookID:book?.id)
    }

    func pause() {
        pause(capturingPosition:true)
    }
    private func pause(capturingPosition: Bool) {
        let hadPlaybackIntent = wantsPlayback || playing || preparing
        if interrupted { resumeAfterInterruption = false }
        wantsPlayback = false; generation = UUID(); operation?.cancel(); operation = nil; loadingRecovery = false
        player.pause()
        if capturingPosition, hadPlaybackIntent, !preparing, !finishing, let current = currentAudioPosition() { position = current }
        playing = false; preparing = false
        if active { if hadPlaybackIntent { save() }; publish(); Task { await synchronize() } }
    }
    func resume() {
        guard active, let book, !unreadBooks.contains(book.id) else { return }; message = nil
        if conflict { presented = true; return }
        wantsPlayback = true; let ticket = UUID(); generation = ticket; preparing = true
        lastAudioProgress = Date()
        operation = Task { [weak self] in
            guard let self else { return }
            do {
                if !self.offline, let manifest = self.manifest {
                    do {
                        let state = try await self.api.audiobookState()
                        guard ticket == self.generation, !Task.isCancelled else { return }
                        if !state.active || state.lease != manifest.lease { self.play(book); return }
                    } catch {
                        if case APIError.rejected(let status, _, _) = error, [401,403,409].contains(status) { throw error }
                        guard self.downloads.complete(book.id) || self.player.currentItem?.status == .readyToPlay else { throw error }
                    }
                }
                try await self.open(at:self.position, token:ticket)
            } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
                guard ticket == self.generation else { return }
                self.pause(); self.message = "Your place is saved. Tap Play when the connection returns."
            }
        }
    }
    func seek(_ target: Double, resumePlayback: Bool = false) {
        guard let book, !unreadBooks.contains(book.id), !conflict else { return }; let wasPlaying = wantsPlayback || resumePlayback
        pause(); finishing = false; position = min(book.duration, max(0,target)); save(); wantsPlayback = wasPlaying
        let ticket = UUID(); generation = ticket; preparing = true
        operation = Task { [weak self] in
            do { try await self?.open(at:self?.position ?? 0, token:ticket) }
            catch { guard let self, ticket == self.generation else { return }; self.pause(); self.message = "Your place is saved. Tap Play to reconnect." }
        }
    }
    func toggle() { if wantsPlayback || preparing { pause() } else if !active, let book { play(book) } else { resume() } }
    private func currentAudioPosition() -> Double? {
        guard active, let book, let part, let item = player.currentItem, item.status == .readyToPlay else { return nil }
        let seconds = item.currentTime().seconds
        guard seconds.isFinite else { return nil }
        return min(book.duration,max(0,part.start+seconds))
    }
    func receivePlaybackTime(_ notificationTime: Double) {
        guard active, !preparing, !finishing else { return }
        guard wantsPlayback else {
            if let deadline = sleepDeadline, Date() >= deadline { setSleep("off"); message = "Sleep timer finished. Your place is saved."; publish() }
            return
        }
        // A queued notice can precede a seek, part change or Pause. Sample the
        // current item instead, and never let notices move a paused checkpoint.
        guard let current = currentAudioPosition() else { return }
        position = current; playing = player.timeControlStatus == .playing
        if position > lastAudioPosition + 0.1 {
            lastAudioProgress = Date(); lastAudioPosition = position
            if playing, wantsPlayback, automaticDownloads, let id = automaticDownloadCandidate, id == book?.id {
                automaticDownloadCandidate = nil
                Task { [weak self] in
                    guard let self, self.active, self.book?.id == id, self.automaticDownloads, self.downloads.records[id] == nil else { return }
                    await self.downloads.download(id)
                }
            }
        }
        if (sleepDeadline.map { Date() >= $0 } ?? false) || (sleepChapter.map { position >= $0 } ?? false) {
            if let end = sleepChapter, position >= end { position = end; pause(capturingPosition:false) }
            else { pause() }
            setSleep("off"); message = "Sleep timer finished. Your place is saved."
        }
        if Date().timeIntervalSince(lastSaved) >= 10 { save(); Task { await synchronize() } }
        publish()
    }
    private func recoverStall() {
        guard active, wantsPlayback, !preparing, !interrupted, !finishing, Date().timeIntervalSince(lastAudioProgress) > 8 else { return }
        guard recoveryAttempts < 3 else { pause(); message = "Playback is paused. Your place is saved. Tap Play to reconnect."; return }
        recoveryAttempts += 1; PlaybackDiagnostics.event("audiobook-recovery attempt=\(recoveryAttempts)"); resume()
    }
    private func save(finished: Bool = false) {
        guard active, let book, var value = pending[book.id] else { return }
        guard !unreadBooks.contains(book.id) else { return }
        guard value.needsCheckpoint(position:position, playing:playing, finished:finished || finishing) else { lastSaved = Date(); return }
        value.checkpoint.seq += 1; value.checkpoint.position = position; value.checkpoint.finished = finished || finishing
        value.playing = playing; value.dirty = true; pending[book.id] = value; lastSaved = Date(); persist()
        downloads.checkpoint(book.id, position:position, finished:value.checkpoint.finished)
        var displayed = book
        displayed.progress = AudiobookProgress(currentTime:position,lastUpdate:value.checkpoint.baseRemoteStamp,isFinished:value.checkpoint.finished)
        shelf.applyProgress(displayed,refreshMembership:false)
    }
    func synchronize(force: Bool = false, bookID: String? = nil) async {
        let account = accountGeneration
        let performed = await syncGate.run { [weak self] in await self?.synchronizePass(force:force, bookID:bookID, account:account) }
        guard account == accountGeneration, !Task.isCancelled else { return }
        // A caller may have joined a heartbeat snapshot that did not contain its
        // latest checkpoint. Drain once more, preserving sequence acknowledgements.
        if force, let bookID, pending[bookID]?.dirty == true, !performed || syncIssues[bookID] == nil {
            await syncGate.run { [weak self] in await self?.synchronizePass(force:true, bookID:bookID, account:account) }
        }
    }
    private func synchronizePass(force: Bool, bookID: String?, account: UUID) async {
        let ids = bookID.map { [$0] } ?? pending.keys.sorted { left, right in
            if left == bookID { return right != bookID }; if right == bookID { return false }; return left < right
        }
        for id in ids {
            guard !unreadBooks.contains(id) else { continue }
            guard account == accountGeneration, !Task.isCancelled else { return }
            guard resolvingRemote[id] == nil, let value = pending[id], value.dirty else { continue }
            if syncIssues[id]?.isConflict == true { continue }
            guard force || Date() >= (retryDates[id] ?? .distantPast) else { continue }
            do {
                let receipt: AudiobookReceipt
                if let lease = value.lease {
                    do { receipt = try await api.audiobookProgress(lease:lease, deviceId:value.checkpoint.deviceId, seq:value.checkpoint.seq, position:value.checkpoint.position, playing:value.playing, finished:value.checkpoint.finished) }
                    catch APIError.rejected(let status, _, _) where status == 409 {
                        guard account == accountGeneration, !Task.isCancelled else { return }
                        guard pending[id]?.checkpoint.sessionId == value.checkpoint.sessionId else { continue }
                        pending[id]?.lease = nil; persist()
                        receipt = try await api.offlineAudiobookProgress(value.checkpoint)
                    }
                } else { receipt = try await api.offlineAudiobookProgress(value.checkpoint) }
                guard account == accountGeneration, !Task.isCancelled else { return }
                guard pending[id]?.checkpoint.sessionId == value.checkpoint.sessionId else { continue }
                if receipt.saved && !receipt.pending {
                    let clean = pending[id]?.acknowledge(session:value.checkpoint.sessionId, sequence:value.checkpoint.seq, remoteStamp:receipt.remoteStamp) == true
                    if let latest = pending[id]?.checkpoint {
                        shelf.recordProgress(id,progress:AudiobookProgress(currentTime:latest.position,lastUpdate:latest.baseRemoteStamp,isFinished:latest.finished))
                    }
                    if clean && (!active || book?.id != id) { pending.removeValue(forKey:id) }
                    if let stamp = receipt.remoteStamp { downloads.checkpoint(id, remoteStamp:stamp) }
                    persist(); syncFailures[id] = nil; retryDates[id] = nil; syncIssues[id] = nil
                    PlaybackDiagnostics.event("audiobook-sync-ready newer-local=\(!clean)")
                } else { syncIssues[id] = .progressPending; retryDates[id] = Date().addingTimeInterval(10) }
                if resolvingRemote[id] == nil, receipt.active == false, let lease = value.lease, manifest?.lease == lease {
                    pause(); message = "Playback moved to another device. Your place is saved."
                }
            } catch where RequestCancellation.matches(error) || Task.isCancelled { return }
            catch {
                guard account == accountGeneration else { return }
                let issue = AudiobookPlaybackIssue.classify(error); syncIssues[id] = issue
                PlaybackDiagnostics.event("audiobook-sync-deferred reason=\(issue.category)")
                if issue.isConflict {
                    if book?.id == id && resolvingRemote[id] == nil { reportConflict(issue) }
                } else if issue == .authentication {
                    retryDates[id] = Date().addingTimeInterval(60)
                    if book?.id == id && resolvingRemote[id] == nil { pause(); message = issue.message }
                } else {
                    let failures = min((syncFailures[id] ?? 0) + 1, 6); syncFailures[id] = failures
                    retryDates[id] = Date().addingTimeInterval(min(60,pow(2,Double(failures)))*Double.random(in:0.8...1.2))
                }
                // Another book's pending progress must remain eligible.
            }
        }
    }
    func networkAvailable() async {
        retryDates = [:]; await synchronizePreferences(); await synchronize()
        if active && offline { await connectLocalPlayback(token:generation, account:accountGeneration) }
    }
    private func begin(_ id: String, deviceId: String, revision: Int, requestId: String, restart: Bool = false, useRemote: Bool = false, previousLease: String? = nil) async throws -> AudiobookManifest {
        // The backend checks an accepted request ID before validating the old
        // music revision. Reusing this identity cannot create a second session.
        do { return try await api.beginAudiobook(id, deviceId:deviceId, revision:revision, requestId:requestId, restart:restart, useRemote:useRemote, previousLease:previousLease) }
        catch {
            guard !RequestCancellation.matches(error), !Task.isCancelled else { throw CancellationError() }
            let issue = AudiobookPlaybackIssue.classify(error)
            guard issue == .offline || issue == .temporary else { throw error }
            try await Task.sleep(for:.milliseconds(400))
            try Task.checkCancellation()
            PlaybackDiagnostics.event("audiobook-begin-retry identity=reused")
            return try await api.beginAudiobook(id, deviceId:deviceId, revision:revision, requestId:requestId, restart:restart, useRemote:useRemote, previousLease:previousLease)
        }
    }
    private func connectLocalPlayback(token: UUID, account: UUID) async {
        guard token == generation, account == accountGeneration, active, offline, wantsPlayback, !interrupted, !conflict, let book else { return }
        guard pending[book.id]?.dirty != true else { return }
        do {
            let state = try await api.state()
            guard token == generation, account == accountGeneration, !Task.isCancelled else { return }
            let result = try await begin(book.id, deviceId:music?.deviceId ?? "", revision:state.queueRevision, requestId:token.uuidString.lowercased())
            guard token == generation, account == accountGeneration, !Task.isCancelled else { return }
            guard AudiobookEdition.matches(result.book,book) else {
                reportConflict(.editionChanged); return
            }
            // Refresh the fenced streaming URLs without reopening local audio.
            self.book = result.book
            // Keep the local player and cursor; acquiring ownership never seeks.
            manifest = result; offline = false; pending[book.id]?.lease = result.lease
            persist(); save()
            PlaybackDiagnostics.event("audiobook-local-session-connected")
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            guard token == generation, account == accountGeneration else { return }
            let issue = AudiobookPlaybackIssue.classify(error)
            PlaybackDiagnostics.event("audiobook-local-connect-deferred reason=\(issue.category)")
            if issue.isConflict { reportConflict(issue) }
            if issue == .authentication { pause(); message = issue.message }
        }
    }
    private func reportConflict(_ issue: AudiobookPlaybackIssue) {
        let checkpoint = book.map { selected -> AudiobookCheckpoint in
            if let checkpoint = pending[selected.id]?.checkpoint { return checkpoint }
            if let downloaded = downloads.records[selected.id]?.book { return AudiobookCheckpoint(book:downloaded,deviceId:music?.deviceId ?? "") }
            var checkpoint = AudiobookCheckpoint(book:selected,deviceId:music?.deviceId ?? ""); checkpoint.position = position; return checkpoint
        }
        pause(); conflict = true; recovery = issue == .editionChanged ? .edition : .position
        recoveryBook = nil; recoveryCheckpoint = checkpoint; message = issue.message
        Task { [weak self] in await self?.refreshRecovery() }
    }
    func refreshRecovery() async {
        guard conflict, !loadingRecovery, let selected = book else { return }
        let account = accountGeneration, token = generation
        loadingRecovery = true
        defer { if account == accountGeneration, token == generation { loadingRecovery = false } }
        do {
            let api = api, id = selected.id
            let fresh = try await AudiobookReadDeadline.load { try await api.audiobook(id) }
            guard account == accountGeneration, token == generation, book?.id == id, conflict, !Task.isCancelled else { return }
            recoveryBook = fresh; book = fresh; bookmarks = fresh.bookmarks ?? []
            position = min(fresh.duration,max(0,position))
            if let checkpoint = pending[id]?.checkpoint ?? recoveryCheckpoint,
               !checkpoint.sourceRevision.isEmpty, !AudiobookEdition.matches(checkpoint,fresh) { recovery = .edition }
        } catch { /* Keep the saved history and known chapters available. */ }
    }
    private func rememberPlace(_ checkpoint: AudiobookCheckpoint, title: String) throws {
        guard checkpoint.position > 0 || checkpoint.finished else { return }
        let place = AudiobookSavedPlace(checkpoint:checkpoint,title:title)
        guard !savedPlaces.contains(where:{ $0.id == place.id }) else { return }
        guard let placesFile else { throw AudiobookPlaybackIssue.storage }
        let forBook = savedPlaces.filter { $0.checkpoint.bookId == checkpoint.bookId }
        let keep = Set(forBook.prefix(2).map(\.id))
        let updated = Array(([place] + savedPlaces.filter { $0.checkpoint.bookId != checkpoint.bookId || keep.contains($0.id) }).prefix(512))
        do { try JSONEncoder().encode(updated).write(to:placesFile,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication]) }
        catch { throw AudiobookPlaybackIssue.storage }
        savedPlaces = updated
    }
    func resumeSavedPlace(_ place: AudiobookSavedPlace, in value: Audiobook) {
        guard AudiobookEdition.matches(place.checkpoint,value) else { return }
        play(value,startingAt:place.checkpoint.position)
    }
    func restart(_ value: Audiobook) throws {
        if book?.id == value.id { pause() }
        let checkpoint = pending[value.id]?.checkpoint ?? AudiobookCheckpoint(book:presentation(value),deviceId:music?.deviceId ?? "")
        try rememberPlace(checkpoint,title:value.title)
        play(value,restart:true)
    }
    func useDevicePlaceInUpdatedBook() {
        guard recovery == .edition, let fresh = recoveryBook, let saved = deviceSavedPosition else { return }
        play(fresh,useRemote:true,startingAt:min(fresh.duration,max(0,saved)))
    }
    func updateCurrentDownload() async {
        guard let selected = book else { return }
        let account = accountGeneration
        do {
            try preservePlaceBeforeDownload(selected)
            await downloads.download(selected.id)
            guard account == accountGeneration, book?.id == selected.id else { return }
            await refreshRecovery()
        } catch { if account == accountGeneration { message = AudiobookPlaybackIssue.classify(error).message } }
    }
    func preservePlaceBeforeDownload(_ value: Audiobook) throws {
        guard downloads.needsUpdate(value), let old = downloads.records[value.id]?.book else { return }
        let checkpoint = pending[value.id]?.checkpoint ?? AudiobookCheckpoint(book:old,deviceId:music?.deviceId ?? "")
        try rememberPlace(checkpoint,title:value.title)
    }
    func keepLocalPosition() async {
        guard let book, recovery != .edition else { return }
        guard let old = pending[book.id] ?? recoveryCheckpoint.map({ Pending(checkpoint:$0,lease:nil,playing:false,dirty:true) }) else { return }
        let account = accountGeneration, token = generation
        do {
            let remote = try await api.audiobook(book.id)
            guard account == accountGeneration, token == generation, self.book?.id == book.id, !Task.isCancelled else { return }
            guard AudiobookEdition.matches(old.checkpoint,remote) else {
                reportConflict(.editionChanged); return
            }
            try rememberPlace(AudiobookCheckpoint(book:remote,deviceId:music?.deviceId ?? ""),title:remote.title)
            var value = old
            value.checkpoint.sessionId = UUID().uuidString.lowercased()
            value.checkpoint.baseRemoteStamp = remote.progress?.lastUpdate ?? 0
            value.checkpoint.seq = 1; value.lease = nil; value.dirty = true
            pending[book.id] = value; persist(); conflict = false; recovery = nil; syncIssues[book.id] = nil; retryDates[book.id] = nil
            await synchronize(force:true, bookID:book.id)
            guard account == accountGeneration, token == generation, self.book?.id == book.id, !Task.isCancelled else { return }
            if pending[book.id]?.dirty != true {
                if old.checkpoint.isStatusChange != true { play(remote) }
                else { recoveryBook = nil; recoveryCheckpoint = nil; message = nil }
            } else { conflict = true; recovery = .position }
        } catch { if account == accountGeneration, token == generation { message = "Connect to choose where to resume. Your saved place is safe." } }
    }
    func markUnread(_ value: Audiobook) async throws -> Audiobook {
        try await setReadingState(value,finished:false)
    }
    func markFinished(_ value: Audiobook) async throws -> Audiobook {
        try await setReadingState(value,finished:true)
    }
    private func setReadingState(_ value: Audiobook, finished: Bool) async throws -> Audiobook {
        guard !unreadBooks.contains(value.id) else { throw AudiobookPlaybackIssue.progressPending }
        let account = accountGeneration
        if book?.id == value.id { pause() }
        unreadBooks.insert(value.id)
        defer { if account == accountGeneration { unreadBooks.remove(value.id) } }
        // Finish a previously submitted write before issuing the explicit reset.
        await syncGate.run {}
        guard account == accountGeneration else { throw CancellationError() }
        var fresh: Audiobook
        do { fresh = try await api.audiobook(value.id) }
        catch {
            let issue = AudiobookPlaybackIssue.classify(error)
            guard issue == .offline || issue == .temporary,
                  let local = downloads.records[value.id]?.book, local.sourceRevision != nil else { throw error }
            fresh = local
        }
        guard account == accountGeneration else { throw CancellationError() }
        if let old = pending[value.id] { try rememberPlace(old.checkpoint,title:value.title) }
        else { try rememberPlace(AudiobookCheckpoint(book:fresh,deviceId:music?.deviceId ?? ""),title:fresh.title) }
        var reset = AudiobookCheckpoint(book:fresh,deviceId:music?.deviceId ?? "")
        reset.seq = 1; reset.position = finished ? fresh.duration : 0; reset.finished = finished; reset.isStatusChange = true
        let previous = pending[value.id]
        pending[value.id] = Pending(checkpoint:reset,lease:nil,playing:false,dirty:true)
        guard let journal else { pending[value.id] = previous; throw AudiobookPlaybackIssue.storage }
        do { try JSONEncoder().encode(pending).write(to:journal,options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication]) }
        catch { pending[value.id] = previous; throw AudiobookPlaybackIssue.storage }
        var stamp = reset.baseRemoteStamp
        do {
            let receipt = try await api.offlineAudiobookProgress(reset)
            guard account == accountGeneration else { throw CancellationError() }
            if receipt.saved, !receipt.pending { stamp = receipt.remoteStamp ?? stamp; pending[value.id] = nil }
        } catch {
            guard account == accountGeneration else { throw CancellationError() }
            let issue = AudiobookPlaybackIssue.classify(error)
            if issue != .offline && issue != .temporary {
                syncIssues[value.id] = issue; persist()
                if issue.isConflict, book?.id == value.id { reportConflict(issue) }
                throw issue
            }
        }
        guard account == accountGeneration else { throw CancellationError() }
        syncIssues[value.id] = nil; retryDates[value.id] = nil; syncFailures[value.id] = nil
        downloads.checkpoint(value.id,position:reset.position,remoteStamp:stamp,finished:finished)
        fresh.progress = AudiobookProgress(currentTime:reset.position,lastUpdate:stamp,isFinished:finished)
        continueListening.removeAll { $0.id == value.id }
        shelf.applyProgress(fresh)
        if book?.id == value.id {
            active = false; wantsPlayback = false; playing = false; preparing = false; manifest = nil
            player.pause(); player.replaceCurrentItem(with:nil); currentURL = nil; position = reset.position; book = fresh; conflict = false; recovery = nil; recoveryBook = nil; recoveryCheckpoint = nil
            part = nil; systemArtwork = nil; receiverArtwork = nil; audioSession.invalidate()
            if music?.state?.playing != true { MPNowPlayingInfoCenter.default().nowPlayingInfo = nil }
        }
        automaticDownloadCandidate = automaticDownloadCandidate == value.id ? nil : automaticDownloadCandidate
        persist(); if book?.id == value.id { message = nil }
        return fresh
    }
    func observe(_ state: PlaybackState) {
        guard active else { return }
        if let epoch = manifest?.musicEpoch, state.ownerEpoch < epoch { return }
        if let manifest, state.ownerDeviceId != music?.deviceId || state.ownerEpoch != manifest.musicEpoch || state.intent != manifest.musicIntent {
            pause(); pending[book?.id ?? ""]?.lease = nil; persist(); message = "Playback moved. Your audiobook place is saved."
        } else if offline, state.playing, state.ownerDeviceId != music?.deviceId { pause(); message = "Another device is playing. Your offline place is saved." }
    }
    func removeDownload(_ id: String) async {
        let account = accountGeneration, token = generation
        if active, book?.id == id, currentURL?.isFileURL == true {
            if !wantsPlayback {
                pause(); active = false; manifest = nil; player.replaceCurrentItem(with:nil)
            } else {
                await synchronize(force:true, bookID:id)
                await connectLocalPlayback(token:token, account:account)
                guard account == accountGeneration, token == generation, !Task.isCancelled else { return }
                guard !offline, manifest != nil else {
                    message = "The server couldn’t be reached. Pause playback before removing this download."; return
                }
                // Replacing an item emits zero-time ticks before the seek.
                // Keep those preparation ticks out of the durable progress journal.
                preparing = true; streamingOverride = token
                do { try await open(at:position, token:token) }
                catch {
                    streamingOverride = nil
                    guard account == accountGeneration, token == generation else { return }
                    try? await open(at:position, token:token)
                    guard account == accountGeneration, token == generation else { return }
                    preparing = false
                    message = "The download was kept because streaming could not start."; return
                }
                if streamingOverride == token { streamingOverride = nil }
                guard account == accountGeneration, token == generation, !Task.isCancelled else { return }
            }
        }
        guard account == accountGeneration else { return }
        await downloads.remove(id)
    }
    func stopForMusic() {
        pause(); active = false; presented = false; book = nil; manifest = nil
        systemArtwork = nil; receiverArtwork = nil
        conflict = false; recovery = nil; recoveryBook = nil; recoveryCheckpoint = nil; message = nil
        player.replaceCurrentItem(with:nil); audioSession.invalidate()
    }
    func clear() {
        pause(); shelf.clear(); accountGeneration = UUID(); syncGate.reset(); heartbeat?.cancel(); heartbeat = nil
        audioSession.invalidate()
        active = false; presented = false; book = nil; manifest = nil; part = nil; enabled = false
        systemArtwork = nil; receiverArtwork = nil
        canonicalLoaded = false
        player.replaceCurrentItem(with:nil); downloads.clear(); pending = [:]; continueListening = []; queue = []; bookmarks = []
        if let journal { try? FileManager.default.removeItem(at:journal) }; journal = nil
        if let preferencesFile { try? FileManager.default.removeItem(at:preferencesFile) }; preferencesFile = nil; preferencesPending = false
        if let placesFile { try? FileManager.default.removeItem(at:placesFile) }; placesFile = nil; savedPlaces = []; loadingRecovery = false
        conflict = false; message = nil; retryDates = [:]; syncFailures = [:]; syncIssues = [:]; resolvingRemote = [:]
        recovery = nil; recoveryBook = nil; recoveryCheckpoint = nil
        automaticDownloadCandidate = nil; unreadBooks = []
    }
    func setSleep(_ choice: String) {
        sleepChoice = choice; sleepDeadline = nil; sleepChapter = nil
        if choice == "chapter" { sleepChapter = chapter?.end }
        else if let minutes = Double(choice), minutes > 0 { sleepDeadline = Date().addingTimeInterval(minutes*60) }
    }
    func updatePreferences(_ value: AudiobookPreferences) async {
        preferences = value; preferencesPending = true; savePreferences()
        player.rate = playing ? Float(value.speed) : 0; systemControls(); publish()
        await synchronizePreferences()
    }
    private func synchronizePreferences() async {
        guard preferencesPending, !savingPreferences else { return }; savingPreferences = true
        let account = accountGeneration; defer { savingPreferences = false }
        while preferencesPending {
            let value = preferences
            do {
                let saved = try await api.audiobookPreferences(value)
                guard account == accountGeneration else { return }
                if preferences == value { preferences = saved; preferencesPending = false }
                savePreferences()
            } catch { if account == accountGeneration { message = "Listening settings are saved on this device and will sync when connected." }; return }
        }
    }
    private func savePreferences() {
        if let preferencesFile, let data = try? JSONEncoder().encode(SavedPreferences(value:preferences, pending:preferencesPending)) { try? data.write(to:preferencesFile, options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication]) }
    }
    func addBookmark() async {
        guard let book else { return }
        let account = accountGeneration
        do {
            try await api.audiobookBookmark(book.id, position:position, title:chapter?.title ?? "Bookmark")
            guard account == accountGeneration, self.book?.id == book.id else { return }
            let value = try await api.audiobook(book.id)
            guard account == accountGeneration, self.book?.id == book.id else { return }
            bookmarks = value.bookmarks ?? []; message = "Bookmark saved."
        } catch { if account == accountGeneration { message = "Connect to save this bookmark." } }
    }
    func refreshQueue() async {
        let account = accountGeneration
        if let state = try? await api.audiobookState(), account == accountGeneration { queue = state.queue ?? [] }
    }
    func queueBook(_ id: String, action: String) async {
        let account = accountGeneration
        do {
            let state = try await api.audiobookState()
            guard account == accountGeneration else { return }
            try await api.audiobookQueue(id, action:action, revision:state.revision ?? 0)
            guard account == accountGeneration else { return }; await refreshQueue()
            if account == accountGeneration { message = action == "next" ? "This book is next in Up Next." : "Added to Up Next." }
        } catch { if account == accountGeneration { message = "Your book queue could not be updated. Try again when connected." } }
    }
    func editQueue(_ id: String, delta: Int? = nil) async {
        let account = accountGeneration
        do {
            let state = try await api.audiobookState(); var ids = (state.queue ?? []).map(\.id)
            guard account == accountGeneration else { return }
            guard let index = ids.firstIndex(of:id) else { return }
            if let delta { let target = index + delta; guard ids.indices.contains(target) else { return }; ids.swapAt(index,target) }
            else { ids.remove(at:index) }
            try await api.audiobookQueue("", action:"order", revision:state.revision ?? 0, ids:ids)
            guard account == accountGeneration else { return }; await refreshQueue()
        } catch { if account == accountGeneration { message = "Your queue changed. Open Up Next and try again." } }
    }
    func playQueued(_ id: String) async {
        let account = accountGeneration, token = generation
        do {
            let value = try await api.audiobook(id)
            guard account == accountGeneration, token == generation else { return }; play(value)
        } catch { if account == accountGeneration, token == generation { message = "This book will be ready when Arrify reconnects." } }
    }
    func removeBookmark(_ mark: AudiobookBookmark) async {
        guard let book else { return }
        let account = accountGeneration
        do {
            try await api.audiobookBookmark(book.id, position:mark.time, title:mark.title, remove:true)
            guard account == accountGeneration, self.book?.id == book.id else { return }
            let value = try await api.audiobook(book.id)
            guard account == accountGeneration, self.book?.id == book.id else { return }; bookmarks = value.bookmarks ?? []
        } catch { if account == accountGeneration { message = "Connect to remove this bookmark." } }
    }
    private func ended() {
        guard active, wantsPlayback, let book, !preparing, !finishing else { return }
        let end = (part?.start ?? 0)+(part?.duration ?? 0)
        if let sleepChapter, end >= sleepChapter {
            position = sleepChapter; pause(capturingPosition:false); setSleep("off"); message = "Sleep timer finished. Your place is saved."; return
        }
        if let part, let next = book.parts?.first(where:{ $0.index > part.index }) { seek(next.start); return }
        finishing = true; position = book.duration; playing = false; save(finished:true)
        let account = accountGeneration, token = generation
        Task { [weak self] in
            guard let self else { return }; let lease = self.manifest?.lease
            await self.synchronize(force:true)
            guard account == self.accountGeneration, token == self.generation else { return }
            if self.preferences.continuous, self.sleepChoice == "off", self.wantsPlayback, self.active,
               self.pending[book.id]?.dirty != true, let lease,
               let state = try? await self.api.audiobookState(), state.active, state.lease == lease,
               let next = state.queue?.first, let value = try? await self.api.audiobook(next.id) {
                guard account == self.accountGeneration, token == self.generation, self.active, self.wantsPlayback else { return }
                self.play(value, previousLease:lease)
            } else if account == self.accountGeneration, token == self.generation { self.wantsPlayback = false; self.message = "You’ve finished " + book.title + "."; self.publish() }
        }
    }
    func remote(_ action: String, position: Double?) -> Bool {
        guard active else { return false }
        switch action {
        case "resume": resume()
        case "pause": pause()
        case "toggle": toggle()
        case "back": seek(self.position-Double(preferences.backSeconds))
        case "forward": seek(self.position+Double(preferences.forwardSeconds))
        case "seek": if let position { seek(position) }
        case "rate": if let position, (0.5...3).contains(position) { var value = preferences; value.speed = position; Task { await updatePreferences(value) } }
        default: return true
        }; return true
    }
    private func systemControls() {
        let commands = MPRemoteCommandCenter.shared()
        commands.skipBackwardCommand.preferredIntervals = [NSNumber(value:preferences.backSeconds)]
        commands.skipForwardCommand.preferredIntervals = [NSNumber(value:preferences.forwardSeconds)]
        commands.skipBackwardCommand.isEnabled = true; commands.skipForwardCommand.isEnabled = true
        commands.changePlaybackRateCommand.isEnabled = true
        commands.changePlaybackRateCommand.supportedPlaybackRates = [0.5,0.75,1,1.25,1.5,1.75,2,2.5,3]
        commands.nextTrackCommand.isEnabled = false; commands.previousTrackCommand.isEnabled = false
    }
    private func publish() {
        guard active, let book else { return }
        player.currentItem?.externalMetadata = ReceiverMetadata.make(title:book.title, artist:book.author,
            album:chapter?.title ?? "Audiobook", artwork:receiverArtwork)
        var info: [String:Any] = [MPMediaItemPropertyTitle:book.title, MPMediaItemPropertyArtist:book.author,
            MPMediaItemPropertyAlbumTitle:chapter?.title ?? "Audiobook", MPMediaItemPropertyPlaybackDuration:book.duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime:position, MPNowPlayingInfoPropertyPlaybackRate:playing ? preferences.speed : 0,
            MPNowPlayingInfoPropertyDefaultPlaybackRate:preferences.speed, MPNowPlayingInfoPropertyMediaType:MPNowPlayingInfoMediaType.audio.rawValue,
            MPNowPlayingInfoPropertyIsLiveStream:false]
        if let systemArtwork { info[MPMediaItemPropertyArtwork] = systemArtwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
}
