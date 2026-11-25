The fixtures contain only synthesized digital silence, generated with FFmpeg.
They include a one-second MP3 and a twelve-second FLAC, encoded as base64 for review.

`ios-media-probe.yml` runs Apple's real AVPlayer against a loopback HTTP server
on macOS and the iPhone/iPad simulators. It requires the incorrectly advertised
MP3 to fail and the same original MP3 bytes to open with the MIME type selected
by Arrify's production `audio_mime` function. It also checks unmodified FLAC.
The server HTTP regression tests separately verify manifest metadata, full GET,
HEAD, ranges, and grant authorization. No account or media grant is used here.
