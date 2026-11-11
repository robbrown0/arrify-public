import SwiftUI
import UIKit

/// Only fixed event names and numeric/boolean measurements belong in this log.
/// No titles, track IDs, endpoints, credentials, or notification userInfo are recorded.
@MainActor enum PlaybackDiagnostics {
    static let writer = DiagnosticWriter(directory: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("PlaybackDiagnostics"))
    static var enabled: Bool { UserDefaults.standard.bool(forKey: "playbackDiagnostics") }
    static func record(_ event: String) { if enabled { writer.record(event) } }
    static func configure(active: Bool) {
        writer.monitor(enabled: enabled && active)
    }
}

/// All mutable state and disk I/O are confined to queue, independently of the UI.
final class DiagnosticWriter: @unchecked Sendable {
    private let queue = DispatchQueue(label: "arrify.diagnostics", qos: .utility)
    private let directory: URL
    private let limit: Int
    private let pendingLock = NSLock()
    private var pendingWrites = 0
    private var timer: DispatchSourceTimer?
    private var pendingSince: TimeInterval?
    private var generation = 0
    init(directory: URL, limit: Int = 128 * 1024) { self.directory = directory; self.limit = limit }
    func record(_ event: String) {
        // A runaway layout must not create an unbounded diagnostic work queue.
        let accepted = pendingLock.withLock {
            guard pendingWrites < 64 else { return false }
            pendingWrites += 1; return true
        }
        guard accepted else { return }
        queue.async {
            self.append(event)
            self.pendingLock.withLock { self.pendingWrites -= 1 }
        }
    }
    private func append(_ event: String) {
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            var excluded = directory
            var values = URLResourceValues(); values.isExcludedFromBackup = true
            try excluded.setResourceValues(values)
            let current = directory.appendingPathComponent("current.txt")
            let previous = directory.appendingPathComponent("previous.txt")
            let bytes = Data((String(format: "%.3f", Date().timeIntervalSince1970) + " " + event.prefix(240) + "\n").utf8)
            let size = (try? current.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
            if size + bytes.count > limit {
                try? FileManager.default.removeItem(at: previous)
                if FileManager.default.fileExists(atPath: current.path) { try FileManager.default.moveItem(at: current, to: previous) }
            }
            if !FileManager.default.fileExists(atPath: current.path) { try Data().write(to: current, options: .completeFileProtectionUntilFirstUserAuthentication) }
            let file = try FileHandle(forWritingTo: current)
            defer { try? file.close() }
            try file.seekToEnd(); try file.write(contentsOf: bytes)
        } catch { /* Diagnostics must never interrupt playback. */ }
    }
    func monitor(enabled: Bool) {
        queue.async {
            self.timer?.cancel(); self.timer = nil; self.pendingSince = nil
            self.generation += 1
            guard enabled else { return }
            self.append("monitor-start")
            let timer = DispatchSource.makeTimerSource(queue: self.queue)
            timer.schedule(deadline: .now() + 2, repeating: 2, leeway: .milliseconds(250))
            timer.setEventHandler { [weak self] in
                guard let self else { return }
                let now = ProcessInfo.processInfo.systemUptime
                if let pending = self.pendingSince {
                    self.append("main-unresponsive seconds=\(Int(now - pending))")
                    return
                }
                self.pendingSince = now
                let generation = self.generation
                DispatchQueue.main.async {
                    self.queue.async {
                        guard self.generation == generation else { return }
                        if let pending = self.pendingSince, ProcessInfo.processInfo.systemUptime - pending >= 2 { self.append("main-recovered") }
                        self.pendingSince = nil
                    }
                }
            }
            self.timer = timer; timer.resume()
        }
    }
    func export() async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                do {
                    var data = Data("Arrify playback diagnostics; no audio or account information\n".utf8)
                    for name in ["previous.txt", "current.txt"] {
                        if let part = try? Data(contentsOf: self.directory.appendingPathComponent(name)) { data.append(part) }
                    }
                    let url = self.directory.appendingPathComponent("Arrify-diagnostics.txt")
                    try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
                    try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
                    continuation.resume(returning: url)
                } catch { continuation.resume(throwing: error) }
            }
        }
    }
    func clear() async {
        await withCheckedContinuation { continuation in
            queue.async { try? FileManager.default.removeItem(at: self.directory); continuation.resume() }
        }
    }
}

struct PlaybackDiagnosticsView: View {
    @AppStorage("playbackDiagnostics") private var enabled = false
    @Environment(\.scenePhase) private var phase
    @State private var exportURL: URL?
    @State private var error: String?
    var body: some View {
        Form {
            Toggle("Record playback diagnostics", isOn: $enabled)
                .onChange(of: enabled) {
                    PlaybackDiagnostics.configure(active: phase == .active)
                    PlaybackDiagnostics.record("recording-enabled")
                }
            Text("Turn on before playing. After a freeze, reopen Arrify, turn recording off, then prepare and share the report. Logs stay on this device and contain timing and UI events, not song titles, audio, passwords, or server addresses. Up to 256 KB of recent events are kept; the export uses up to another 256 KB.")
                .font(.footnote)
            Button("Prepare diagnostic report") {
                Task { do { exportURL = try await PlaybackDiagnostics.writer.export(); error = nil } catch { self.error = "Could not prepare diagnostics. Please try again." } }
            }.accessibilityIdentifier("exportPlaybackDiagnostics")
            if let exportURL { ShareLink("Share diagnostic report", item: exportURL) }
            Button("Delete diagnostics", role: .destructive) {
                enabled = false
                PlaybackDiagnostics.configure(active: false)
                Task { await PlaybackDiagnostics.writer.clear(); exportURL = nil }
            }
            if let error { Text(error).foregroundStyle(.orange) }
        }.navigationTitle("Playback diagnostics")
    }
}
