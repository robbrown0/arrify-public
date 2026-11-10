import SwiftUI
import ArrifyCore
import ImageIO
import CryptoKit

@MainActor @Observable final class LibraryStore {
    let api: APIClient
    var overview: LibraryOverview?
    var loading = false
    var error: String?
    var updated: Date?
    private var generation = UUID()
    init(api: APIClient) { self.api = api }
    func refresh() async {
        let token = UUID(); generation = token; loading = true
        defer { if generation == token { loading = false } }
        do {
            let result = try await api.library()
            guard generation == token, !Task.isCancelled else { return }
            overview = result; error = nil; updated = Date()
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch {
            if generation == token { self.error = error.localizedDescription }
        }
    }
    func clear() { generation = UUID(); overview = nil; error = nil; updated = nil; loading = false }
}

@MainActor @Observable final class LibraryBrowser {
    let api: APIClient
    let kind: String
    let source: LibrarySource?
    var tracks: [Track] = []
    var entries: [LibraryEntry] = []
    var item: LibraryEntry?
    var total = 0
    var nextOffset: Int?
    var loading = false
    var error: String?
    var updated: Date?
    private var revision: String?
    private var generation = UUID()
    init(api: APIClient, kind: String = "tracks", source: LibrarySource? = nil) { self.api = api; self.kind = kind; self.source = source }
    func load(query: String = "", more: Bool = false) async {
        if more && (loading || nextOffset == nil) { return }
        let token: UUID
        if more { token = generation } else { token = UUID(); generation = token }
        loading = true
        defer { if generation == token { loading = false } }
        do {
            let offset = more ? nextOffset ?? 0 : 0
            let rev = more ? revision : nil
            if let source {
                let page = try await api.detail(source, offset: offset, revision: rev)
                guard token == generation, !Task.isCancelled else { return }
                tracks = more ? tracks + page.items : page.items
                item = page.item; total = page.total; nextOffset = page.nextOffset; revision = page.revision
            } else if kind == "tracks" {
                let page = try await api.catalog(query: query, offset: offset, revision: rev)
                guard token == generation, !Task.isCancelled else { return }
                tracks = more ? tracks + page.items : page.items
                total = page.total ?? tracks.count; nextOffset = page.nextOffset; revision = page.revision
            } else {
                let page = try await api.entities(kind: kind, query: query, offset: offset, revision: rev)
                guard token == generation, !Task.isCancelled else { return }
                entries = more ? entries + page.items : page.items
                total = page.total; nextOffset = page.nextOffset; revision = page.revision
            }
            error = nil; updated = Date()
        } catch where RequestCancellation.matches(error) || Task.isCancelled {} catch APIError.rejected(_, let code, _) where code == "catalog_changed" && more {
            guard generation == token else { return }
            await load(query: query)
        } catch { if generation == token { self.error = error.localizedDescription } }
    }
}

nonisolated enum ArtworkDecoder {
    static func image(_ data: Data, pixels: Int) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateThumbnailAtIndex(source, 0,
                [kCGImageSourceCreateThumbnailFromImageAlways: true,
                 kCGImageSourceThumbnailMaxPixelSize: pixels,
                 kCGImageSourceCreateThumbnailWithTransform: true] as CFDictionary) else { return nil }
        return UIImage(cgImage: image)
    }
}

@MainActor final class ArtworkStore {
    private let api: APIClient
    private let disk: ArtworkDiskCache
    private let cache = NSCache<NSString, UIImage>()
    private var pending: [String: Task<UIImage?, Never>] = [:]
    private var cachedAt: [String: Date] = [:]
    private var generation = UUID()
    init(api: APIClient, disk: ArtworkDiskCache = ArtworkDiskCache()) { self.api = api; self.disk = disk; cache.totalCostLimit = 24 * 1024 * 1024; cache.countLimit = 160 }
    func cachedImage(_ path: String) -> UIImage? {
        guard let date = cachedAt[path], Date().timeIntervalSince(date) < 86400 else {
            cache.removeObject(forKey: path as NSString); cachedAt[path] = nil; return nil
        }
        return cache.object(forKey: path as NSString)
    }
    func image(_ path: String) async -> UIImage? {
        if let image = cachedImage(path) { return image }
        if let task = pending[path] {
            let token = generation
            let image = await task.value
            return token == generation ? image : nil
        }
        let token = generation
        let task = Task { [api, disk] in
            let scope = await api.artworkCacheScope()
            if let scope, let data = await disk.read(scope: scope, path: path),
               let image = await Task.detached(priority: .utility, operation: { ArtworkDecoder.image(data, pixels: 384) }).value { return image }
            guard let data = try? await api.artwork(path: path) else { return nil as UIImage? }
            let image = await Task.detached(priority: .utility) { ArtworkDecoder.image(data, pixels: 384) }.value
            if let scope, let thumbnail = image?.jpegData(compressionQuality: 0.85), !Task.isCancelled {
                await disk.write(thumbnail, scope: scope, path: path)
            }
            return image
        }
        pending[path] = task
        let image = await task.value
        guard token == generation else { return nil }
        pending[path] = nil
        if let image { cachedAt[path] = Date(); cache.setObject(image, forKey: path as NSString, cost: Int(image.size.width * image.size.height * 4)) }
        return image
    }
    func clearPersistentCache() async { clear(); await disk.clear() }
    func clear() { generation = UUID(); pending.values.forEach { $0.cancel() }; pending.removeAll(); cachedAt.removeAll(); cache.removeAllObjects() }
}

/// Only requested thumbnails are retained. Library membership always comes from the server.
actor ArtworkDiskCache {
    private let root: URL
    private let limit = 100 * 1024 * 1024
    init(root: URL? = nil) {
        self.root = root ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("ArrifyArtwork-v1", isDirectory: true)
    }
    private func file(scope: String, path: String) -> URL {
        let key = SHA256.hash(data: Data((scope + "\n" + path).utf8)).map { String(format: "%02x", $0) }.joined()
        return root.appendingPathComponent(key)
    }
    func read(scope: String, path: String) -> Data? {
        let url = file(scope: scope, path: path)
        guard let values = try? url.resourceValues(forKeys: [.creationDateKey]),
              let created = values.creationDate, Date().timeIntervalSince(created) < 86400,
              let data = try? Data(contentsOf: url) else { try? FileManager.default.removeItem(at: url); return nil }
        try? FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: url.path)
        return data
    }
    func write(_ data: Data, scope: String, path: String) {
        guard data.count <= limit else { return }
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let url = file(scope: scope, path: path)
        try? FileManager.default.removeItem(at: url)
        try? data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        let files = (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey])) ?? []
        let entries = files.compactMap { url -> (URL, Int, Date)? in
            guard let v = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey]) else { return nil }
            return (url, v.fileSize ?? 0, v.contentModificationDate ?? .distantPast)
        }.sorted { $0.2 < $1.2 }
        var size = entries.reduce(0) { $0 + $1.1 }
        for entry in entries where size > limit { try? FileManager.default.removeItem(at: entry.0); size -= entry.1 }
    }
    func clear() { try? FileManager.default.removeItem(at: root) }
}
