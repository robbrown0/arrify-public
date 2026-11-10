import SwiftUI
@preconcurrency import AVFoundation
@preconcurrency import MediaPlayer

struct RoutePortDetails: Codable, Sendable {
    struct DataSource: Codable, Sendable {
        let id: Int
        let name: String
        init(_ source: AVAudioSessionDataSourceDescription) { id = source.dataSourceID.intValue; name = source.dataSourceName }
    }
    struct Channel: Codable, Sendable {
        let name: String
        let number: Int
        let owningPortUID: String
        init(_ channel: AVAudioSessionChannelDescription) {
            name = channel.channelName; number = channel.channelNumber; owningPortUID = channel.owningPortUID
        }
    }
    let portName: String
    let portType: String
    let uid: String
    let selectedDataSource: DataSource?
    let channels: [Channel]
    init(_ port: AVAudioSessionPortDescription) {
        portName = port.portName; portType = port.portType.rawValue; uid = port.uid
        selectedDataSource = port.selectedDataSource.map(DataSource.init)
        channels = (port.channels ?? []).map(Channel.init)
    }
}

/// Explicit, local, bounded capture. No network upload, credentials, URLs or song metadata.
@MainActor @Observable final class AudioRouteDiagnostics {
    private(set) var capturing = false
    private(set) var records: [String] = []
    private var lastSnapshot: String?
    private var expiry: Task<Void, Never>?
    struct Snapshot: Codable {
        let category: String
        let mode: String
        let categoryOptions: UInt
        let routeSharingPolicy: UInt
        let outputs: [RoutePortDetails]
        let selectedOutputDataSource: RoutePortDetails.DataSource?
        let playerAllowsExternalPlayback: Bool
        let playerIsExternalPlaybackActive: Bool
        let playerTimeControlStatus: Int
        let volumeViewWirelessRouteActive: Bool
        let displayedLocalOutput: String
        let displayedOutput: String
        let observingRemoteOwner: Bool
    }
    struct Entry: Codable {
        let timestamp: Date
        let trigger: String
        let routeChangeReason: UInt?
        let previousOutputs: [RoutePortDetails]?
        let snapshot: Snapshot
    }
    func start() {
        clear(); capturing = true
        expiry = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(600)) } catch { return }
            self?.stop()
        }
    }
    func stop() { capturing = false; expiry?.cancel(); expiry = nil }
    func clear() { stop(); records = []; lastSnapshot = nil }
    func record(trigger: String, player: AVPlayer, volumeView: MPVolumeView,
                localName: String, displayedName: String, remoteOwner: Bool,
                reason: UInt? = nil, previousOutputs: [RoutePortDetails]? = nil) {
        guard capturing else { return }
        let session = AVAudioSession.sharedInstance()
        let snapshot = Snapshot(category: session.category.rawValue, mode: session.mode.rawValue,
            categoryOptions: session.categoryOptions.rawValue, routeSharingPolicy: session.routeSharingPolicy.rawValue,
            outputs: session.currentRoute.outputs.map(RoutePortDetails.init),
            selectedOutputDataSource: session.outputDataSource.map(RoutePortDetails.DataSource.init),
            playerAllowsExternalPlayback: player.allowsExternalPlayback, playerIsExternalPlaybackActive: player.isExternalPlaybackActive,
            playerTimeControlStatus: player.timeControlStatus.rawValue, volumeViewWirelessRouteActive: volumeView.isWirelessRouteActive,
            displayedLocalOutput: localName, displayedOutput: displayedName, observingRemoteOwner: remoteOwner)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]; encoder.dateEncodingStrategy = .iso8601
        guard let snapshotData = try? encoder.encode(snapshot), let fingerprint = String(data: snapshotData, encoding: .utf8) else { return }
        if trigger == "reconcile", fingerprint == lastSnapshot { return }
        lastSnapshot = fingerprint
        let entry = Entry(timestamp: Date(), trigger: trigger, routeChangeReason: reason, previousOutputs: previousOutputs, snapshot: snapshot)
        guard let data = try? encoder.encode(entry), let text = String(data: data, encoding: .utf8) else { return }
        records.append(text)
        if records.count > 120 { records.removeFirst(records.count - 120) }
    }
    var exportText: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "Arrify selected-output diagnostics \(version) (\(build))\n\(UIDevice.current.model); iOS \(UIDevice.current.systemVersion)\nLocal user-requested capture; raw names and port UIDs included.\n" + records.joined(separator: "\n")
    }
}

struct AudioRouteDiagnosticsView: View {
    let playback: PlaybackCoordinator
    var body: some View {
        Form {
            Section("Selected output") {
                Text(playback.displayOutputName).textSelection(.enabled)
                AirPlayPicker(onDismiss: { playback.outputSelectionChanged() }, onPresent: { playback.refreshOutput(trigger: "picker-presented") }).frame(height: 44)
            }
            Section("Capture") {
                Text("Records selected route names, port IDs, session settings and player flags for up to 10 minutes. Stays on this device until you share it. No audio or login details are recorded.").font(.footnote)
                Button(playback.routeDiagnostics.capturing ? "Stop capture" : "Start capture") {
                    if playback.routeDiagnostics.capturing { playback.routeDiagnostics.stop() }
                    else { playback.routeDiagnostics.start(); playback.refreshOutput(trigger: "capture-start") }
                }
                Button("Capture current output") { playback.refreshOutput(trigger: "manual") }
                    .disabled(!playback.routeDiagnostics.capturing)
                Text("\(playback.routeDiagnostics.records.count) snapshots")
                ShareLink("Share diagnostic report", item: playback.routeDiagnostics.exportText)
                    .disabled(playback.routeDiagnostics.records.isEmpty)
                Button("Clear capture", role: .destructive) { playback.routeDiagnostics.clear() }
            }
        }.navigationTitle("Audio output diagnostics")
    }
}
