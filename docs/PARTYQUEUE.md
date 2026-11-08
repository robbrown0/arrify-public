# PartyQueue

PartyQueue continues to use Arrify accounts, SQLite, the existing player/listening session, request companion and invalidation hub. No separate service, player, voting system or media exposure is added. Create from Home, Library or the sidebar. Every party has an immutable Arrify/Apple source; Arrify remains the default.

## Invitations and lifecycle

Each creation gets a new random 128-bit session ID. Identical names are allowed and never identify a session. Domain-separated HMAC-SHA256 capabilities (192 bits) provide independent guest and read-only display invitations. SQLite stores their SHA-256 digests, not plaintext tokens. The private 256-bit derivation key lives at `ARRIFY_DATA/party-invitations.key` with mode 0600; creation and directory updates are flushed to disk. Back up this key securely alongside the accounts database. It is not a host login credential and is never sent to a client.

The host can copy/share the invitation, print a locally generated QR poster, and open the independent TV display. Join links are `/party/join#<invitation>` and display links are `/party-display#<display-capability>`. Fragments keep credentials out of initial navigation requests and referrers; scoped API requests carry the capability over HTTPS. The server deliberately disables access logging. Reverse proxies must also avoid logging capability-bearing API URLs. QR generation uses the existing vendored MIT Project Nayuki generator, without an external QR service.

Active parties accept guests and requests subject to the existing open/closed setting. Ending is permanent: joins, search/write permissions, QR fetches and playback commands are refused, guest credential rows are removed, playback is paused/detached, and pending entries become removed/skipped. Queue/history rows and settings remain until deletion. Repeated end is idempotent. Public state becomes a terminal, empty snapshot rather than exposing later queue changes.

The owner may delete an ended party, or confirm ending and deleting an active party in one transaction (`confirm_end: true`). Entries, acquisitions, guest associations and party-specific Apple history are removed. A minimal tombstone retains only ID, owner ID, capability digests, state and timestamp to prevent reuse and recognize deleted invitations; it has no party name, queue or guest identity. Repeated owner deletion is idempotent. Other accounts and guest/display capabilities cannot end/delete/manage a party. Ordinary users, music files, library and unrelated playlists/history are untouched.

Additive startup migration creates `party_session_ids` and adds `parties.invitation_version`. Legacy plaintext invitations expire permanently, and active legacy playback is paused/detached. Their settings/history are retained for the host. A new party is required. The migration is transactional and restart-safe. If a database is restored without its matching derivation key, unmatched invitations expire instead of silently issuing a different credential for an existing party.

## Request rules remain unchanged

The authoritative `Parties.quota()` algorithm is unchanged: three initial charged requests, every two completed/removed/skipped charged entries unlock two more; pending charged entries cannot exceed the configured limit. Limits/refill remain configurable from 1–20. Requests remain FIFO unless the host deliberately reorders them, moves a song to Play next, or removes one. All inserts and quota decisions execute under the existing SQLite `BEGIN IMMEDIATE` transaction and shared lock, including concurrent requests.

Guest identity remains a per-party HttpOnly, SameSite browser cookie with separate guest CSRF protection. It is a browser identity, not proof of a person: another browser/profile can obtain a separate allowance. Host mutations retain existing account/CSRF authorization. Frozen/deleted/unapproved owners revoke public access. The invitation-validation bucket is bounded per network peer; external catalog searches and writes retain their separate existing limits.

Missing-music submissions still use the host's existing request pipeline and consume no queue slots. Verified arrivals are inserted without charge. A submission already dispatched before End may complete in the acquisition pipeline; it cannot repopulate the ended/deleted party. No downloader settings or background workers are replaced.

## Independent TV display

The dedicated `/party-display` surface has Arrify branding, party name, large artwork/title/artist/album, normalized playback progress, paused/offline state, upcoming FIFO requests, queue counts and a persistent Scan to Join QR. Attribution is deliberately generic on the public TV (Guest request/Host); the host retains actual guest names. The browser display has no audio element, playback permission, host session or MusicKit user token. It stays connected when the host changes screens, changes apps or locks the phone. Audio continuity still depends on the actual playback device/provider; the display cannot keep a sleeping audio client alive.

