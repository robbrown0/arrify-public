import UIKit
import ArrifyCore

/// Inline thumbnail: the Live Activity extension cannot download cover art.
/// Keep the encoded image below 2 KB, leaving room for metadata and attributes.
@MainActor enum ActivityThumbnail {
    static func load(api: APIClient, trackID: Int) async -> String? {
        guard let data = try? await api.artwork(trackID), let image = ArtworkDecoder.image(data, pixels: 96) else { return nil }
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        for size in [44, 32, 24] {
            let thumb = UIGraphicsImageRenderer(size: CGSize(width: CGFloat(size), height: CGFloat(size)), format: format).image { _ in
                image.draw(in: CGRect(x: 0, y: 0, width: CGFloat(size), height: CGFloat(size)))
            }
            if let bytes = thumb.jpegData(compressionQuality: 0.35), bytes.count <= 1400 { return bytes.base64EncodedString() }
        }
        return nil
    }
}
