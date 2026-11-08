import UIKit
@preconcurrency import MediaPlayer

/// MediaPlayer requests immutable artwork on worker queues. Resize to the
/// requested bounds without capturing a player or crossing the UI actor.
enum NowPlayingArtwork {
    nonisolated static func make(image: UIImage) -> MPMediaItemArtwork {
        MPMediaItemArtwork(boundsSize: image.size) { @Sendable size in
            guard size.width.isFinite, size.height.isFinite, size.width > 0, size.height > 0,
                  image.size.width > 0, image.size.height > 0, let source = image.cgImage else { return image }
            let factor = min(1, min(size.width / image.size.width, size.height / image.size.height))
            guard factor < 1 else { return image }
            let width = max(1, Int(image.size.width * factor)), height = max(1, Int(image.size.height * factor))
            guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return image }
            context.interpolationQuality = .high
            context.draw(source, in: CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
            guard let scaled = context.makeImage() else { return image }
            return UIImage(cgImage: scaled)
        }
    }
}
