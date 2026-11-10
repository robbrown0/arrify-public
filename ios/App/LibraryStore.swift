import SwiftUI
import ArrifyCore
import ImageIO

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
    private let cache = NSCache<NSString, UIImage>()
    private var pending: [String: Task<UIImage?, Never>] = [:]
    private var generation = UUID()
    init(api: APIClient) { self.api = api; cache.totalCostLimit = 24 * 1024 * 1024; cache.countLimit = 160 }
    func cachedImage(_ path: String) -> UIImage? { cache.object(forKey: path as NSString) }
    func image(_ path: String) async -> UIImage? {
        if let image = cache.object(forKey: path as NSString) { return image }
        if let task = pending[path] {
            let token = generation
            let image = await task.value
            return token == generation ? image : nil
        }
        let token = generation
        let task = Task { [api] in
            guard let data = try? await api.artwork(path: path) else { return nil as UIImage? }
            return await Task.detached(priority: .utility) { ArtworkDecoder.image(data, pixels: 384) }.value
        }
        pending[path] = task
        let image = await task.value
        guard token == generation else { return nil }
        pending[path] = nil
        if let image { cache.setObject(image, forKey: path as NSString, cost: Int(image.size.width * image.size.height * 4)) }
        return image
    }
    func clear() { generation = UUID(); pending.values.forEach { $0.cancel() }; pending.removeAll(); cache.removeAllObjects() }
}
