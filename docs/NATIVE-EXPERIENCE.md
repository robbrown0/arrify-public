# Native listening experience

Use Apple platform conventions and familiar music-app behavior while retaining
Arrify's restrained black surfaces, readable artwork and green action accent.
These are implementation requirements for the forthcoming iOS app, not a claim
that a native UI already exists. References reviewed November 8, 2026.

## Navigation and browsing

Start with Home, Search, Library and Channels as stable destinations. On iPad,
adapt to a sidebar/tab presentation and available window size; do not stretch the
phone layout. Keep a persistent mini-player above navigation and a full Now Playing
view. Use platform controls and SF Symbols with accessible labels; use native system
materials where appropriate while keeping content black and uncluttered. Apple
recommends predictable tab availability and supports attached mini-player accessories.
[Apple tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars).

Search prioritizes playable matches, with artist aliases and clear Songs, Artists,
Albums and Playlists filters. Catalog discovery is secondary and expandable.
Unavailable tracks show Request, never an enabled play control that cannot play.
Artist pages expose popular songs and albums with explicit release/popularity sort;
only show popularity when a defensible ranking exists. Moods are top-level groups
with related playable playlists. Keep station logos legible, controls consistently
placed, and secondary details subtle without sacrificing contrast or touch targets.

## Playback and queue

Tapping a song starts it in the selected collection context. Tapping a channel song
starts there and continues channel rotation. Present Play Next and Add to Queue as
distinct actions, preserve manually queued items, and label the source of automatic
continuation. Show the upcoming queue, allow reorder/removal, and give clear feedback
when replacing it. Do not claim every provider uses identical insertion rules:
Arrify's contract must specify its own deterministic behavior. Familiar references:
[Apple queue controls](https://support.apple.com/en-au/109336),
[Spotify Play Queue](https://support.spotify.com/gy/article/play-queue/).

One long-lived playback coordinator owns audio, independent of screens. Use
AVAudioSession playback, system routing/volume, Now Playing and remote commands.
Backgrounding or locking must not stop playback. Headphone removal pauses; a phone
call resumes only when system guidance, prior intent and ownership permit it.
An explicit pause remains paused. Show the actual output and distinguish local
playback from controlling another device. Verify speaker AirPlay and Apple TV
separately. [Apple playing audio](https://developer.apple.com/design/human-interface-guidelines/playing-audio).

## Quality, caching and recovery

The main player stays simple. A secondary quality view describes the actual
rendition and route, not merely the configured maximum. Tidal's player distinguishes
available/selected quality, a useful precedent for honest quality labeling.
[Tidal quality display](https://support.tidal.com/hc/en-us/articles/17412130162961-HiRes-FLAC-audio).

Separate temporary read-ahead from deliberately downloaded/offline content. Show
Ready only for a validated playable file. Explain queued/preparing/retrying states
when needed, with a bounded retry and useful recovery action. Current plus next two
is automatic; storage/cellular policy belongs in settings. A cached quality change
must not silently relabel old bytes. Home-only routing never falls back to VPS
silently. Do not show technical diagnostics in the normal listening flow.

## Accessibility and release acceptance

Support Dynamic Type, VoiceOver, sufficient contrast, reduced motion, and at least
44-point tappable targets. Green is never the only indicator of state. Large text
must reflow rather than clip song titles and controls. Test accessibility on both
phone and tablet. [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility).

A native release must pass: sign-in/renewal without a password prompt during a normal
session; browse and search without blocking audio; correct collection/channel queue;
lock-screen transport; calls/Siri and headphone changes; AirPlay; home/remote/VPN
switches; large-track read-ahead; cache eviction; low storage; token expiry; second
device takeover; and accessible loading/empty/error states. Measure startup, stalls,
transition latency and memory on physical devices. A familiar appearance is not a
substitute for reliable playback. Use these criteria in feature PRs and the device
release checklist.
