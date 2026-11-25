import Foundation
import AVFoundation

@MainActor func probe(_ name: String, url: URL) async -> Bool {
    let item = AVPlayerItem(url: url)
    let player = AVPlayer(playerItem: item)
    let deadline = Date().addingTimeInterval(12)
    while item.status == .unknown && Date() < deadline {
        try? await Task.sleep(for: .milliseconds(50))
    }
    print("\(name): status=\(item.status.rawValue) duration=\(item.duration.seconds)")
    var error = item.error as NSError?
    for _ in 0..<4 {
        guard let current = error else { break }
        print("  error: \(current.domain) \(current.code)")
        error = current.userInfo[NSUnderlyingErrorKey] as? NSError
    }
    let ready = item.status == .readyToPlay && item.duration.seconds > 0
    player.replaceCurrentItem(with: nil)
    return ready
}
let flac = await probe("extensionless", url: URL(string: "http://127.0.0.1:18742/api/v1/stream/1?grant=fixture")!)
let flacExtension = await probe("extension", url: URL(string: "http://127.0.0.1:18742/api/v1/stream/1.flac?grant=fixture")!)
let incorrect = await probe("mp3-wrong-mp4-type", url: URL(string: "http://127.0.0.1:18742/mp3-wrong?grant=fixture")!)
let corrected = await probe("mp3-correct-type", url: URL(string: "http://127.0.0.1:18742/mp3-correct?grant=fixture")!)
guard flac && flacExtension && !incorrect && corrected else { exit(1) }
print("PASS: incorrect MIME reproduces failure; corrected MIME opens; original FLAC remains playable.")
