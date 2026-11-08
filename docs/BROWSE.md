# Browse discovery

Browse replaces Search in the bottom tray and desktop navigation. The persistent top search field remains available. New & Popular, Decades and Genres are separate destinations with phone/tablet layouts.

## Library classification

A bounded background tag reader builds a private browse.sqlite3 cache without changing source files or tags. Original release year tags take priority when supplied; otherwise the file release-year tag is used. Unknown years are excluded from decade pages. Release-year fallback may place reissues in their reissue decade; this is stated in the UI rather than inventing an original date. Unrecognized/missing genres remain in Other / unclassified. Bluegrass may appear in both Bluegrass and Country, and other cross-genre content can appear in multiple categories.

Genre Songs, Albums and Artists include all matching available library content, with pagination. Playback resolves the full selected scope on the server rather than only the first displayed page. Decades span 1960s through the current decade and offer genre filters ordered by matching library count. Essentials selects up to 200 distinct recordings with curated-collection appearances or cached ListenBrainz count signals; it is explicitly not an official retrospective chart. More recordings can be found under Songs even when Essentials is sparse.

Indexing runs independently of playback with one tag worker and one chart worker. Tags cache by track ID and modification timestamp; charts and tags survive restarts. Catalog additions/removals are reflected as the catalog and metadata index refresh. Cold indexing can take time over NAS storage; progress and a Refresh Browse action are visible. All browse/API selection paths use local cached data and never wait for provider or file-tag I/O.

## Charts and new releases

Overall U.S. Top 40 comes from the public Apple Music most-played feed. Genre Top 40s use Apple's public iTunes Store genre song feeds, with verified genre IDs, and are labeled iTunes Store rather than Apple Music. Sources measure different activity and are not called Billboard charts. Up to forty distinct entries are shown; short charts are not padded with unrelated music. Snapshots refresh after seven days, show fetch/source dates and retain stale successful results during provider failures. Failed lookups back off for an hour. Opening or refreshing Browse never triggers a provider fetch.

New releases is a separate shelf of releases from these charts dated within the last ninety days, not a claim to contain every new release worldwide. Genre filters are available. Existing library recordings are matched by normalized artist/title with remaster deduplication. Play selects only owned tracks. Missing recordings have a one-click Request action: the server verifies the named song and its official album, then submits through the existing private request pipeline. Requested state and progress are retained. Ambiguous identities fail without submission; browsing alone does not request music. See DIRECT-REQUESTS.md.

## Sources

- Apple discovery guidance: https://support.apple.com/en-ie/guide/iphone/iph2c41e6189/ios
- Apple genre/decade categories: https://music.apple.com/us/search
- Apple public RSS builder: https://rss.marketingtools.apple.com/
- Overall feed: https://rss.marketingtools.apple.com/api/v2/us/music/most-played/100/songs.json
- Genre taxonomy: https://itunes.apple.com/WebObjects/MZStoreServices.woa/ws/genres?id=34
- Example genre feed: https://itunes.apple.com/us/rss/topsongs/limit=100/genre=14/json

## Verification

Fourteen Browse tests cover multi-genre normalization, word boundaries, original dates/fallback, all-content scopes, decade filtering, removal/reindexing, pagination, full-scope playback, cached exact chart matching, missing recordings, recent/future dates, malformed/duplicate feeds, weekly refresh, failure backoff and persistence. Full backend suite: 163 passed (includes concurrent companion budget tests); JavaScript suite: 35 passed. Chromium and desktop WebKit flows checked Browse navigation, genres/decades/album/artist/chart routes, missing-song search without acquisition, real playback continuity and five phone/tablet portrait/landscape sizes. Screenshots were visually reviewed. Desktop WebKit does not replace physical iPhone/iPad acceptance.
