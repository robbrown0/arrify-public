@preconcurrency import AVFoundation
import ArrifyCore

/// Session activation is the system's authority on whether explicit playback may resume.
@MainActor protocol PlaybackAudioSession {
    func activate() async throws
    func deactivate() async throws
    var outputName: String { get }
    var usesAirPlay: Bool { get }
    func invalidate()
}

extension PlaybackAudioSession {
    var outputName: String { "Native app" }
    var usesAirPlay: Bool { false }
    func invalidate() {}
}

@MainActor final class SystemPlaybackAudioSession: PlaybackAudioSession {
    private let worker: AudioSessionWorker
    init(activate: (@Sendable () throws -> Void)? = nil, deactivate: (@Sendable () throws -> Void)? = nil) {
        worker = AudioSessionWorker(activation: activate, deactivation: deactivate)
    }
    private var active = false
    private var activation: Task<Void, Error>?
    private var generation = 0
    var usesAirPlay: Bool { AVAudioSession.sharedInstance().currentRoute.outputs.contains { $0.portType == .airPlay } }
    func invalidate() { active = false; generation += 1; activation = nil }

    var outputName: String {
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs
        return SelectedOutputNames.display(outputs.map { port in
            let fallback: String
            switch port.portType {
            case .airPlay: fallback = "AirPlay"
            case .bluetoothA2DP, .bluetoothHFP, .bluetoothLE: fallback = "Bluetooth"
            case .carAudio: fallback = "Car audio"
            case .builtInSpeaker: fallback = "Speaker"
            case .builtInReceiver: fallback = "Receiver"
            case .headphones: fallback = "Headphones"
            case .usbAudio: fallback = "USB audio"
            case .HDMI: fallback = "HDMI"
            default: fallback = "Audio output"
            }
            return (name: port.portName, fallback: fallback)
        })
    }
    func activate() async throws {
        if active { return }
        let epoch = generation
        let task: Task<Void, Error>
        if let pending = activation { task = pending }
        else {
            let worker = worker
            task = Task { try await worker.activate() }
            activation = task
        }
        defer { if generation == epoch { activation = nil } }
        try await task.value
        guard generation == epoch else { throw CancellationError() }
        active = true
    }
    func deactivate() async throws {
        invalidate()
        try await worker.deactivate()
    }
}

/// Blocking system activation runs serially off the UI actor.
private actor AudioSessionWorker {
    let activation: (@Sendable () throws -> Void)?
    let deactivation: (@Sendable () throws -> Void)?
    init(activation: (@Sendable () throws -> Void)?, deactivation: (@Sendable () throws -> Void)?) {
        self.activation = activation; self.deactivation = deactivation
    }
    func activate() throws {
        let started = ProcessInfo.processInfo.systemUptime
        PlaybackDiagnostics.writer.record("activation-start")
        defer { PlaybackDiagnostics.writer.record("activation-finish ms=\(Int((ProcessInfo.processInfo.systemUptime - started) * 1000))") }
        if let activation { try activation(); return }
        let session = AVAudioSession.sharedInstance()
        if session.category != .playback || session.mode != .default || session.routeSharingPolicy != .longFormAudio {
            try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
        }
        try session.setActive(true)
    }
    func deactivate() throws {
        if let deactivation { try deactivation(); return }
        try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
