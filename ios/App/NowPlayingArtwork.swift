import UIKit
@preconcurrency import MediaPlayer

/// MediaPlayer may request artwork on its own worker queue.
/// Keep this synchronous callback independent of the player and UI actor.
enum NowPlayingArtwork {
    nonisolated static func make(image: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: image.size) { @Sendable _ in image }
    }
}
