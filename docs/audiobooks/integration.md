# Audiobooks integration

Arrify owns authentication and playback-device selection. Audiobookshelf owns the
catalog, files, library permissions and canonical listening progress. Arrify uses
its HTTP API; it does not open, scan or modify the Audiobookshelf database.

An administrator enables Audiobooks in the existing Arrify administration screen.
Each approved listener receives a random managed Audiobookshelf identity on first
use. It is restricted to selected book libraries, with no upload, delete or
library-administration permissions. Arrify passwords are never sent to the provider.
Managed credentials are encrypted in a separate Arrify database; preserve its
private encryption key when backing up or restoring the integration. Account erasure
also removes the managed provider identity. Revoking an Arrify session fences new
media requests and active transfers; downloaded files are removed on native sign-out.

The optional section includes a bookshelf, chapters, bookmarks, narration speed,
configurable skip intervals, sleep timer, and book-level Up Next. Continue Listening
reads the user's canonical unfinished books. Music and books keep separate queues.
Selecting a book pauses music through the existing ownership transaction while
preserving the music queue and its cached successors. Continuous book playback is
opt-in. Dedicated CarPlay navigation is excluded: Arrify has no CarPlay entitlement.

## Recovery and offline progress

A checkpoint is written durably before attempting a provider update. Sequence
numbers order seeks and intentional rewinds. A lost response is reconciled against
the canonical cursor before issuing another write. A changed canonical timestamp
with a different position requires a user's choice; it is never replaced by a
maximum-position merge. Offline synchronization does not claim audio ownership or
interrupt another device. Ordinary provider failures never invalidate Arrify login.

Audiobookshelf's API does not provide an atomic compare-and-swap progress write.
Arrify serializes its own updates and checks the canonical timestamp before each
write. A simultaneous update made directly through another Audiobookshelf client
can still race between that read and write. Cross-client instantaneous consistency
cannot be promised without upstream API support.

Native full-book downloads use a separate protected Application Support directory,
excluded from device backups. URLSession background transfers retain completed
parts and opaque resume data. An explicit authenticated resume renews an unchanged
source's grant URL. A different source revision or origin discards incompatible
resume data. Audio grants contain no provider credentials. Offline revocation cannot
be observed until connectivity returns; a server cannot remotely remove bytes from
an offline device. Native sign-out/account erasure removes local book data.

## Verified upstream contract

Integration verification used installed Audiobookshelf 2.38.0 with disposable,
restricted accounts; existing accounts and progress were left untouched. Verify
again on provider upgrades:

- `POST /login`, `x-return-tokens: true`; rotating refresh via `/auth/refresh`.
- Restricted `/api/users` creation requires `isActive: true`.
- Expanded `/api/items/:id`, `/api/me/progress/:id`, bookmark endpoints.
- `/api/me/items-in-progress` for canonical Home continuation.
- Authenticated `/api/items/:id/file/:ino` supports HEAD and byte ranges.

The [published API reference](https://api.audiobookshelf.org/) warns that it is not
maintained, so the installed upstream source and live API are the compatibility
checks. UX references include Audible's [listening controls](https://help.audible.com/s/article/listen-in-the-app?language=en_US).
Native transfers follow Apple's [background download guidance](https://developer.apple.com/documentation/foundation/downloading-files-in-the-background)
and [resume guidance](https://developer.apple.com/documentation/foundation/pausing-and-resuming-downloads).

## Release gates

Keep private production books out of the public review/demo deployment. The demo
must stay disabled or use synthetic content. Preserve the music/authentication
regression baseline. Add the nested audiobook database and private key directory
to consistent backups, and include `audiobook_playing` in deployment idle checks.
Hardware AirPlay, interruption and locked-device playback require physical-device
acceptance in addition to simulator tests. No audiobook TestFlight build has been
qualified or released until the release record explicitly confirms it.
