# Arrify

Arrify is a personal music server and native iOS client. It supports library
browsing, playlists, moods, channel-style rotations, background audio, lock-screen
controls, and AirPlay.

Source publication does not grant an open-source license. See LICENSE and
THIRD_PARTY_NOTICES.md. Music must be supplied by a server operator with the
necessary rights; no commercial recordings are included.

## Development

- Server: Python, with pinned dependencies in requirements.txt.
- Web: browser-native JavaScript and CSS.
- iOS: Swift/SwiftUI; see ios/README.md.
- API definitions: contracts/ and docs/NATIVE-PLAYBACK-CONTRACT.md.
- Original demonstration media: tools/demo/README.md.

Build and run tests with the Docker test target and the GitHub Actions workflows.
Use an isolated database and demo media for development. Never commit runtime
configuration, account databases, credentials, or production logs.

## Deployment

The Compose files are examples. Configure your own HTTPS origin, storage mounts,
account provisioning, and trusted proxy. Keep deployment overrides outside the
source repository. Do not expose an unconfigured instance publicly.

## Releases and security

Pull-request tests use read-only permissions and no release credentials.
Signing and TestFlight publication are manual workflows restricted to main and
the testflight environment. Configure branch protections and review policies
before accepting external contributions. Report vulnerabilities as described in
SECURITY.md.
