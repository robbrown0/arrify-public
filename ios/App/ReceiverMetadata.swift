import Foundation
import ArrifyCore
@preconcurrency import AVFoundation

/// Embedded bytes allow supported receivers to show covers without bearer credentials.
@MainActor enum ReceiverMetadata {
    static func make(track: Track, artwork: Data?) -> [AVMetadataItem] {
        make(title:track.title, artist:track.artist, album:track.album, artwork:artwork)
    }
    static func make(title: String, artist: String, album: String, artwork: Data?) -> [AVMetadataItem] {
        func text(_ identifier: AVMetadataIdentifier, _ value: String) -> AVMetadataItem {
            let item = AVMutableMetadataItem()
            item.identifier = identifier; item.extendedLanguageTag = "und"
            item.value = value as NSString
            return item.copy() as! AVMetadataItem
        }
        var result = [text(.commonIdentifierTitle, title), text(.commonIdentifierArtist, artist), text(.commonIdentifierAlbumName, album)]
        if let artwork {
            let item = AVMutableMetadataItem()
            item.identifier = .commonIdentifierArtwork; item.dataType = "public.jpeg"
            item.extendedLanguageTag = "und"
            item.value = artwork as NSData
            result.append(item.copy() as! AVMetadataItem)
        }
        return result
    }
}
