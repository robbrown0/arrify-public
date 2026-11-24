# Native authentication and recovery

Access credentials last one hour. Each device session expires after 30 days
without successful foreground authenticated use; there is no absolute lifetime.
Foreground requests extend expiry at most once per five minutes. Refresh, SSE,
background polling and background playback do not extend inactivity. An app left
open in the foreground counts as use. Logout, device revocation and existing
account security revocation continue to remove the session family.

Refresh recovery v1 persists a random attempt ID in device-only Keychain before
sending a refresh. The server atomically consumes the old refresh token and records
only hashes plus issuance metadata. HMAC domain-separated token derivation lets
an identical attempt recover the same response without storing plaintext bearer
tokens. Recovery is limited to 24 hours and the immediately succeeding, unused
refresh token. It never extends the original access expiry. Other consumed-token
reuse revokes the family. After an interrupted refresh lasting over 24 hours,
signing in again can be necessary even within the 30-day inactivity period.

Temporary network, server and storage errors preserve credentials and the pending
attempt. Shared refresh work and bounded exponential backoff with jitter prevent
per-screen retry storms; Retry-After seconds are honored up to an hour. A network
change permits an immediate new attempt. Restoring a saved session does not require
network access. Confirmed invalid refresh or a repeated authorized HTTP 401 ends
the local session and presents sign-in globally. Ordinary permission HTTP 403 does
not mean logout. Recovery resumes the existing authoritative playback reconciliation.

Compatibility: old clients may continue refreshing without an attempt ID, using
their existing strict replay semantics. Their refresh responses advertise the new
one-hour access lifetime, but they do not mark foreground activity and retain an
inactivity deadline until upgraded. New clients require refresh_recovery_v1 before
sending recoverable refreshes. Deploy server support before distributing the app.
The additive schema is readable by the prior server; reverting the server removes
recovery support, so avoid rollback while refresh attempts are pending.

Diagnostics record auth event categories only, never tokens, attempt IDs, endpoints,
account names or track information. Qualification includes expiration, revocation,
legacy behavior, concurrency, lost responses, durable recovery, offline restoration,
foreground/background activity and return to the sign-in UI.

Guidance: RFC 9700 section 4.14 (rotation, replay detection, inactivity expiry);
AWS Builders' Library timeouts/retries/backoff. Durations are Arrify policy choices,
not universal standards. This is first-party native authentication, not an OAuth server.
