# Account deletion

Implemented against Apple's [5.1.1(v) account deletion guidance](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion), checked November 11, 2026.

- Settings → Account → Delete account. Current password and a destructive confirmation are required. Cancellation does nothing; invalid credentials leave the account intact.
- The sign-in screen also offers Delete an existing account, so pending or frozen accounts need no administrator approval or support request.
- The authenticated API accepts no username or target ID: it deletes only the bearer session's account. Without a session, username and password are required. Both paths require explicit confirmation, HTTPS in the app, strict fields, bounded password hashing and rate limiting. Passwords never go into a URL or application log.
- Deletion removes the user row and associated records, including shared playlists, requests, hosted parties and guest data, integrations, preferences, profile/avatar, and histories. Session deletion cascades to every native token family and remote activity registration. Streaming grants no longer authorize new requests. Audio already delivered to a receiver cannot be recalled.
- The deleting app stops playback and clears Keychain credentials and its artwork, audio and diagnostics caches. Other devices lose server access; exported/third-party copies cannot be remotely erased.
- Administrators may delete themselves, including the last administrator. Initial setup stays closed afterward to prevent a network visitor claiming administration. The operator can provision a replacement using the trusted server administration tooling; deletion does not require doing so first.
- Arrify has no paid subscriptions or Sign in with Apple. Apple Music is an optional playback integration, not Sign in with Apple; deleting the stored integration is not cancellation of a separate music subscription.

## Backups and restoration

`account-deletions.jsonl` contains only reserved numeric IDs and the initial-setup-completed flag. It is written and fsynced before erasure, kept with the live data, and replayed before authentication is enabled. `deleted_account_ids` prevents ID reuse and blocks late writes by requests that were already in flight. A malformed ledger stops startup rather than restoring deleted accounts.

**Never roll back or omit the live deletion ledger when restoring an old database.** Preserve it separately from rotating database snapshots. A full disaster restore must recover the most recent ledger before starting the service. Failed deletion transactions with a recorded intent are completed at the next server start.

Run the following hourly for each server, with its own data and backup directories:

```
python3 tools/erase_account_backups.py --data /path/to/live/data --backups /path/to/backups
```

Pass `--requests /path/to/arrify-requests` when a shared acquisition service is configured, to remove legacy requester IDs from queued requests. New submissions do not disclose the account ID to that service. Shared acquisition jobs continue for the server library.

This removes erased account data from `accounts.db` snapshots, retains the reserved IDs/setup flag, checkpoints WAL, and vacuums the files. It does not delete another user's account or the shared catalog. Monitor the job for failures. Robnets-operated servers run this hourly; the published maximum backup erasure window is 24 hours. Independently operated servers must establish and disclose their own retention practices. Do not place database snapshots outside the configured backup root without applying the erasure ledger to them too.

## Validation

Server tests cover wrong-password refusal, strict targeting/confirmation, brute-force throttling, pending/frozen deletion, multiple session revocation, integration and shared-party cleanup, other-user isolation, ID reuse, late writes, last-admin setup protection, backup scrubbing, and replay after restoring an old database. iPhone and iPad UI tests cover Settings discovery, cancellation, incorrect password, confirmation, success acknowledgement and return to sign-in. No live user account is deleted by release validation.
