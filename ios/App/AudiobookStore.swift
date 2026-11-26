import SwiftUI
import ArrifyCore
import CryptoKit
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer

@MainActor @Observable final class AudiobookStore {
    private typealias Pending = AudiobookJournalEntry
    let api: APIClient
    let downloads: AudiobookDownloads
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
    private var preferencesPending = false
    private var savingPreferences = false
    private struct SavedPreferences: Codable { let value: AudiobookPreferences; let pending: Bool }
    private var part: AudiobookPart?
    private var currentURL: URL?
    private var wantsPlayback = false
    private var syncing = false
    private var retryAt = Date.distantPast
    private var failures = 0
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
    private var canonicalLoaded = false
    private(set) var enabled = false
    var preferences = AudiobookPreferences()
    private(set) var continueListening: [Audiobook] = []
    private(set) var book: Audiobook?
    private(set) var manifest: AudiobookManifest?
    private(set) var position = 0.0
    private(set) var playing = false
    private(set) var preparing = false
    private(set) var active = false
    private(set) var offline = false
    private(set) var conflict = false
    private(set) var message: String?
    private(set) var queue: [AudiobookQueueEntry] = []
    private(set) var bookmarks: [AudiobookBookmark] = []
    var presented = false
    var sleepChoice = "off"
    var chapter: AudiobookChapter? { AudiobookTimeline.chapter(book?.chapters ?? [], at:position) }
    var visible: Bool { enabled && preferences.visible }
    var resumeBooks: [Audiobook] {
        var values = continueListening
        guard !canonicalLoaded else { return values }
        for record in downloads.records.values where record.finished != true {
            guard let position = record.lastPosition, position > 0 else { continue }
            var book = record.manifest.book
            book.progress = AudiobookProgress(currentTime:position, lastUpdate:record.remoteStamp ?? 0, isFinished:false)
            if let index = values.firstIndex(where:{ $0.id == book.id }) { values[index] = book }
            else { values.append(book) }
        }
        return values
    }
    init(api: APIClient, music: PlaybackCoordinator, audioSession: (any PlaybackAudioSession)? = nil, localMedia: ((Audiobook, AudiobookPart) -> URL?)? = nil) {
        self.api = api; self.music = music; downloads = AudiobookDownloads(api:api)
        self.localMedia = localMedia
        self.audioSession = audioSession ?? SystemPlaybackAudioSession()
        observer = player.addPeriodicTimeObserver(forInterval:CMTime(seconds:0.5, preferredTimescale:600), queue:.main) { @Sendable [weak self] time in
            let seconds = time.seconds
            MainActor.assumeIsolated { self?.tick(seconds) }
        }
        notifications.append(NotificationCenter.default.addObserver(forName:.AVPlayerItemDidPlayToEndTime, object:nil, queue:.main) { @Sendable [weak self] note in
            MainActor.assumeIsolated { if let self, (note.object as? AVPlayerItem) === self.player.currentItem { self.ended() } }
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
                MainActor.assumeIsolated { guard let self, self.active else { return }; self.pause(); self.player.replaceCurrentItem(with:nil); self.audioSession.invalidate(); self.message = "Audio was reset. Tap Play to continue from your saved place." }
            })
        }
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
                continueListening = values; canonicalLoaded = true
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
            }
        }
    }
    func play(_ value: Audiobook, restart: Bool = false, useRemote: Bool = false, previousLease: String? = nil) {
        pause(); active = false; book = value; let ticket = UUID(); generation = ticket; preparing = true; message = nil; conflict = false; presented = true
        operation = Task { [weak self] in
            guard let self else { return }
            let account = self.accountGeneration
            do {
                await self.synchronize(force:true)
                if self.pending[value.id]?.dirty == true && !useRemote { self.preparing = false; self.conflict = true; self.message = "Your offline position needs to be saved before continuing."; return }
                if useRemote { self.pending.removeValue(forKey:value.id); self.persist() }
                let musicState = try await self.api.state()
                let result = try await self.api.beginAudiobook(value.id, deviceId:self.music?.deviceId ?? "", revision:musicState.queueRevision, requestId:ticket.uuidString.lowercased(), restart:restart, useRemote:useRemote, previousLease:previousLease)
                guard ticket == self.generation, account == self.accountGeneration, !Task.isCancelled else { return }
                self.install(result.book, manifest:result, position:result.position, offline:false)
                try await self.open(at:result.position, token:ticket)
                await self.refreshQueue()
            } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
                guard ticket == self.generation, account == self.accountGeneration else { return }
                if case APIError.rejected(let status, _, let text) = error, [401,403,409].contains(status) {
                    self.message = text; self.conflict = status == 409; self.preparing = false; return
                }
                if self.downloads.complete(value.id), let stored = self.downloads.records[value.id]?.manifest {
                    let downloaded = self.downloads.records[value.id]
                    let cursor = restart || downloaded?.finished == true ? 0 : self.pending[value.id]?.checkpoint.position ?? downloaded?.lastPosition ?? stored.book.progress?.currentTime ?? stored.position
                    self.install(stored.book, manifest:nil, position:cursor, offline:true)
                    do { try await self.open(at:cursor, token:ticket) }
                    catch { self.preparing = false; self.message = "This downloaded book could not be opened." }
                } else { self.preparing = false; self.message = "This book will be ready when Arrify reconnects. Download it to listen offline." }
            }
        }
    }
    private func install(_ value: Audiobook, manifest: AudiobookManifest?, position: Double, offline: Bool) {
        music?.suspendForAudiobook(); self.book = value; self.manifest = manifest; self.position = position
        self.offline = offline; active = true; wantsPlayback = true; part = nil; finishing = false
        bookmarks = value.bookmarks ?? []; sleepChoice = "off"; sleepDeadline = nil; sleepChapter = nil
        systemArtwork = nil; recoveryAttempts = 0; lastAudioPosition = position; lastAudioProgress = Date()
        Task { [weak self] in
            guard let self, let image = await self.music?.artworkStore.image(value.cover, pixels:1024), self.book?.id == value.id, self.active else { return }
            self.systemArtwork = NowPlayingArtwork.make(image:image); self.publish()
        }
        if pending[value.id]?.dirty != true {
            var checkpoint = AudiobookCheckpoint(book:value, deviceId:music?.deviceId ?? "")
            checkpoint.position = position
            if offline, let stamp = downloads.records[value.id]?.remoteStamp { checkpoint.baseRemoteStamp = stamp }
            pending[value.id] = Pending(checkpoint:checkpoint, lease:manifest?.lease, playing:true, dirty:false)
        }
        persist(); systemControls()
        PlaybackDiagnostics.event(offline ? "audiobook-open-offline" : "audiobook-open-stream")
    }
    private func open(at target: Double, token: UUID) async throws {
        guard let book, let selected = AudiobookTimeline.part(book.parts ?? [], at:target) else { throw APIError.invalidEndpoint }
        if part?.index == selected.index, let item = player.currentItem, item.status == .readyToPlay {
            try await seekAndPlay(selected:selected, target:target, token:token); return
        }
        var address: URL
        if let local = localMedia?(book, selected) ?? downloads.file(book, part:selected) { address = local }
        else {
            guard let path = selected.url else { throw APIError.invalidEndpoint }
            address = try await api.audiobookMediaURL(path)
        }
        for attempt in 0..<3 {
            do {
                guard token == generation, !Task.isCancelled else { throw CancellationError() }
                let item = AVPlayerItem(url:address); item.audioTimePitchAlgorithm = .spectral; part = selected; currentURL = address
                player.replaceCurrentItem(with:item)
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
        publish(); save(); await synchronize()
    }

    func pause() {
        if interrupted { resumeAfterInterruption = false }
        wantsPlayback = false; generation = UUID(); operation?.cancel(); operation = nil
        player.pause(); playing = false; preparing = false
        if active { save(); publish(); Task { await synchronize() } }
    }
    func resume() {
        guard active, let book else { return }; message = nil
        if conflict { presented = true; return }
        wantsPlayback = true; let ticket = UUID(); generation = ticket; preparing = true
        lastAudioProgress = Date()
        operation = Task { [weak self] in
            guard let self else { return }
            do {
                if !self.offline, let manifest = self.manifest {
                    do {
                        let state = try await self.api.audiobookState()
                        if !state.active || state.lease != manifest.lease { self.play(book); return }
                    } catch {
                        if case APIError.rejected(let status, _, _) = error, [401,403,409].contains(status) { throw error }
                        guard self.downloads.complete(book.id) || self.player.currentItem?.status == .readyToPlay else { throw error }
                    }
                }
                try await self.open(at:self.position, token:ticket)
            } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
                guard ticket == self.generation else { return }
                self.preparing = false; self.playing = false; self.message = "Your place is saved. Tap Play when the connection returns."
            }
        }
    }
    func seek(_ target: Double) {
        guard let book else { return }; let wasPlaying = wantsPlayback
        pause(); finishing = false; position = min(book.duration, max(0,target)); save(); wantsPlayback = wasPlaying
        let ticket = UUID(); generation = ticket; preparing = true
        operation = Task { [weak self] in
            do { try await self?.open(at:self?.position ?? 0, token:ticket) }
            catch { guard let self, ticket == self.generation else { return }; self.preparing = false; self.playing = false; self.message = "Your place is saved. Tap Play to reconnect." }
        }
    }
    func toggle() { if wantsPlayback || preparing { pause() } else if !active, let book { play(book) } else { resume() } }
    private func tick(_ seconds: Double) {
        guard active, !preparing, seconds.isFinite else { return }
        position = min(book?.duration ?? 0, (part?.start ?? 0)+seconds); playing = player.timeControlStatus == .playing
        if position > lastAudioPosition + 0.1 { lastAudioProgress = Date(); lastAudioPosition = position }
        if (sleepDeadline.map { Date() >= $0 } ?? false) || (sleepChapter.map { position >= $0 } ?? false) {
            pause(); setSleep("off"); message = "Sleep timer finished. Your place is saved."
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
        value.checkpoint.seq += 1; value.checkpoint.position = position; value.checkpoint.finished = finished || finishing
        value.playing = playing; value.dirty = true; pending[book.id] = value; lastSaved = Date(); persist()
        downloads.checkpoint(book.id, position:position, finished:value.checkpoint.finished)
    }
    func synchronize(force: Bool = false) async {
        guard !syncing, force || Date() >= retryAt else { return }; syncing = true; let ticket = accountGeneration
        defer { syncing = false }
        for (id, value) in pending {
            guard value.dirty else { continue }
            do {
                let receipt: AudiobookReceipt
                if let lease = value.lease {
                    do { receipt = try await api.audiobookProgress(lease:lease, deviceId:value.checkpoint.deviceId, seq:value.checkpoint.seq, position:value.checkpoint.position, playing:value.playing, finished:value.checkpoint.finished) }
                    catch APIError.rejected(let status, _, _) where status == 409 {
                        guard ticket == accountGeneration else { return }
                        pending[id]?.lease = nil; persist()
                        receipt = try await api.offlineAudiobookProgress(value.checkpoint)
                    }
                } else { receipt = try await api.offlineAudiobookProgress(value.checkpoint) }
                guard ticket == accountGeneration else { return }
                guard pending[id]?.checkpoint.sessionId == value.checkpoint.sessionId else { continue }
                if receipt.saved && !receipt.pending {
                    // Preserve a newer local seek/checkpoint while this reply was in flight.
                    let clean = pending[id]?.acknowledge(session:value.checkpoint.sessionId, sequence:value.checkpoint.seq, remoteStamp:receipt.remoteStamp) == true
                    if clean && (!active || book?.id != id) { pending.removeValue(forKey:id) }
                    if let stamp = receipt.remoteStamp { downloads.checkpoint(id, remoteStamp:stamp) }
                    persist(); failures = 0; retryAt = .distantPast
                }
                if receipt.active == false, manifest?.lease == value.lease { pause(); message = "Playback moved to another device. Your place is saved." }
            } catch where RequestCancellation.matches(error) || Task.isCancelled { return }
            catch {
                guard ticket == accountGeneration else { return }
                if case APIError.rejected(let status, _, let text) = error, status == 409 {
                    if book?.id == id { conflict = true; pause(); message = text }
                    retryAt = .distantFuture
                } else { failures += 1; retryAt = Date().addingTimeInterval(min(60,pow(2,Double(min(failures,6))))*Double.random(in:0.8...1.2)) }
                return
            }
        }
    }
    func networkAvailable() async { retryAt = .distantPast; await synchronizePreferences(); await synchronize() }
    func observe(_ state: PlaybackState) {
        guard active else { return }
        if let epoch = manifest?.musicEpoch, state.ownerEpoch < epoch { return }
        if let manifest, state.ownerDeviceId != music?.deviceId || state.ownerEpoch != manifest.musicEpoch || state.intent != manifest.musicIntent {
            pause(); pending[book?.id ?? ""]?.lease = nil; persist(); message = "Playback moved. Your audiobook place is saved."
        } else if offline, state.playing, state.ownerDeviceId != music?.deviceId { pause(); message = "Another device is playing. Your offline place is saved." }
    }
    func stopForMusic() {
        guard active else { return }; pause(); active = false; presented = false; book = nil; manifest = nil
        player.replaceCurrentItem(with:nil); audioSession.invalidate()
    }
    func clear() {
        pause(); accountGeneration = UUID(); heartbeat?.cancel(); heartbeat = nil
        active = false; presented = false; book = nil; manifest = nil; part = nil; enabled = false
        canonicalLoaded = false
        player.replaceCurrentItem(with:nil); downloads.clear(); pending = [:]; continueListening = []; queue = []; bookmarks = []
        if let journal { try? FileManager.default.removeItem(at:journal) }; journal = nil
        if let preferencesFile { try? FileManager.default.removeItem(at:preferencesFile) }; preferencesFile = nil; preferencesPending = false
        conflict = false; message = nil; retryAt = .distantPast; failures = 0; syncing = false
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
        do { try await api.audiobookBookmark(book.id, position:position, title:chapter?.title ?? "Bookmark"); bookmarks = (try await api.audiobook(book.id)).bookmarks ?? []; message = "Bookmark saved." }
        catch { message = "Connect to save this bookmark." }
    }
    func refreshQueue() async {
        if let state = try? await api.audiobookState() { queue = state.queue ?? [] }
    }
    func queueBook(_ id: String, action: String) async {
        do { let state = try await api.audiobookState(); try await api.audiobookQueue(id, action:action, revision:state.revision ?? 0); await refreshQueue(); message = action == "next" ? "This book is next in Up Next." : "Added to Up Next." }
        catch { message = "Your book queue could not be updated. Try again when connected." }
    }
    func editQueue(_ id: String, delta: Int? = nil) async {
        do {
            let state = try await api.audiobookState(); var ids = (state.queue ?? []).map(\.id)
            guard let index = ids.firstIndex(of:id) else { return }
            if let delta { let target = index + delta; guard ids.indices.contains(target) else { return }; ids.swapAt(index,target) }
            else { ids.remove(at:index) }
            try await api.audiobookQueue("", action:"order", revision:state.revision ?? 0, ids:ids); await refreshQueue()
        } catch { message = "Your queue changed. Open Up Next and try again." }
    }
    func playQueued(_ id: String) async {
        do { play(try await api.audiobook(id)) } catch { message = "This book will be ready when Arrify reconnects." }
    }
    func removeBookmark(_ mark: AudiobookBookmark) async {
        guard let book else { return }
        do { try await api.audiobookBookmark(book.id, position:mark.time, title:mark.title, remove:true); bookmarks = (try await api.audiobook(book.id)).bookmarks ?? [] }
        catch { message = "Connect to remove this bookmark." }
    }
    private func ended() {
        guard active, let book, !preparing, !finishing else { return }
        let end = (part?.start ?? 0)+(part?.duration ?? 0)
        if end < book.duration-1 { seek(end+0.05); return }
        finishing = true; position = book.duration; playing = false; save(finished:true)
        Task { [weak self] in
            guard let self else { return }; let lease = self.manifest?.lease
            await self.synchronize(force:true)
            if self.preferences.continuous, self.sleepChoice == "off", self.wantsPlayback, self.active,
               self.pending[book.id]?.dirty != true, let lease,
               let state = try? await self.api.audiobookState(), state.active, state.lease == lease,
               let next = state.queue?.first, let value = try? await self.api.audiobook(next.id) {
                self.play(value, previousLease:lease)
            } else { self.wantsPlayback = false; self.message = "You’ve finished " + book.title + "."; self.publish() }
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
        var info: [String:Any] = [MPMediaItemPropertyTitle:book.title, MPMediaItemPropertyArtist:book.author,
            MPMediaItemPropertyAlbumTitle:chapter?.title ?? "Audiobook", MPMediaItemPropertyPlaybackDuration:book.duration,
            MPNowPlayingInfoPropertyElapsedPlaybackTime:position, MPNowPlayingInfoPropertyPlaybackRate:playing ? preferences.speed : 0,
            MPNowPlayingInfoPropertyDefaultPlaybackRate:preferences.speed, MPNowPlayingInfoPropertyMediaType:MPNowPlayingInfoMediaType.audio.rawValue,
            MPNowPlayingInfoPropertyIsLiveStream:false]
        if let systemArtwork { info[MPMediaItemPropertyArtwork] = systemArtwork }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
}
