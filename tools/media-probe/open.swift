import Foundation
import AVFoundation

@MainActor func probe(_ name: String, url: URL) async {
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
    player.replaceCurrentItem(with: nil)
}
await probe("extensionless", url: URL(string: "http://127.0.0.1:18742/api/v1/stream/1?grant=fixture")!)
await probe("extension", url: URL(string: "http://127.0.0.1:18742/api/v1/stream/1.flac?grant=fixture")!)
await probe("mp3-wrong-mp4-type", url: URL(string: "http://127.0.0.1:18742/mp3-wrong?grant=fixture")!)
await probe("mp3-correct-type", url: URL(string: "http://127.0.0.1:18742/mp3-correct?grant=fixture")!)
let bytes = Data(base64Encoded: try String(contentsOfFile: "tools/media-probe/silence.flac.base64", encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines))!
let file = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".flac")
try bytes.write(to: file)
await probe("local", url: file)
try FileManager.default.removeItem(at: file)
