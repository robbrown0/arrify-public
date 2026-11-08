# Artist and album exploration

Artist detail pages load a cached MusicBrainz album discography separately from local playback. Release groups represent albums rather than repeating every regional edition. Browse pages use the actual returned item count for the next offset, following https://musicbrainz.org/doc/MusicBrainz_API . Metadata can be incomplete; this is a catalog view, not a guarantee that every album exists or can be acquired.

Search retains immediate local results. “Explore artists & albums” performs explicit external discovery for artist names, album titles, and combined artist/album queries. Verified MusicBrainz aliases bridge names such as Pink/P!nk. Artist results include disambiguation where supplied. Opening a local artist verifies its identity against a known song; ambiguous identities fail visibly rather than merging namesakes.

Album opening selects an official edition, verifies release-group identity and complete track counts, and reuses the existing best-local-copy and explicit-request flow. Browsing never submits acquisition requests. Explicit discography browsing includes live albums and compilations; automatic song-to-original-album resolution retains its existing exclusions. Missing metadata shows a retry/error without replacing local music.

The helper retains its network lock, request spacing, bounded responses, deadline, and 24-hour cache. UI pages guard against stale responses, disable pending actions, support keyboard activation and dialog focus restoration per https://www.w3.org/WAI/ARIA/apg/patterns/button/ .

Validation: Python catalog/identity/edition tests, existing Node tests, mobile Chromium catalog and album checks, and read-only live Pink / Try This / Pink Try This queries. Real iOS appearance remains a device check.
