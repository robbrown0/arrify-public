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
    typealias VolumeEvent = @Sendable (Float?, Float?, Date) -> Void
    typealias ObserveVolume = @MainActor (@escaping VolumeEvent) -> NSKeyValueObservation?
    private(set) var capturing = false
    private(set) var records: [String] = []
    private var lastSnapshot: String?
    private var expiry: Task<Void, Never>?
    @ObservationIgnored private var volumeObservation: NSKeyValueObservation?
    private var captureGeneration: UUID?
    private let readSystemVolume: @MainActor () -> Float
    private let observeVolume: ObserveVolume
    private var context: CaptureContext?
    private var controls: [WeakControl] = []
    private final class WeakControl {
        weak var view: MPVolumeView?
        let id = UUID().uuidString
        let location: String
        init(_ view: MPVolumeView, location: String) { self.view = view; self.location = location }
    }
    private final class CaptureContext {
        weak var player: AVPlayer?
        weak var volumeView: MPVolumeView?
        let localName: String
        let displayedName: String
        let remoteOwner: Bool
        init(player: AVPlayer, volumeView: MPVolumeView?, localName: String, displayedName: String, remoteOwner: Bool) {
            self.player = player; self.volumeView = volumeView; self.localName = localName
            self.displayedName = displayedName; self.remoteOwner = remoteOwner
        }
    }
    struct VolumeChange: Codable {
        let observedAt: Date
        let observedAtSeconds: Double
        let oldValue: Float?
        let newValue: Float?
    }
    struct NativeSlider: Codable {
        let value: Float?
        let minimumValue: Float?
        let maximumValue: Float?
        let tracking: Bool
        let enabled: Bool
        let hidden: Bool
        let frame: String
        let bounds: String
        let trackRect: String
    }
    struct NativeControl: Codable {
        let id: String
        let location: String
        let attachedToWindow: Bool
        let hidden: Bool
        let enabled: Bool
        let bounds: String
        let sliders: [NativeSlider]
    }
    init(readSystemVolume: @escaping @MainActor () -> Float = { AVAudioSession.sharedInstance().outputVolume },
         observeVolume: ObserveVolume? = nil) {
        self.readSystemVolume = readSystemVolume
        self.observeVolume = observeVolume ?? { handler in
            AVAudioSession.sharedInstance().observe(\.outputVolume, options: [.old, .new]) { _, change in
                handler(change.oldValue, change.newValue, Date())
            }
        }
    }
    /// Weak, read-only registration; no volume value, frame or audio-session mutation.
    func registerVolumeView(_ view: MPVolumeView, location: String) {
        controls.removeAll { $0.view == nil }
        guard !controls.contains(where: { $0.view === view }) else { return }
        controls.append(WeakControl(view, location: location))
        if controls.count > 16 { controls.removeFirst(controls.count - 16) }
    }
    private static func finite(_ value: Float?) -> Float? {
        guard let value, value.isFinite else { return nil }
        return value
    }
    private func nativeControls() -> [NativeControl] {
        controls.removeAll { $0.view == nil }
        return controls.compactMap { control in
            guard let view = control.view else { return nil }
            var sliders: [NativeSlider] = []
            func inspect(_ child: UIView) {
                guard sliders.count < 8 else { return }
                if let slider = child as? UISlider {
                    sliders.append(NativeSlider(value: Self.finite(slider.value), minimumValue: Self.finite(slider.minimumValue),
                        maximumValue: Self.finite(slider.maximumValue), tracking: slider.isTracking, enabled: slider.isEnabled,
                        hidden: slider.isHidden, frame: NSStringFromCGRect(slider.frame), bounds: NSStringFromCGRect(slider.bounds),
                        trackRect: NSStringFromCGRect(slider.trackRect(forBounds: slider.bounds))))
                }
                for nested in child.subviews { inspect(nested) }
            }
            inspect(view)
            return NativeControl(id: control.id, location: control.location, attachedToWindow: view.window != nil,
                hidden: view.isHidden, enabled: view.isUserInteractionEnabled, bounds: NSStringFromCGRect(view.bounds), sliders: sliders)
        }
    }
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
        let volumeViewWirelessRouteActive: Bool?
        let volumeViewAttachedToWindow: Bool
        let displayedLocalOutput: String
        let displayedOutput: String
        let observingRemoteOwner: Bool
        // Distinct observations, never substituted for a receiver's confirmed volume.
        let sessionOutputVolume: Float?
        let playerVolume: Float?
        let nativeVolumeControls: [NativeControl]
    }
    struct Entry: Codable {
        let timestamp: Date
        let timestampSeconds: Double
        let trigger: String
        let routeChangeReason: UInt?
        let previousOutputs: [RoutePortDetails]?
        let snapshot: Snapshot
        let volumeChange: VolumeChange?
    }
    func start() {
        clear(); capturing = true
        let generation = UUID(); captureGeneration = generation
        volumeObservation = observeVolume { [weak self] old, new, observedAt in
            Task { @MainActor [weak self] in
                guard let self, self.capturing, self.captureGeneration == generation,
                      let context = self.context, let player = context.player else { return }
                self.record(trigger: "system-volume-change", player: player, volumeView: context.volumeView,
                    localName: context.localName, displayedName: context.displayedName, remoteOwner: context.remoteOwner,
                    volumeChange: VolumeChange(observedAt: observedAt, observedAtSeconds: observedAt.timeIntervalSince1970,
                        oldValue: Self.finite(old), newValue: Self.finite(new)))
            }
        }
        expiry = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(600)) } catch { return }
            self?.stop()
        }
    }
    func stop() {
        capturing = false; captureGeneration = nil; context = nil
        volumeObservation?.invalidate(); volumeObservation = nil
        expiry?.cancel(); expiry = nil
    }
    func clear() { stop(); records = []; lastSnapshot = nil }
    func record(trigger: String, player: AVPlayer, volumeView: MPVolumeView?,
                localName: String, displayedName: String, remoteOwner: Bool,
                reason: UInt? = nil, previousOutputs: [RoutePortDetails]? = nil, volumeChange: VolumeChange? = nil) {
        guard capturing else { return }
        context = CaptureContext(player: player, volumeView: volumeView, localName: localName, displayedName: displayedName, remoteOwner: remoteOwner)
        if let volumeView { registerVolumeView(volumeView, location: "expanded") }
        let session = AVAudioSession.sharedInstance()
        let snapshot = Snapshot(category: session.category.rawValue, mode: session.mode.rawValue,
            categoryOptions: session.categoryOptions.rawValue, routeSharingPolicy: session.routeSharingPolicy.rawValue,
            outputs: session.currentRoute.outputs.map(RoutePortDetails.init),
            selectedOutputDataSource: session.outputDataSource.map(RoutePortDetails.DataSource.init),
            playerAllowsExternalPlayback: player.allowsExternalPlayback, playerIsExternalPlaybackActive: player.isExternalPlaybackActive,
            playerTimeControlStatus: player.timeControlStatus.rawValue, volumeViewWirelessRouteActive: volumeView?.isWirelessRouteActive,
            volumeViewAttachedToWindow: volumeView?.window != nil,
            displayedLocalOutput: localName, displayedOutput: displayedName, observingRemoteOwner: remoteOwner,
            sessionOutputVolume: Self.finite(readSystemVolume()), playerVolume: Self.finite(player.volume), nativeVolumeControls: nativeControls())
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]; encoder.dateEncodingStrategy = .iso8601
        guard let snapshotData = try? encoder.encode(snapshot), let fingerprint = String(data: snapshotData, encoding: .utf8) else { return }
        if trigger == "reconcile", fingerprint == lastSnapshot { return }
        lastSnapshot = fingerprint
        let timestamp = Date()
        let entry = Entry(timestamp: timestamp, timestampSeconds: timestamp.timeIntervalSince1970,
            trigger: trigger, routeChangeReason: reason, previousOutputs: previousOutputs, snapshot: snapshot, volumeChange: volumeChange)
        guard let data = try? encoder.encode(entry), let text = String(data: data, encoding: .utf8) else { return }
        records.append(text)
        if records.count > 120 { records.removeFirst(records.count - 120) }
    }
    var exportText: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return "Arrify selected-output and volume diagnostics \(version) (\(build))\n\(UIDevice.current.model); iOS \(UIDevice.current.systemVersion)\nLocal user-requested capture; raw names, port UIDs and observed volume levels included.\n" + records.joined(separator: "\n")
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
                Text("Records selected route names, port IDs, observed volume levels, control geometry, session settings and player flags for up to 10 minutes. Stays on this device until you share it. No audio or login details are recorded.").font(.footnote)
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
