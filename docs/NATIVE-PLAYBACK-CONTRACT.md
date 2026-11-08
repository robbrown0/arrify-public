# Native playback and catalog contract

The additive contract lives in `contracts/native-playback.openapi.json`.
`native-auth.openapi.json` remains the sign-in/session contract. Capability feature
names advertise `native_media_manifest_v1`, `native_catalog_v1` and
`native_playback_v1`. Existing web endpoints and receiver grants keep their behavior.

## One listening path

1. Enroll an HTTPS endpoint, verify its server identity, and create a native session.
2. GET `/api/v1/native/catalog?kind=tracks&q=Pink&limit=50`. The bounded local index
   handles the existing artist aliases. `kind` also supports artists and albums.
3. Continue pages with `offset=next_offset` and the same `revision`. A missing
   revision after page one or a changed snapshot returns 409 `catalog_changed`.
   Restart at page one. The maximum page size is 100; no provider lookup runs here.
4. GET `/api/v1/native/playback` for authoritative ownership and queue revision.
   POST a `play` command with device ID/name, `expected_revision`, and either
   1–1000 track IDs or a channel slug. A selected channel track uses `track_id`.
5. POST `/api/v1/native/playback/manifest` with the returned output device ID,
   owner epoch and explicit profile. The response contains at most current plus
   next two distinct tracks in playback order. Compare `queue_revision` with the
   latest playback state before treating a response as current.
6. Fetch a Ready item's origin-relative URL on the enrolled endpoint. Use AVPlayer
   with a normal media URL and URLSession for complete-file caching; no undocumented
   AVURLAsset header injection is required. Receiver access must still be proven
   on physical Apple hardware, including home-network reachability.
7. Send owner heartbeats with epoch, intent, expected current track and increasing
   report sequence. Heartbeat responses are partial: preserve the preceding queue
   when `upcoming_track_ids` is null. This avoids rewriting large queues on each
   observation. An expired access token is renewed through the serialized auth
   coordinator, independently of an active media transfer.

## Playback command semantics

This initial facade supports play, pause, resume, seek, next, previous, heartbeat,
takeover and reattach. It delegates to the existing server listening state machine.
It does not create a second player or bypass revisions, epochs, ordered heartbeat
checks or channel rotation. Other queue editing operations remain later contract work.

Device IDs are 16–64 lowercase hexadecimal/hyphen characters; normalize a Swift UUID string to lowercase and persist it per installation. They are separate from authentication session IDs.

Non-heartbeat commands require the observed `expected_revision`. Heartbeat requires
`owner_epoch`, `intent`, `expected_current`, position/duration seconds and `observed`.
Reattach requires both revision and epoch. Automatic next additionally requires
`automatic=true`, owner epoch, expected current and expected next. Every native next
command requires a unique `transition_id` (1–128 characters); reuse that ID only for
retrying the same transition. A duplicate acknowledged transition does not skip again.
On 409 `playback_changed`, fetch state and reconcile intent rather than retrying the
old write blindly. Reattach and explicit takeover invalidate old output media grants.

## Preparation, identity and HTTP

Profiles are `original`, `aac` (seekable fast-start MP4) and `mp3`. Leveling is off
for this first contract; unsupported combinations are rejected. There is no silent
quality downgrade. `duration_seconds` is currently null; let the native media asset
resolve duration. A local catalog listing is not proof a source file remains readable;
the manifest reports unavailable files. Album/artist IDs are current browse addresses
and still follow metadata names; durable entity aliases and detail pagination remain
before broad library features. Track IDs plus server and media revision identify cache
entries; the catalog paging revision is not an audio revision.

An available original file is immediately Ready. Compatible renditions use one bounded
background worker and the existing shared two-slot conversion limit. The worker never
adds an unbounded executor queue. Poll no faster than `retry_after_seconds` while
queued/preparing/retrying. Failed preparation is retried at most three times with a
30-second delay after failure; then state becomes failed. `retry=true` explicitly
restarts attempts. Missing files are unavailable. Job history is capped at 128 entries.
Conversion retains the existing 120-second and 128-MiB output bounds. Client cancellation
stops polling; an already-running shared render may finish within those bounds and
remain reusable. It does not authorize playback or hold the account transaction.