Use the display URL on a separate browser/kiosk device. No screen mirroring is required. Fullscreen and QR printing are available. Desktop 16:9 layouts scale through 1080p and 4K; narrower portrait and landscape layouts remain usable.

On connection loss the QR is immediately removed. A watchdog also removes it after 12 seconds without a verified response/event. Reconnection always fetches authoritative state before restoring it. End removes the QR and shows Party Ended; a deleted invitation shows an inactive screen. Artwork URLs are entry-scoped and reauthorized; they never provide music bytes or arbitrary library access. Stable artwork DOM/URLs and private revalidation avoid repeated unchanged image downloads.

## Shared API and synchronization

Existing host API remains `/api/v1/parties` and `/api/v1/parties/<id>`. New host actions are `play_next` (entry_id) and `delete` (optional confirm_end). Existing settings/add/move/remove/toggle/end remain. Host QR endpoint rejects ended parties.

Guest APIs remain `/api/v1/party-public/<invitation>` with join/search/catalog/add/request. Anonymous initial responses reveal only join-screen metadata; queue/eligibility require an authorized guest cookie. Display APIs are `/api/v1/party-display/<display-capability>`, `/qr.svg`, `/artwork/<entry-id>` and `/events`. Display capabilities are read-only and cannot authenticate as guests or accounts.

Guest `/events` requires its guest cookie; display `/events` requires only the separate display capability. Both reuse `ListeningEvents` SSE invalidations. Connections revalidate SQLite authorization on every wake and at least every five seconds. End/delete invalidations wake clients immediately and terminate former guest subscriptions without further queue payloads. Limits reserve two public display and two public guest streams, leaving existing account/control HTTP workers available; excess clients use conditional snapshot fallback. No second event server is introduced.

SSE events (`session`, `ping`, `terminal`) contain only revision/version, authoritative timestamp or terminal state. They are ordered invalidations, not a replay log. New connections always get an invalidation and fetch a full snapshot, including after process restart. Snapshots are transactional; browsers serialize refreshes. Normalized display `playback` includes position, duration, observed_at, server_time, playing and online. Progress is projected between observations and bounded after stale reports. Conditional GET handles fallback/resynchronization; display polls every ten seconds for changing playback observations, guests every fifteen seconds. No artwork or music travels through SSE.

## Local access, remote joining and native clients

Default public invitation origin is existing `ARRIFY_ORIGIN`; optional `ARRIFY_PARTY_PUBLIC_ORIGIN` explicitly overrides it. Public remote origins require HTTPS. Explicit private-IP/local-host HTTP is preserved for LAN-only installations; those LAN-only QR links require access to that LAN and are not universal links. Configure HTTPS for public QR joining. No new ports, public music share, proxy route or automatic remote access is created. If a separate public origin is used, include it in the existing allowed origins/proxy configuration.

The backend serves `/.well-known/apple-app-site-association`. Set `ARRIFY_IOS_APP_IDS` to actual signed `TEAMID.bundle.identifier` values to opt in; absent IDs yield an empty association. The native iOS app must have matching Associated Domains entitlements and handle `/party/join` plus its token fragment. No native iOS Arrify app currently exists, so installed-native universal-link handoff cannot be claimed or device-tested. Web joining works without one; the saved PWA remains the existing web app.

Apple TV does not offer WKWebView and Roku does not provide a general-purpose web browser. Remaining native work is a tvOS view and Roku SceneGraph channel that securely accept/store a display capability, read these same normalized snapshot/events APIs, render QR/artwork, and handle remote navigation/network transitions. An owner supplies the display-only URL/capability as the authorization mechanism; it contains no host permission. Native platform pairing UX, app signing/distribution, remote-control accessibility and physical playback/device testing remain. No native TV application or casting claim is made here.

