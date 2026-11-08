# Original review library

`generate.py OUTPUT_DIRECTORY` creates six original synthesized instrumentals and geometric album artwork. It uses only mathematical oscillators, no sampled recordings, third-party compositions, trademarks, or downloaded artwork. Pillow is a development-only dependency. These demonstration assets may be used and distributed with Arrify and its review server.

The generated manifest supplies tags for encoding each WAV as FLAC with ffmpeg. Attach its album's cover.png, and set title, artist, album, and track metadata from manifest.json. Install only the resulting `Arrify Studio` directory as the demo music root and playlists.json as its collection definitions. Keep this service isolated from production accounts and media.

The review account needs two station preferences: demo-daylight and demo-afterglow. They use the same six original recordings; they are demonstration rotations, not third-party radio services. The collections use existing Feel Good and Unwind mood categories.

Review coverage: Songs, Albums, Artists, Playlists, Channels, Moods, search, play/pause, seek, next/previous, shuffle, repeat, background audio, lock-screen controls, and AirPlay. Receiver-specific artwork behavior still requires a physical Apple TV test.
