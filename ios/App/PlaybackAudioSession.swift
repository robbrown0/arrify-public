@preconcurrency import AVFoundation

/// Session activation is the system's authority on whether explicit playback may resume.
@MainActor protocol PlaybackAudioSession {
    func activate() throws
    func deactivate() throws
    var outputName: String { get }
}

extension PlaybackAudioSession { var outputName: String { "Native app" } }

@MainActor struct SystemPlaybackAudioSession: PlaybackAudioSession {
    var outputName: String {
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs
        let names = outputs.map(\.portName).joined(separator: ", ")
        let prefix = outputs.contains { $0.portType == .airPlay } ? "AirPlay · " : outputs.contains { $0.portType == .carAudio } ? "CarPlay · " : ""
        return String((prefix + (names.isEmpty ? "Native app" : names)).prefix(80))
    }
    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
        try session.setActive(true)
    }
    func deactivate() throws {
        try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