References: [Apple universal-link setup](https://developer.apple.com/documentation/technotes/tn3155-debugging-universal-links/), [Apple web-view platform support](https://developer.apple.com/design/human-interface-guidelines/web-views), [Roku browser availability](https://support.roku.com/article/can-i-browse-the-internet), [Roku SceneGraph](https://developer.roku.com/dev/docs/scenegraph).

## Apple Music controls and limitations

MusicKit's key remains server-only; catalog IDs/metadata are resolved on the backend. User authorization follows MusicKit in the host browser. The upgraded bar includes canonical album art, track details, previous/play-pause/next, seek progress and an AirPlay/output button. The button invokes MusicKit's own native output picker synchronously in the gesture when available, with Control Center guidance otherwise. It never targets Arrify's separate media element. Lock-screen actions are previous/next tracks, not timed skips. Replaying a previous Apple song is an uncharged host replay; it leaves the interrupted guest request queued and does not alter guest credits.

Real MusicKit catalog signing and authorization-window opening have been tested. User authorization previously reported Unauthorized; a referrer-policy handoff fix is deployed, but successful real subscriber authorization/playback, entitlements, region/unavailable content, physical iOS lock-screen controls and AirPlay/Sonos remain acceptance tests. Mock-provider success is not proof of those behaviors.

## Verification and rollout

Tests: `tests/test_parties.py`, `test_party_http.py`, `test_party_sessions.py`, `test_party_session_http.py`, Apple party tests, `tests/party-session-browser-check.cjs` and the Apple browser fixture. Existing 3/2 replenishment, simultaneous writes, FIFO, duplicates and playback-transition tests are retained. New tests cover uniqueness/hash storage, legacy expiration, end/delete, stale guests, owner isolation, concurrent end/write, delayed acquisitions, SSE state/termination, TV offline/reconnect and QR removal.

Run Python with `PYTHONPATH=.:request-bridge` (and the existing vendored package/runtime dependencies), and `node --test tests/*.test.cjs`. Browser checks require Playwright and an isolated QA service/catalog. Never point fixture checks at production. Physical iPhone/iPad, native TV clients and live Apple subscription playback are not covered by headless Chromium.

Before rollout back up accounts.db consistently and retain the previous player image. Existing invitations will expire. Preserve every current mount/environment value, especially /requests, music mounts and Apple secret mounts. After rollout protect/backup the new invitation key. Rollback to an older image must first end all upgraded sessions, because older builds do not understand hashed invitations; restoring a pre-migration database discards newer activity and is not the default rollback.

## Second-screen setup and display fixes

1. On the host device, choose **Copy display link**.
2. Open that link on a second iPad or computer and keep the page open.
3. If using Apple TV or an AirPlay-compatible Roku, start **Screen Mirroring on the second device**. Keep the main device for Arrify host controls and music to Sonos.

The print poster contains the party name, QR and Scan to Join instructions, with no URL beneath it. Print layout removes screen-height sizing to fit a single page.

Display controls detect native fullscreen support. Supported browsers enter/exit fullscreen. iPhone Safari falls back to **Focus view**, with visible guidance that browser bars remain. This is not a claim of native iPhone fullscreen. Screen Wake Lock is retained, reused and reacquired after returning to the page. A status confirms when it is held. Refusal/release is visible, with a retry button and Low Power Mode/Auto-Lock guidance; the site cannot override iOS power policy or system Auto-Lock settings.

Apple Music queue continuation handles both SDK ended and completed events, ignores old-item events and preserves continuation across a naturally exhausted queue. Next is revision-fenced and explicit Pause remains authoritative. Replacing a song no longer issues an unnecessary SDK stop first; an already-ended song is not repeatedly stopped while waiting for requests. An active Apple party can wait empty and adopt the next arrival. Real iOS autoplay/background behavior remains a physical acceptance check.

While viewing an active Apple Music PartyQueue, the top search bar is labelled **Search Apple Music** and uses that party's existing server-authorized Apple catalog endpoint. Results are shown within the party and can be added to it. Leaving the party restores Arrify search; searches are never silently sent to the local catalog from that Apple party context.

The tablet Arrify dock follows the Apple dock layout, with artwork/details left, heart/queue centered, output/previous/play/next right, and the timeline below. Existing audio elements, output selection and button handlers are retained.

The Arrify dock has no separate fullscreen icon. Tap its artwork or song title to open the full-size card; heart and queue are the only center actions. Browser checks compare the native and Apple dock typography, container styling, button dimensions and transport spacing in both iPad orientations.
