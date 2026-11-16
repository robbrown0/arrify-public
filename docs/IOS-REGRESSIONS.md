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
  user scrolls, searches, changes sorting/filtering, or edits. Collection lists retain
  Show playing song to resume following; Songs now uses navigation to return to the
  playing track (see Songs scrolling below). Queue current entry remains at the top, with occurrence-based
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

PR Apple qualification runs iPhone and iPad as parallel jobs on separate macOS
runners, with fail-fast disabled so a failure does not hide the other device's
results. `apple-build` aggregates both results and fails if either fails or is
cancelled. The generic physical-device build runs once. All app/UI tests and
screenshots remain included. TestFlight qualification already uses the same
parallel device matrix and distribution still depends on both passing.

Qualification caught a user-scroll regression in the initial navigation change:
the long Songs list moved, but the phase callback did not suspend following on
iPad. Lists now also observe the native scroll view pan recognizer from the current
row, without adding a competing gesture or replacing its delegate. A first
simultaneous-gesture attempt interfered with a song menu; retain menu tests as
well as scroll tests. The same safeguard applies to the queue. Keep
the long-list test's actual drag and recenter assertion; its old return-button
assertion is replaced by absence of that bar and navigation back to Songs. Initial
positioning alone is insufficient evidence that manual browsing works.
The native scroll observer must remain hidden from accessibility. The queue's
current row explicitly retains its combined accessibility element and playback
value after the observer background is applied; `testCurrentTrackIsMarkedInListAndQueue`
checks the paused value as well as visible highlighting.

Account deletion dismisses password focus before presenting confirmation, so
keyboard restoration does not compete with the alert or error state. Keep cancel,
wrong-password, successful deletion and sign-out assertions. UI cases stop after
the first failed assertion to avoid cascading missing-element waits; the suite
still executes the remaining cases.

## Player actions and handoff sprint (internal only)

- Menus must stay open and retain scroll position through playback ticks; album
  navigation must work with one tap. `testEmptyPlayerAndSingleTapAlbumActions`
  covers both device families, including original album navigation. Test manually
  with large Dynamic Type and sustained physical playback.
- Empty player: meaningful Browse music action, no transport controls until a
  selection exists; reserve footer height on every tab, tablet and keyboard layout.
- Saved addresses are separate from tokens. Test normal TestFlight upgrade followed
  by expired-session sign-in; retain the private address and allow explicit removal.
  `testSavedAddressesSurviveCredentialRemovalAndCanBeClearedExplicitly` covers preferences.
- One active output per account remains authoritative. Native explicit Play/Resume
  can claim paused, failed or expired owners, never a newly refreshed live owner.
  Server tests cover atomic claims, late heartbeats and existing epoch fences.
  Native tests `testExplicitResumeClaimsInactiveOutputAfterFreshState` and
  `testFreshActiveOwnerBlocksImplicitResumeWithoutActivatingAudio` check activation order.
- Native AirPlay routing is unavailable on non-owner devices. Full-screen takeover
  uses the same banner as compact playback. Verify phone/tablet and VoiceOver.
- Original album: preserve identity/version/official-release verification, skip
  later candidates only when they cannot improve the verified album year, retain
  metadata cache/rate limiting, bound cold album lookup budget to 12 seconds, and
  cancel client retry tasks with their presentation. External service availability
  remains outside Arrify's control; an error must allow retry without stopping audio.
- Keep every prior regression and native volume geometry test. Do not publish an
  external build; internal device acceptance is still required after qualification.

Qualification notes: give full-screen takeover its own `fullScreenTakeOverPlayback` identifier
because the compact banner remains in the underlying view hierarchy. Its button
must be hittable after presentation and AirPlay must be unavailable. The slow-state
feedback test holds all state reads during its assertion so a concurrent background
refresh cannot consume a single-use delay intended for the selection.

## Open: intermittent Show playing song crash (2026-11-15)

Reported sequence: open Songs, tap Show playing song, app terminates. After relaunch,
a temporary unavailable/retry message appears on repeated visits; the earlier item
then returns and Show playing song works repeatedly. Device, build and timestamp
awaiting confirmation. Recovery does not close this regression.

Investigate initial/around-track loading, list identity changes and centering during
layout updates, overlapping refreshes, and restoration after termination. Extend
coverage beyond the existing already-loaded-list centering test to cold/off-page
loads and failed-load retry. Do not identify a root cause or claim a fix without
crash evidence or reproduction. Track the post-launch error separately until linked.

