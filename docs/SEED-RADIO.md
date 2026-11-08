# Search stations — October 26, 2026

Search stations appear alongside local song and artist results. Song menus also offer song/artist radio; external catalog results offer a Radio menu. Start station plays available music. Refresh mix changes the preview without changing active playback. Save mix saves a private snapshot.

## Matching and speed

Playback uses an in-memory catalog and cached relationships, with no model call or metadata network request on the playback/search path. Curated channel/playlist overlap supplies initial context. A bounded background worker resolves an unambiguous MusicBrainz artist, then retrieves ListenBrainz related artists, recording listen counts and metadata. Artist genre profiles filter incompatible relationships when tags are available. Cached external relationships restrict the candidate pool; unrelated popular songs never fill a sparse station. This is artist/context/genre similarity, not measured acoustic, tempo or song-level mood similarity. Ambiguous or missing metadata can limit coverage.

Within qualified artists, curated appearances and public ListenBrainz listen counts favour familiar recordings, while retaining exploration. These are not Spotify ratings or guaranteed chart hits. Recent recordings and artists are spaced, remaster copies deduplicated, and the published next track remains stable. Newly indexed tracks enter subsequent picks automatically; removed songs are pruned without changing playback intent. A one-song pool ends instead of immediately repeating.

An unowned catalog song/artist can seed a station of related owned music. An empty pool does not fabricate tracks or start downloads. Public enrichment can take several seconds on a cold cache; the UI polls briefly, then allows an explicit refresh. One bounded worker, request spacing, response/time limits, bounded caches and persistent private SQLite storage keep this off playback locks. No user account or listening history is sent upstream.

## Sparse stations and requests

Below five available artists or thirty unique recordings, the station offers up to twenty missing album recommendations, at most two per artist. The shortlist uses qualified related recordings and public counts and excludes owned recordings/albums. Fewer than ten is acceptable when evidence is sparse; unrelated music is not inserted to meet a quota.

Review recommendations verifies recordings and pins official releases using existing release rules. Duplicate or unverified albums are excluded. Users can deselect albums before clicking Request albums. Verification alone submits nothing. Approved requests use existing account limits, deduplication, acquisition budgets and Music requests progress/notifications. Partial failures remain visible. Availability notifications and catalog scans bring arrivals into future station picks.

The companion's private /selection endpoint only prepares trusted metadata; the existing submission endpoint remains the sole acquisition action. Music files, tags, and acquisition policies are unchanged.

## Research

- [Spotify Radio](https://support.spotify.com/us/article/spotify-radio/): artist/album/song seeded stations.
- [ListenBrainz core API](https://listenbrainz.readthedocs.io/en/latest/users/api/core.html) and [metadata](https://listenbrainz.readthedocs.io/en/latest/users/api/metadata.html): public artist radio, listen counts and recording identities. The live radio call requires pop_begin/pop_end; authenticated popularity endpoints are not used.
- [OpenAI latency guidance](https://developers.openai.com/api/docs/guides/latency-optimization): precompute/cache and avoid unnecessary model calls. No OpenAI API cost introduced.

## Verification

148 backend tests and 34 JavaScript tests passed. The final targeted radio suite has 24 passing tests, including normalization, similarity restrictions, dynamic catalog changes, manual queue priority, private sessions, persistent external seeds, stale-next revision delivery and slow metadata/disk nonblocking reads.

Disposable Chromium and desktop WebKit flows covered search, station start, mix refresh with the same playing audio element/source, real ended-event advancement, saving a private mix, explicit request approval, Music requests and external catalog seeds. Local preview timings were approximately 6–36 ms on the review catalog; network latency is additional. A twenty-proposal fixture excluded one duplicate and one unverified release, allowed deselection and reported a partial submission failure. Phone/tablet and landscape review widths were checked for overflow. No real acquisition requests were submitted in QA.

Desktop WebKit is not physical iPhone/iPad validation. Physical orientation and Sonos AirPlay startup/Next dropout follow-ups remain open; this feature does not claim to fix those issues.
