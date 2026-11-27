import SwiftUI
import ArrifyCore
import CryptoKit

/// Explicit full-book downloads live separately from the temporary music cache.
@MainActor @Observable final class AudiobookDownloads {
    struct Record: Codable {
        var manifest: AudiobookManifest
        var addresses: [String]
        var transferId: String
        var lastPosition: Double?
        var remoteStamp: Double?
        var finished: Bool?
    }
    private let api: APIClient
    private let configuration: @MainActor (String) -> URLSessionConfiguration
    private var root: URL?
    private var session: URLSession?
    private var delegate: BookDownloadDelegate?
    private var epoch = UUID()
    private(set) var records: [String: Record] = [:]
    private(set) var working: Set<String> = []
    private(set) var errors: [String: String] = [:]
    private var transferProgress: [String: (Int64, Int64)] = [:]
    private(set) var revision = 0
    init(api: APIClient, configuration: @escaping @MainActor (String) -> URLSessionConfiguration = { URLSessionConfiguration.background(withIdentifier:$0) }) {
        self.api = api; self.configuration = configuration
    }
    func configure() async {
        guard let scope = try? await api.sessionScope() else { return }
        let key = SHA256.hash(data:Data(scope.utf8)).map { String(format:"%02x", $0) }.joined()
        let directory = URL.applicationSupportDirectory.appending(path:"Audiobooks/" + key)
        if root == directory { return }
        session?.invalidateAndCancel(); epoch = UUID(); let ticket = epoch
        root = directory; records = [:]; errors = [:]; working = []; transferProgress = [:]
        do {
            try FileManager.default.createDirectory(at:directory, withIntermediateDirectories:true,
                attributes:[.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication])
            var url = directory; var values = URLResourceValues(); values.isExcludedFromBackup = true; try url.setResourceValues(values)
            for file in try FileManager.default.contentsOfDirectory(at:directory, includingPropertiesForKeys:nil) where file.pathExtension == "json" {
                if let data = try? Data(contentsOf:file), let value = try? JSONDecoder().decode(Record.self, from:data) { records[value.manifest.book.id] = value }
            }
            let identifier = "com.robnets.arrify.books." + key
            let handler = BookDownloadDelegate(root:directory, identifier:identifier, progress:{ [weak self] name, received, expected in
                Task { @MainActor in
                    let parts = name.split(separator:"/"); guard parts.count == 3 else { return }; let book = String(parts[0])
                    guard let self, self.epoch == ticket, self.records[book]?.transferId == String(parts[1]) else { return }
                    self.transferProgress[book] = (received, expected)
                }
            }) { [weak self] name, error in
                Task { @MainActor in
                    let parts = name.split(separator:"/"); guard parts.count == 3 else { return }; let book = String(parts[0])
                    guard let self, self.epoch == ticket, self.records[book]?.transferId == String(parts[1]) else { return }
                    self.working.remove(book); self.errors[book] = error
                    self.transferProgress.removeValue(forKey:book)
                    self.revision += 1
                    if error == nil { await self.schedule(book) }
                }
            }
            delegate = handler
            let config = configuration(identifier)
            config.sessionSendsLaunchEvents = true; config.isDiscretionary = false
            config.waitsForConnectivity = true; config.httpMaximumConnectionsPerHost = 2
            config.timeoutIntervalForResource = 3600; config.httpCookieStorage = nil; config.urlCache = nil
            let queue = OperationQueue(); queue.maxConcurrentOperationCount = 1
            let value = URLSession(configuration:config, delegate:handler, delegateQueue:queue); session = value
            let tasks = await value.allTasks
            guard epoch == ticket else { return }
            working = Set(tasks.compactMap { $0.taskDescription?.split(separator:"/").first.map(String.init) })
        } catch { errors["storage"] = "Downloads are unavailable. Free some device storage and try again." }
    }
    private func partName(_ part: AudiobookPart) -> String {
        let ext: String
        switch part.mime { case "audio/mpeg": ext = "mp3"; case "audio/flac", "audio/x-flac": ext = "flac"; case "audio/wav", "audio/x-wav": ext = "wav"; case "audio/aac": ext = "aac"; case "audio/ogg": ext = "ogg"; default: ext = "m4b" }
        return String(part.index) + "." + ext
    }
    private func path(_ id: String, _ part: AudiobookPart) -> URL? {
        guard let record = records[id] else { return nil }
        return root?.appending(path:id + "/" + record.transferId + "/" + partName(part))
    }
    func file(_ book: Audiobook, part: AudiobookPart) -> URL? {
        guard let record = records[book.id], record.manifest.book.sourceRevision == book.sourceRevision,
              let file = path(book.id, part),
              let bytes = (try? file.resourceValues(forKeys:[.fileSizeKey]))?.fileSize, bytes > 0,
              part.sizeBytes == nil || part.sizeBytes == 0 || Int64(bytes) == part.sizeBytes else { return nil }
        return file
    }
    func complete(_ id: String) -> Bool {
        guard let book = records[id]?.manifest.book, let parts = book.parts, !parts.isEmpty else { return false }
        return parts.allSatisfy { file(book, part:$0) != nil }
    }
    func completedParts(_ id: String) -> Int {
        guard let book = records[id]?.manifest.book else { return 0 }
        return book.parts?.filter { file(book, part:$0) != nil }.count ?? 0
    }
    func fraction(_ id: String) -> Double {
        guard let book = records[id]?.manifest.book, let parts = book.parts, !parts.isEmpty else { return 0 }
        if complete(id) { return 1 }
        let progress = transferProgress[id]
        let total = parts.reduce(Int64(0)) { $0 + ($1.sizeBytes ?? 0) }
        if total > 0 {
            let completed = parts.filter { file(book, part:$0) != nil }.reduce(Int64(0)) { $0 + ($1.sizeBytes ?? 0) }
            return min(0.99, Double(completed+(progress?.0 ?? 0))/Double(total))
        }
        let current = (progress?.1 ?? 0) > 0 ? Double(progress?.0 ?? 0)/Double(progress!.1) : 0
        return min(0.99,(Double(completedParts(id))+current)/Double(parts.count))
    }
    private func persist(_ id: String) throws {
        guard let root, let record = records[id] else { return }
        try JSONEncoder().encode(record).write(to:root.appending(path:id + ".json"), options:[.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    func download(_ id: String) async {
        await configure(); guard root != nil else { return }
        let ticket = epoch
        do {
            let manifest = try await api.audiobookDownload(id)
            var urls: [String] = []
            for part in manifest.book.parts ?? [] {
                guard let path = part.url else { throw APIError.invalidEndpoint }
                urls.append(try await self.api.audiobookMediaURL(path).absoluteString)
            }
            guard epoch == ticket, let root else { return }
            let size = (manifest.book.parts ?? []).reduce(Int64(0)) { $0 + ($1.sizeBytes ?? 0) }
            let storage = try root.resourceValues(forKeys:[.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey])
            let capacity = storage.volumeAvailableCapacityForImportantUsage ?? Int64(storage.volumeAvailableCapacity ?? 0)
            guard capacity > min(size, Int64.max - 256*1024*1024) + 256*1024*1024 else {
                errors[id] = "There isn’t enough free space to download this book."; return
            }
            if let old = records[id], old.manifest.book.sourceRevision != manifest.book.sourceRevision {
                await remove(id)
            }
            let old = records[id]
            if old?.addresses != urls || old?.manifest.lease != manifest.lease {
                // Resume data is opaque. A changed origin/grant starts a fresh transfer.
                if let transfer = old?.transferId, let files = try? FileManager.default.contentsOfDirectory(at:root.appending(path:id + "/" + transfer), includingPropertiesForKeys:nil) {
                    for file in files where file.pathExtension == "resume" { try? FileManager.default.removeItem(at:file) }
                }
            }
            records[id] = Record(manifest:manifest, addresses:urls, transferId:old?.transferId ?? UUID().uuidString.lowercased(),
                lastPosition:old?.lastPosition, remoteStamp:old?.remoteStamp, finished:old?.finished); errors[id] = nil
            try persist(id); await schedule(id)
        } catch where RequestCancellation.matches(error) {} catch {
            if epoch == ticket { errors[id] = "Download paused. Your completed parts are saved. Try again when connected." }
        }
    }
    private func schedule(_ id: String) async {
        guard !working.contains(id), let record = records[id], let session, let root else { return }
        guard let part = record.manifest.book.parts?.first(where:{ file(record.manifest.book, part:$0) == nil }) else { revision += 1; return }
        guard part.index < record.addresses.count, let url = URL(string:record.addresses[part.index]) else { return }
        do {
            try FileManager.default.createDirectory(at:root.appending(path:id + "/" + record.transferId), withIntermediateDirectories:true,
                attributes:[.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication])
            let name = id + "/" + record.transferId + "/" + partName(part)
            let resume = root.appending(path:name + ".resume")
            let task: URLSessionDownloadTask
            if let data = try? Data(contentsOf:resume) { task = session.downloadTask(withResumeData:data) }
            else { task = session.downloadTask(with:url) }
            task.taskDescription = name; working.insert(id); task.resume()
        } catch { errors[id] = "Download paused. Check available device storage." }
    }
    func pause(_ id: String) async {
        guard let session, let root else { return }
        let ticket = epoch
        for task in await session.allTasks where task.taskDescription?.hasPrefix(id + "/") == true {
            if let download = task as? URLSessionDownloadTask {
                let name = task.taskDescription ?? ""
                let data = await download.cancelByProducingResumeData()
                guard epoch == ticket, let transfer = records[id]?.transferId else { return }
                if let data, name.hasPrefix(id + "/" + transfer + "/") { try? data.write(to:root.appending(path:name + ".resume"), options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication]) }
            }
        }
        guard epoch == ticket else { return }
        working.remove(id)
    }
    func remove(_ id: String) async {
        guard let root else { return }; let ticket = epoch
        if let session { for task in await session.allTasks where task.taskDescription?.hasPrefix(id + "/") == true { task.cancel() } }
        guard epoch == ticket else { return }
        records.removeValue(forKey:id); working.remove(id); errors.removeValue(forKey:id); transferProgress.removeValue(forKey:id)
        try? FileManager.default.removeItem(at:root.appending(path:id)); try? FileManager.default.removeItem(at:root.appending(path:id + ".json")); revision += 1
    }
    func clear() {
        epoch = UUID(); delegate?.invalidate(); session?.invalidateAndCancel(); session = nil; delegate = nil
        if let root { try? FileManager.default.removeItem(at:root) }
        root = nil; records = [:]; working = []; errors = [:]; transferProgress = [:]; revision += 1
    }
    func checkpoint(_ id: String, position: Double? = nil, remoteStamp: Double? = nil, finished: Bool? = nil) {
        guard records[id] != nil else { return }
        if let position { records[id]?.lastPosition = position }
        if let remoteStamp { records[id]?.remoteStamp = remoteStamp }
        if let finished { records[id]?.finished = finished }
        try? persist(id)
    }
}

/// The system calls this delegate while the UI may be suspended. Move files before
/// returning; hand observation changes back to the UI actor. Never log URLs/tokens.
private final class BookDownloadDelegate: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let root: URL
    let identifier: String
    let completion: @Sendable (String, String?) -> Void
    let progress: @Sendable (String, Int64, Int64) -> Void
    private let lock = NSLock()
    private var valid = true
    private var progressAt: [Int:TimeInterval] = [:]
    init(root: URL, identifier: String, progress: @escaping @Sendable (String, Int64, Int64) -> Void, completion: @escaping @Sendable (String, String?) -> Void) {
        self.root = root; self.identifier = identifier; self.progress = progress; self.completion = completion
    }
    func invalidate() { lock.lock(); defer { lock.unlock() }; valid = false }
    private func name(_ task: URLSessionTask) -> String? {
        guard let value = task.taskDescription, value.range(of:"^[A-Za-z0-9_-]{1,128}/[a-f0-9-]{36}/[0-9]{1,4}\\.(mp3|m4b|flac|wav|aac|ogg)$", options:.regularExpression) != nil else { return nil }; return value
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        lock.lock(); defer { lock.unlock() }
        let now = Date.timeIntervalSinceReferenceDate
        guard valid, let name = name(downloadTask), now-(progressAt[downloadTask.taskIdentifier] ?? 0) >= 0.25 else { return }
        progressAt[downloadTask.taskIdentifier] = now
        progress(name, max(0,totalBytesWritten), max(0,totalBytesExpectedToWrite))
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        lock.lock(); defer { lock.unlock() }; guard valid, let name = name(downloadTask),
            let response = downloadTask.response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else { return }
        do {
            let bytes = try location.resourceValues(forKeys:[.fileSizeKey]).fileSize ?? 0
            guard bytes > 0 else { return }
            let destination = root.appending(path:name)
            try? FileManager.default.removeItem(at:destination)
            try FileManager.default.moveItem(at:location, to:destination)
            try FileManager.default.setAttributes([.protectionKey:FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath:destination.path)
            try? FileManager.default.removeItem(at:root.appending(path:name + ".resume"))
        } catch { completion(name, "Download paused. Check device storage.") }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        lock.lock(); defer { lock.unlock() }; guard valid, let name = name(task) else { return }
        progressAt.removeValue(forKey:task.taskIdentifier)
        if let data = (error as NSError?)?.userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
            try? data.write(to:root.appending(path:name + ".resume"), options:[.atomic,.completeFileProtectionUntilFirstUserAuthentication])
        }
        let code = (task.response as? HTTPURLResponse)?.statusCode ?? 0
        let message = error == nil && (200..<300).contains(code) && FileManager.default.fileExists(atPath:root.appending(path:name).path) ? nil : "Download paused. Your completed parts are saved."
        completion(name, message)
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Task { @MainActor [identifier] in AudiobookBackgroundEvents.finish(identifier) }
    }
}

@MainActor enum AudiobookBackgroundEvents {
    private static var handlers: [String: () -> Void] = [:]
    static func receive(_ id: String, handler: @escaping () -> Void) { handlers[id] = handler }
    static func finish(_ id: String) { handlers.removeValue(forKey:id)?() }
}
@MainActor final class ArrifyApplicationDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication, handleEventsForBackgroundURLSession identifier: String, completionHandler: @escaping () -> Void) {
        AudiobookBackgroundEvents.receive(identifier, handler:completionHandler)
    }
}