## Open acceptance: action menu refresh and original-album latency (#37)

Confirmed on iPhone 0.3.0 (32): playlist and full-player action menus refresh roughly every ten seconds; the lower menu item is clipped and scrolling can reset. Original-album lookup for Hold Me Now / Thompson Twins failed after about thirty seconds. iPad not yet retested by user.

Regression coverage must keep the menu open across background refresh cycles, retain its scroll position, reach the last item, and execute album navigation once. Menu contents are captured per opening so selection targets cannot change during playback refresh. Album browsing must retain artist/version/release verification, avoid redundant recording fetches, bound the interactive helper wait, and show provider failures without disrupting playback. Simulator success does not replace the reported real-device acceptance. Retain #35 separately; its crash remains unconfirmed.

## Unified search and relevance

- iPad Songs/Albums/Artists reuse the top search field with a persistent All/Songs/Albums/Artists scope selector. Scope changes preserve the query; sidebar navigation starts a fresh section search. Global search includes all three entity types.
- Nonempty queries use relevance ordering, including catalog playback expansion; clearing restores the saved browsing sort. Exact normalized titles rank first.
- Search debounce is 150 ms; the tablet navigation stack resets when starting a search session, not on every keystroke. Existing cancellation/generation guards remain.
- Snapshot-local search caches retain at most 16 ranked ID lists per entity index (maximum 50,000 IDs per entry). They contain no account filters; liked filtering remains outside the cache. New snapshots invalidate cached results.
- Regression coverage: native exact-match paging/playback order, repeated query paging/cache bounds/snapshot isolation, single iPad search field and query retention across scope changes, existing delayed-search cancellation tests, and `testReturningToPreviousQueryAfterCancellationReloadsClearedWindow`. Search resets invalidate the last successful timestamp before clearing rows.
- Device acceptance: search “Take on me” on a large library; confirm A-ha ranks above “Take A Chance On Me,” change scopes, clear, open an album then start another search, and compare local/public network responsiveness.

Build 34 is authorized for internal testers and external beta users. Run both complete device suites before upload; verify Apple processing and external review state before claiming beta availability.


## Songs scrolling and entry behavior

- Songs loads preceding rows automatically when scrolling upward; no Earlier items
  button. Preserve the visible song position when prepending, stable row identities,
  and existing cancellation/revision guards. A later drag must win over a slow
  previous-page response. Section initials follow server casefold ordering, including
  ß and Kelvin-sign titles (`testCatalogSectionTitlesFollowUnicodeCasefoldOrder`).
  Do not fetch the entire library at entry.
- Remove the Show playing song bar from Songs. Opening Songs while playback is
  active starts around the playing track; paused/stopped playback starts at the
  normal sorted/filtered view. Searches and explicit sorting/filtering remain in
  control and must not snap to the playing track.
- Manual browsing suspends following, including when earlier/later pages arrive or
  the playing track changes. Returning through Songs navigation resumes positioning
  for active playback. Other collection and queue behavior remains covered.
- `testLongSongsListCentersCurrentAndManualBrowsingSuspendsFollowing` retains real
  scrolling and centering assertions, reaches song 1 outside the initial window,
  verifies that Next does not disturb manual browsing, reopens Songs at the new
  playing track, and verifies normal entry after pausing. It also checks that both
  obsolete Songs buttons are absent. Retain app paging/identity and delayed-response
  tests, plus all menu tests; do not introduce a competing scroll gesture.
- Physical acceptance: iPhone/iPad portrait and landscape, slowly cross several
  previous-page boundaries with local/public connections, verify no visible jump,
  test search and saved sort/filter choices, and reopen Songs during local/remote
  playback and while paused/stopped. The unresolved crash report above remains open;
  removing its former button does not establish a root cause or crash fix.


## Home selection returns to the top

- Selecting Home starts at the top of Home, including tapping Home again while
  already there. Returning from another section must also reset its scroll position
  and dismiss any nested Home navigation; keep the shared player uninterrupted.
- `testHomeSelectionReturnsToTopIncludingReselection` scrolls Home until its heading
  is offscreen, verifies the original heading position after reselecting Home, and
  repeats after visiting Library. Both iPhone and iPad run this regression with a
  personal playlist and remote-playback footer to ensure scrollable content.
- Physical acceptance: repeat on phone/tablet with long Home content, nested album
  or playlist detail, and active local/remote playback. Confirm no playback restart.
