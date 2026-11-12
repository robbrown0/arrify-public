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
    func scheduleRefresh() {
        let token = generation
        Task { @MainActor [weak self] in
            guard let self, self.generation == token else { return }
            await self.refresh()
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
    var popularityPending = false
    var total = 0
    var nextOffset: Int?
    var loading = false
    var error: String?
    var updated: Date?
    private var revision: String?
    private var generation = UUID()
    init(api: APIClient, kind: String = "tracks", source: LibrarySource? = nil) { self.api = api; self.kind = kind; self.source = source }
    func refreshPopularity(query: String = "") async {
        let token = generation
        for _ in 0..<4 {
            guard popularityPending, nextOffset == nil || tracks.count <= 50 else { return }
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
            guard token == generation, !Task.isCancelled else { return }
            // Enrich existing rows without changing the selection or pagination.
            do {
                let values: [Track]
                let pending: Bool
                if let source {
                    let page = try await api.detail(source)
                    values = page.items; pending = page.popularityPending ?? false
                } else {
                    let page = try await api.catalog(query: query)
                    values = page.items; pending = page.popularityPending ?? false
                }
                guard token == generation, !Task.isCancelled else { return }
                popularityPending = pending
                let byID = Dictionary(values.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
                tracks = tracks.map { byID[$0.id] ?? $0 }
            } catch { return }
        }
    }
    func resetSearch() {
        generation = UUID(); tracks = []; entries = []; nextOffset = nil
        revision = nil; error = nil; loading = true
    }
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
                popularityPending = page.popularityPending ?? false
                item = page.item; total = page.total; nextOffset = page.nextOffset; revision = page.revision
            } else if kind == "tracks" {
                let page = try await api.catalog(query: query, offset: offset, revision: rev)
                guard token == generation, !Task.isCancelled else { return }
                tracks = more ? tracks + page.items : page.items
                popularityPending = page.popularityPending ?? false
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
    func cachedImage(_ path: String, pixels: Int = 384) -> UIImage? {
        let path = "\(pixels):\(path)"
        guard let date = cachedAt[path], Date().timeIntervalSince(date) < 86400 else {
            cache.removeObject(forKey: path as NSString); cachedAt[path] = nil; return nil
        }
        return cache.object(forKey: path as NSString)
    }
    func image(_ path: String, pixels: Int = 384) async -> UIImage? {
        let key = "\(pixels):\(path)"
        if let image = cachedImage(path, pixels: pixels) { return image }
        if let task = pending[key] {
            let token = generation
            let image = await task.value
            return token == generation ? image : nil
        }
        let token = generation
        let task = Task<UIImage?, Never> { [api, disk, weak self] in
            let scope = await api.artworkCacheScope()
            if let scope, let data = await disk.read(scope: scope, path: path),
               let image = await Task.detached(priority: .utility, operation: { ArtworkDecoder.image(data, pixels: pixels) }).value { return image }
            guard let data = try? await api.artwork(path: path) else { return nil as UIImage? }
            let image = await Task.detached(priority: .utility) { ArtworkDecoder.image(data, pixels: 2048) }.value
            if let scope, token == self?.generation, let thumbnail = image?.jpegData(compressionQuality: 0.85), !Task.isCancelled {
                await disk.write(thumbnail, scope: scope, path: path)
            }
            // Keep list thumbnails small in memory while retaining a player-sized disk cover.
            if pixels < 2048 {
                return await Task.detached(priority: .utility) { ArtworkDecoder.image(data, pixels: pixels) }.value
            }
            return image
        }
        pending[key] = task
        let image = await task.value
        guard token == generation else { return nil }
        pending[key] = nil
        if let image { cachedAt[key] = Date(); cache.setObject(image, forKey: key as NSString, cost: Int(image.size.width * image.size.height * 4)) }
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
        if root == nil {
            // v1 retained only small thumbnails; retire it rather than doubling the disk budget.
            let legacy = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("ArrifyArtwork-v1", isDirectory: true)
            try? FileManager.default.removeItem(at: legacy)
        }
        self.root = root ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("ArrifyArtwork-v2", isDirectory: true)
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
