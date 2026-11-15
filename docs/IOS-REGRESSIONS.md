# iOS release regression register

Every TestFlight upload must pass the complete app and UI test suites on both
an iPhone and an iPad simulator. `.github/workflows/ios-testflight.yml` enforces
this with parallel qualification jobs and a dependent distribution job. A focused
prototype run is useful during development but cannot replace this release gate.
Existing tests stay in the suite when adding features; do not remove a failing
regression to obtain a release. Record new user-reported regressions here.

| Reported issue | Required automated coverage | Device acceptance |
| --- | --- | --- |
| Lock-screen Next waits until foreground; repeated taps stall | `PlaybackTransitionTests`, including background protection before removal, buffered transition protection, synchronous expiration, failed-next gate release, interruption/pause ordering and slow metadata | With phone locked, Next while local and AirPlay playback are active; repeated taps; automatic track end. Simulator cannot prove iOS background scheduling or receiver behavior. |
| Volume jumps or loses alignment; iPad compact player regressed in builds 27/28 | `testNativeVolumeGeometryFollowsContainerWithoutTrackOffsets`, `testTabletVolumeUsesNativeHeightCenteredInControlRow`; asymmetric real slider fixture, repeated sizing, late native layout, unchanged internal slider frame | iPad mini-player portrait, landscape and narrow window. Drag without a jump. Preserve build 29 whole-control alignment. Simulator omits the actual system-volume slider, so geometry tests are not physical visual proof. |
| Excess bottom space in iPad full-screen player | `testLastChannelClearsMiniPlayerAndRepeatControls` (16–100 point bottom-clearance assertion) | Inspect full-screen player in portrait/landscape, including long titles. |
| Play-here banner hides Settings/Library content | `testTakeoverFooterClearsAllDestinations`, `testSettingsClearsPlaybackFooterWithLargeText`, `testLastChannelClearsMiniPlayerAndRepeatControls` | Home, Library/details, Channels, Search, Settings; banner and reconnect states; Dynamic Type. |
| Search keyboard obscures content or leaves footer misplaced | `testTabletSearchKeepsHeaderAndPlayerVisible`, `testLongTitlePlayerDismissalRestoresSearchResponsiveness` | Open/close keyboard, open/dismiss full-screen player, rotate. |
| Connection status wraps awkwardly or misrepresents route | Connection sheet coverage in `testHomeRecentSongsAndFullScreenSongActions`; core route selection/fallback tests | Verify live local/public changes without starting playback; long addresses remain readable. |
| iPad mini-player empty metadata area does not open full screen | `testCurrentTrackIsMarkedInListAndQueue` taps the trailing open area and verifies Pause does not expand | Tap blank space beside title; playback, volume, AirPlay and Queue remain independent. |
| Collection Shuffle does not start playback or indicate state | `testCollectionShuffleStartsPlaybackAndReflectsState` on phone and tablet | Start shuffled collection, pause/resume without losing shuffle, toggle off while playing, synchronize with full-screen control. Only the matching collection shows active shuffle. |
| Current song cannot be spotted in lists | `testTrackRowStatusFollowsIdentityAndPlayback`, `testCurrentTrackIsMarkedInListAndQueue` on phone and tablet | Playlist, album, mood, station, search, liked songs and recent songs; Next, pause/resume, remote playback. Upcoming duplicates must not appear current. Static icon plus text supports Reduce Motion and differentiation without color. |
| Internal release reports failure immediately after Apple processing | `tests.test_testflight_internal` | Confirm exact build is VALID, INTERNAL_ONLY and IN_BETA_TESTING. Never submit external review without user authorization. |
| App/cache growth | Bundle-size artifact on every upload; existing cache bounds and lifecycle tests | Review payload delta before distribution; exported IPA size is not App Store thinned installed size. |

## Build 30 design and scope

A restrained green current-row background, green title and a small native speaker
or pause symbol with a text status make the track identifiable without an animated
analyzer or audio processing. VoiceOver receives the status as the row value.
Only the current Queue section receives this treatment; upcoming occurrences are
not marked by matching their song ID. Detail navigation titles use the collection
name instead of “Details.” No automatic scrolling or changed song-row tap behavior. The iPad mini-player metadata button includes its open space using a rectangular content shape; adjacent controls remain separate.

References reviewed:
- [Apple shuffle behavior](https://support.apple.com/en-la/109332)
- [Apple Music symbols](https://support.apple.com/en-jo/guide/music/mus131245dbe/mac)
- [Apple accessibility guidance](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- [Apple motion guidance](https://developer.apple.com/design/human-interface-guidelines/motion)
- [Spotify Play Queue](https://support.spotify.com/pg/article/play-queue/)

Build 30 is authorized for internal TestFlight only. Prior fixes remain included.
A passing automated suite does not substitute for the device acceptance cases
above. Record any unverified physical behavior explicitly in release reports.

## Library navigation and responsiveness (next internal build)

- Keep the visible Shuffle label constant; expose on/off through color and VoiceOver.
- Open the active song list around its current song; follow changes only until the
  user scrolls, searches, changes sorting/filtering, or edits. Show playing song
  resumes following. Queue current entry remains at the top, with occurrence-based
  upcoming entries; do not mark matching upcoming duplicates current.
- Direct server windows are bounded to 50 rows. Test earlier/later paging, overlap,
  duplicate playlist entries, missing songs, stale revisions, account-scoped liked
  filters, and old search responses arriving after a new query.
- Songs playback expands the full server selection, never just the downloaded page.
- Preserve all existing regression suites and physical acceptance above.
- New automated coverage: `NativeNavigationTests`, `PopularityReadCostTests`,
  `testLibraryWindowPrependsWithoutDuplicatesAndKeepsRowIdentity`,
  `testSlowPreviousSearchCannotReplaceNewSelection`, and
  `testLongSongsListCentersCurrentAndManualBrowsingSuspendsFollowing`.

Performance review: retain command-state ownership checks, exact nonzero resume
seeks, pause/cancellation ordering, background leases, and progress confirmation.
Avoid re-sorting each catalog page, repeated per-artist database commits when
reopening a playlist within a minute, and seeking to zero when a newly installed
ready item is already exactly at zero. Apple notes exact seeking can add decoding
delay: https://developer.apple.com/documentation/avfoundation/avplayer/seek(to:tolerancebefore:toleranceafter:completionhandler:)
Library-load diagnostics add duration and cancellation only, no song/account data.
Recently added uses stable catalog insertion order (track IDs), not file modification
times; a rescan that updates metadata must not make an old song appear newly added.
- `testSelectionFeedbackAppearsBeforeSlowStateRequestAndClearsOnStop` holds a
  state response and checks immediate row feedback, unchanged confirmed playback,
  and sign-out cleanup before releasing the response.

Synthetic benchmark (Linux, 50,000 songs, 100 first-page calls): previous per-call
sorting averaged 14.19 ms; indexed response averaged 0.02 ms. This isolates server
catalog work and does not measure network latency, SwiftUI rendering or audio start.
Popularity page enrichment batches newly viewed artists in one transaction
(`test_new_playlist_batches_view_records_in_one_commit` verifies 50 artists,
unchanged row order, refresh scheduling and no extra commit on immediate reopen).
