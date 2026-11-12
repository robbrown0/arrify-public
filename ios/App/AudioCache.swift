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
        generation = UUID(); files = [:]; items = []
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }
    func fill(_ manifest: Manifest, api: APIClient) async {
        let epoch = generation
        let window = Array(manifest.items.prefix(4)); let wanted = Set(window.map(\.trackId))
        for id in Array(files.keys) where !wanted.contains(id) {
            if let old = files.removeValue(forKey: id) { try? FileManager.default.removeItem(at: old.url) }
        }
        items = window.map { item in
            let ready = file(for: item) != nil
            return BufferEntry(id: item.trackId, loadedBytes: ready ? files[item.trackId]!.bytes : 0,
                               totalBytes: min(256 * 1024 * 1024, max(0, item.sizeBytes ?? 0)), state: ready ? "ready" : "queued")
        }
        for item in window {
            guard epoch == generation, !Task.isCancelled else { return }
            guard file(for: item) == nil else { continue }
            guard item.state == "ready", let size = item.sizeBytes, size > 0 else { continue }
            guard size <= 256 * 1024 * 1024 else { update(item.trackId, state: "oversized"); continue }
            guard files.values.reduce(0, { $0 + $1.bytes }) + size <= 512 * 1024 * 1024 else { update(item.trackId, state: "budget"); continue }
            update(item.trackId, state: "downloading")
            do {
                let remote = try await api.mediaURL(item)
                let (temporary, response) = try await download(remote)
                defer { try? FileManager.default.removeItem(at: temporary) }
                guard epoch == generation, !Task.isCancelled else { return }
                guard let http = response as? HTTPURLResponse, http.statusCode == 200,
                      (try FileManager.default.attributesOfItem(atPath: temporary.path)[.size] as? NSNumber)?.intValue == size else { throw APIError.invalidResponse }
                let suffix = item.mimeType == "audio/flac" || item.mimeType == "audio/x-flac" ? ".flac" : item.mimeType == "audio/mpeg" ? ".mp3" : item.mimeType == "audio/mp4" ? ".m4a" : ["audio/wav", "audio/x-wav", "audio/wave"].contains(item.mimeType ?? "") ? ".wav" : ".audio"
                let target = directory.appendingPathComponent(UUID().uuidString + suffix)
                try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: temporary.path)
                try FileManager.default.moveItem(at: temporary, to: target)
                if let old = files[item.trackId] { try? FileManager.default.removeItem(at: old.url) }
                files[item.trackId] = Entry(key: key(item), url: target, bytes: size)
                update(item.trackId, state: "ready", bytes: size)
            } catch {
                if epoch == generation { update(item.trackId, state: "retrying") }
            }
        }
    }
    private func update(_ id: Int, state: String, bytes: Int = 0) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].state = state; items[index].loadedBytes = bytes
    }
}
