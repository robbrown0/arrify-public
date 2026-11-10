@preconcurrency import AVFoundation
import ArrifyCore

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
    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        if session.category != .playback || session.mode != .default || session.routeSharingPolicy != .longFormAudio {
            try session.setCategory(.playback, mode: .default, policy: .longFormAudio)
        }
        try session.setActive(true)
    }
    func deactivate() throws {
        try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
