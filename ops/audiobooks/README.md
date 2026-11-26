# Ordered book publications

Audiobookshelf remains the catalog and listening-progress authority. Arrify uses
its API; these producer-side tools never edit its database. Arrify does not group
items by title: each source book must be published as one Audiobookshelf item.

The existing Studio producer publishes reviewed chapter previews separately for
Plex. `audiobookshelf_books.py` creates a second, read-only publication view for
Audiobookshelf, keeping those previews and the original recordings intact. It
does not generate narration, invoke paid services, or change Studio job state.

Install the adapter beside the producer's `book_metadata.py` and
`plex_audiobooks.py`, and copy `tools/audiobook_publication.py` there as
`book_publication.py`. The adapter uses the producer's verified source-upload
identity and chapter order. Duplicate chapters or conflicting editions fail
closed. Only a contiguous prefix beginning with the prologue is exposed; later
chapters wait for gaps to be filled. Existing chapters retain their files when
new chapters are appended. Destructive shrink requires manual review.

The private `config/book-library.json` contains `published_root` and the dedicated
Audiobookshelf `library_id`. Mount that directory read-only into Audiobookshelf
and configure the library through its API. Existing `config/library-login.json`
supplies the producer's private credentials; credentials never enter the repo.
Select this library in Arrify's audiobook provider settings.

The included user service and timer check reviewed publications every minute
with jitter and bounded resources. Update their paths for another producer host.
Receipts under `work/book-publications` identify the canonical item and source
files. Unchanged inputs do not trigger copying or scanning. A changed edition is
verified against Audiobookshelf's indexed file order and chapter titles. Failed
scans retry on the next timer invocation. Archived previews whose published M4B
no longer exists are not restored.

Before consolidating an existing preview library, back up the source metadata,
publication list, hashes, and Arrify's encrypted audiobook state. With playback
idle, map only proven Arrify-owned listeners' chapter progress/bookmarks to the
corresponding global offsets through the API. Preserve all original progress
and unowned accounts; do not overwrite newer canonical progress. Record a
one-time migration receipt before switching Arrify to the canonical library.
Restoring previews requires restoring the selected library and its account
permissions, not deleting the new book or its listening progress.

The current source edition is in progress. Appending chapters preserves its
canonical item identifier, but a listener who finished the currently available
edition may need to select the next chapter after new content arrives.

## Qualification

`tests/test_audiobook_publication.py` checks source preservation, stable append,
ordering, missing chapters, duplicate editions, and rejected destinations.
`test_audiobooks.py` also verifies one multipart item and one global cursor.

The manual `tests/audiobook-navigation-browser-check.py` and
`tests/audiobook-multipart-browser-check.py` use Playwright against the disposable
loopback preview on port 18850 with its synthetic `navqualifier` account. They
must never target a real account or production data. The latter requires the
synthetic provider `tests/audiobook_preview.py` with `ARRIFY_MULTIPART_BOOK=1`.
It plays two actual short WAV files, checks the second begins at zero, verifies
a finished checkpoint, and confirms chapter sleep does not open the next file.
Set `CHROMIUM_EXECUTABLE` only when using an existing Chromium installation.
