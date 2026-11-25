import Foundation
import Observation
import ArrifyCore

/// A small disposable read-ahead window, scoped to this signed-in app lifetime.
/// Downloads go to disk, are size-checked, and are never counted as ready early.
@MainActor @Observable final class AudioCache {
    private struct Entry { let key: String; let url: URL; let bytes: Int }
    private var files: [Int: Entry] = [:]
    private let directory: URL
    private let download: @Sendable (URL) async throws -> (URL, URLResponse)
    private static var didPrune = false
    private var generation = UUID()
    private var fillGeneration = UUID()
    private var downloadTokens: [String: UUID] = [:]
    private var downloads: [String: Task<Void, Never>] = [:]
    var items: [BufferEntry] = []
    var downloadedBytes: Int { items.reduce(0) { $0 + $1.loadedBytes } }
    init(download: (@Sendable (URL) async throws -> (URL, URLResponse))? = nil) {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent("arrify-audio-" + UUID().uuidString, isDirectory: true)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil; configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 180; configuration.httpMaximumConnectionsPerHost = 1
        let session = URLSession(configuration: configuration)
        self.download = download ?? { url in try await session.download(from: url) }
        // A force-quit may leave disposable files; never retain another session's audio.
        if !Self.didPrune, let old = try? FileManager.default.contentsOfDirectory(at: FileManager.default.temporaryDirectory, includingPropertiesForKeys: nil) {
            for folder in old where folder.lastPathComponent.hasPrefix("arrify-audio-") { try? FileManager.default.removeItem(at: folder) }
        }
        Self.didPrune = true
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    private func key(_ item: MediaItem) -> String { "\(item.trackId):\(item.sourceRevision ?? ""):\(item.profile)" }
    func file(for item: MediaItem) -> URL? {
        guard let entry = files[item.trackId] else { return nil }
        guard entry.key == key(item) else { invalidate(item.trackId); return nil }
        guard let bytes = try? FileManager.default.attributesOfItem(atPath: entry.url.path)[.size] as? NSNumber,
              bytes.intValue == entry.bytes else { invalidate(item.trackId); return nil }
        return entry.url
    }
    func invalidate(_ id: Int, matching url: URL? = nil) {
        if let url, files[id]?.url != url { return }
        if let entry = files.removeValue(forKey: id) { try? FileManager.default.removeItem(at: entry.url) }
        update(id, state: "retrying")
    }
    func reset() {
        generation = UUID(); fillGeneration = UUID()
        for task in downloads.values { task.cancel() }; downloads = [:]; downloadTokens = [:]
        files = [:]; items = []
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    func fill(_ manifest: Manifest, api: APIClient, onReady: (() async -> Void)? = nil) async {
        let epoch = generation
        let pass = UUID(); fillGeneration = pass
        let window = Array(manifest.items.prefix(4)); let wanted = Set(window.map(\.trackId))
        let wantedKeys = Set(window.map { key($0) })
        for key in Array(downloads.keys) where !wantedKeys.contains(key) { downloads.removeValue(forKey: key)?.cancel(); downloadTokens.removeValue(forKey: key) }
        for id in Array(files.keys) where !wanted.contains(id) {
            if let old = files.removeValue(forKey: id) { try? FileManager.default.removeItem(at: old.url) }
        }
        items = window.map { item in
            let ready = file(for: item) != nil
            return BufferEntry(id: item.trackId, loadedBytes: ready ? files[item.trackId]!.bytes : 0,
                               totalBytes: min(256 * 1024 * 1024, max(0, item.sizeBytes ?? 0)), state: ready ? "ready" : "queued")
        }
        for item in window {
            guard epoch == generation, fillGeneration == pass, !Task.isCancelled else { return }
            if file(for: item) != nil { await onReady?(); continue }
            guard item.state == "ready", let size = item.sizeBytes, size > 0 else { continue }
            guard size <= 256 * 1024 * 1024 else { update(item.trackId, state: "oversized"); continue }
            guard files.values.reduce(0, { $0 + $1.bytes }) + size <= 512 * 1024 * 1024 else { update(item.trackId, state: "budget"); continue }
            let cacheKey = key(item)
            if downloads[cacheKey] == nil {
                update(item.trackId, state: "downloading")
                let token = UUID(); downloadTokens[cacheKey] = token
                downloads[cacheKey] = Task { [weak self] in
                    guard let self else { return }
                    defer { if self.downloadTokens[cacheKey] == token { self.downloads.removeValue(forKey: cacheKey); self.downloadTokens.removeValue(forKey: cacheKey) } }
                    do {
                        let remote = try await api.mediaURL(item)
                        PlaybackDiagnostics.event("cache-download-start bytes=\(size)")
                        let (temporary, response) = try await self.downloadOnAvailableRoute(remote, api: api, generation: epoch)
                        defer { try? FileManager.default.removeItem(at: temporary) }
                        guard self.generation == epoch, !Task.isCancelled else { return }
                        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                              (try FileManager.default.attributesOfItem(atPath: temporary.path)[.size] as? NSNumber)?.intValue == size else { throw APIError.invalidResponse }
                        let suffix = item.mimeType == "audio/flac" || item.mimeType == "audio/x-flac" ? ".flac" : item.mimeType == "audio/mpeg" ? ".mp3" : item.mimeType == "audio/mp4" ? ".m4a" : ["audio/wav", "audio/x-wav", "audio/wave"].contains(item.mimeType ?? "") ? ".wav" : ".audio"
                        let target = self.directory.appendingPathComponent(UUID().uuidString + suffix)
                        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: temporary.path)
                        try FileManager.default.moveItem(at: temporary, to: target)
                        if let old = self.files[item.trackId] { try? FileManager.default.removeItem(at: old.url) }
                        self.files[item.trackId] = Entry(key: cacheKey, url: target, bytes: size)
                        self.update(item.trackId, state: "ready", bytes: size)
                        PlaybackDiagnostics.event("cache-download-ready bytes=\(size)")
                    } catch {
                        guard self.generation == epoch, !Task.isCancelled else { return }
                        self.update(item.trackId, state: "retrying")
                        PlaybackDiagnostics.event("cache-download-failed code=\((error as NSError).code)")
                    }
                }
            }
            // Cancellation of a stale queue reader does not cancel a useful
            // current/next download. reset() and window eviction do cancel it.
            await downloads[cacheKey]?.value
            guard epoch == generation, fillGeneration == pass, !Task.isCancelled else { return }
            if file(for: item) != nil { await onReady?() }
        }
    }
    private func downloadOnAvailableRoute(_ url: URL, api: APIClient, generation epoch: UUID) async throws -> (URL, URLResponse) {
        do {
            let result = try await download(url)
            if let response = result.1 as? HTTPURLResponse, [502, 503, 504].contains(response.statusCode) {
                try? FileManager.default.removeItem(at: result.0)
                throw APIError.rejected(status: response.statusCode, code: "stream_unavailable", message: "Stream unavailable")
            }
            return result
        } catch {
            guard generation == epoch, !Task.isCancelled, !RequestCancellation.matches(error) else { throw CancellationError() }
            // The download is an idempotent GET. Alternate origins still require the pinned server proof.
            guard ReadRecovery.delay(error, attempt: 0) != nil,
                  let alternate = try await api.alternateMediaURL(url) else { throw error }
            guard generation == epoch, !Task.isCancelled else { throw CancellationError() }
            PlaybackDiagnostics.event("cache-download-route-retry")
            return try await download(alternate)
        }
    }
    private func update(_ id: Int, state: String, bytes: Int = 0) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].state = state; items[index].loadedBytes = bytes
    }
}