URLs appear only for Ready files. A rendition evicted after manifest creation returns
503 promptly; it does not synchronously start a new conversion on the native stream.
Request a new manifest. Preparation state is distinct from a completed device download.
Mark a device file Ready only after validating complete bytes and atomically publishing it.

Ready streams support GET, HEAD, Content-Length, Accept-Ranges, strong ETag, 206 and 416.
Use Range with If-Range for resume. A validator mismatch returns a full 200 response;
replace partial bytes instead of appending them. Media endpoints retain the legacy
`{"error":"message"}` envelope on errors, unlike the native JSON control envelope.
For media 403, refresh the manifest after reconciling session/ownership; for 503,
prepare again. Do not loop indefinitely on either status.

Source identity includes path, filesystem identity, size, mtime and ctime, exposed only
as a hash. Replacing a same-size file with the same mtime still invalidates the grant
and rendition key. A source that changes during conversion is not published. Existing
render-cache keys advance to v2, so previously prepared renditions may need one fresh
conversion; old files age out under the same storage cap. No library audio is changed.

## Scoped authorization and routing

Native grants use signed v2 claims with track, output device/epoch, profile, source
revision and renewable session-family ID. They expire after at most one hour, clipped
to family expiry. Access-token rotation does not interrupt them. Account revocation,
family revocation/reuse detection, source change, takeover and removal from the
current-plus-next-two window invalidate them. Ongoing streams recheck authorization
about once a second; URL expiry alone does not abort an already-authorized transfer,
but family/ownership revocation still does. A valid URL is a secret capability: anyone
holding it can fetch that exact media while its scope remains valid.

These grants cannot authenticate control APIs or artwork, change quality, access an
arbitrary track, or widen legacy receiver scope. Cookies/bearer credentials are not
needed on a native media fetch. Keep URLs out of analytics, support exports and proxy
access logs; redact `native_grant` as well as legacy `grant`. Resolve relative URLs
only against an enrolled TLS-validated origin. Do not follow arbitrary redirects with
credentials or silently substitute the public VPS endpoint for Home-only policy.

The receiver must be able to reach that origin. Home URLs do not magically become
reachable from a remote Apple TV; route selection belongs in the app and device tests.

## Release gates and remaining work

HTTP regression tests exercise real handlers for native manifests, cookie-free media,
HEAD/ranges/validators, profile tampering, next-two scope, token rotation, revocation,
file replacement, takeover, queue changes and evicted renditions. Native catalog and
playback responses are checked against OpenAPI fixtures. Existing stream, playback,
render-cache and authentication suites remain required.

The next deliverable is a qualified Apple build environment and a minimal native
listening prototype with Keychain renewal, one playback coordinator, system route
selection, Now Playing and lock-screen commands. The UX criteria in
`NATIVE-EXPERIENCE.md` remain acceptance requirements. Physical-device tests must prove
calls/Siri, headphone removal, AirPlay to speakers/Apple TV, background transitions,
home/VPN/public routing, and large-file caching. These server changes do not claim
those native tests passed or that permanent offline playback is implemented.


## Native library browsing

`native_library_v1` adds GET `/api/v1/native/library` for account-specific channels,
curated/personal playlists, counts and the web mood taxonomy. GET
`/api/v1/native/library/detail?kind=albums&id=...` supports albums, artists,
collections, channels and authorized personal playlists. Offset/limit/revision
rules match catalog paging (default50/max100, conflict409 on stale pages).
Empty catalog queries now browse sorted local songs/albums/artists.

A `play` command can carry `source:{kind,id}`, optional `track_id`, and boolean
`shuffle`. Sources cannot be combined with direct IDs or other source selectors.
The server resolves/authorizes the complete selection (up to50,000 songs); a
selected song must still belong to it. Channels retain server radio rotation.
Direct-ID commands retain their 1,000-song limit. These additions do not change
web API contracts or permit playlist editing.

Native playback also accepts `shuffle` (reorders upcoming songs, preserving current)
and `repeat` with `mode: off|all|one`, using the same revision fencing as web.
Full snapshots expose `repeat_mode` and `queue_controls_available`; compact
heartbeats return null for these fields, so clients retain the last full values.
Radio and party contexts retain their existing queue-policy restrictions.
