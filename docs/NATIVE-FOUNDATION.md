# Native API foundation

This first increment adds native session lifecycle and discovery under `/api/v1/native/`.
The machine-readable contract is `contracts/native-auth.openapi.json` (OpenAPI 3.0.3).
It does not yet promise a complete native catalog or media contract. Existing web
and seven-day `/api/v1/auth/token` behavior remains compatible.

## Authentication decision

Use first-party direct sign-in for the private Arrify client initially. This reuses
Arrify's approved local accounts and password verification. It is not OAuth, does
not provide third-party authorization, and must not be exposed as an OAuth password
grant. A future delegated provider should use a maintained authorization server,
ASWebAuthenticationSession, and authorization code with PKCE.

Access tokens last 15 minutes. Refresh credentials rotate on every use and have
an absolute 30-day lifetime from sign-in. Both are random 256-bit opaque secrets;
only SHA-256 digests are stored. Refresh credentials cannot authenticate API or
media requests. Device names are labels, not proof of hardware identity.

Rotation, consumption, and issuance are one SQLite transaction. Reusing any consumed
refresh credential revokes that session's entire family, including still-valid
access tokens. The revocation commits before returning 401. This deliberately
fails closed: if the response to a successful refresh is lost, replaying the old
credential requires sign-in again. Do not blindly retry refresh after an ambiguous
network failure. This follows the rotation/reuse-detection model in
[RFC 9700 section 4.14](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14).

The iOS authentication coordinator must serialize refresh through one actor/task,
atomically replace the credential pair in Keychain, and never persist passwords.
Use a device-only Keychain accessibility policy compatible with background renewal
(after first unlock); do not store secrets in UserDefaults, analytics, crash
breadcrumbs, or URLs. Clear account cache and credentials on logout. An explicit
sign-in is required at refresh expiry; these lifetimes are policy, not a promise of
permanent login. Do not let each failed image/download independently refresh.

Session-list and revoke endpoints require a native bearer token. Revocation is
account scoped and idempotent; another user's session ID is a no-op. Account freeze,
delete, password recovery, and existing logout remove the corresponding refresh
families through the existing session revocation boundary. Ordinary access expiry
does not revoke renewal. Prior access tokens remain valid until their own expiry
unless the family is revoked, so an in-flight request is not invalidated solely
because another request renewed credentials.

Rate limits use the actual TCP peer, never untrusted forwarded headers; a proxy
can therefore share a bucket across clients. 429 includes Retry-After. Account-aware
proxy attribution and fleet limits remain a hardening follow-up before public beta.
The current limits are conservative for this private deployment. TLS is mandatory
outside loopback tests. No secrets are logged by these handlers.

## Contract and endpoint enrollment

Capabilities return an opaque stable server ID stored in the account database.
Home and public endpoints sharing that database report the same ID. Compare it only
over validated TLS after the user enrolls an endpoint. It is an identity hint, not
cryptographic proof: do not send credentials to a discovered/untrusted hostname just
because it claims the ID. Never forward bearer headers across arbitrary redirects.
Restoring the same server preserves the ID; an independent cloned installation must
be assigned a new identity before enrollment.

Times (`created`, `expires`) are Unix seconds; duration fields are seconds. Clients
ignore unknown response fields/features. Native errors contain a stable code,
human-readable message, and opaque request ID. All native responses use no-store.
Malformed JSON and transport validation also use that envelope. Legacy endpoints
still have their existing error contract (including 403 on expired credentials);
clients must not assume this increment changes every v1 route.

The native session ID is not a listening device ID or ownership epoch. Refresh and
session revocation do not advance queues. Existing logout still applies the
ownership-aware pause rules. AVPlayer/receiver authorization is a separate next
milestone: do not use undocumented AVURLAsset HTTP-header options or widen receiver
grants to make short-lived access tokens appear to work. Existing stream grants
are session-bound and can expire with access credentials; no native player should
ship until the media manifest/grant lifecycle is tested through track transitions.

## Validation and rollout

Tests cover rotation/replay, concurrent refresh, absolute expiry, account-wide
revocation, cross-account isolation, session-kind separation, persisted identity,
hashed credentials, real HTTP lifecycle, malformed JSON, rate limiting, and response
conformance to the checked-in schema. Run the full release CI before merging.

Schema additions are additive. Back up accounts.db with SQLite's backup API before
first deployment, never by copying a live WAL database. Rolling back code leaves
unused tables intact; do not restore an old database over newer user activity.
Rolling back loses the new endpoints, so native clients must tolerate their absence.
Before production, use an isolated state directory and the production interpreter;
verify both HTTPS endpoints have the same server identity after promotion.

## Next increments

1. Versioned media manifest with bounded preparation, current-plus-next-two scope,
   renewable receiver authorization, range/resume and representation identity tests.
2. Catalog/listening contract, pagination, durable identifiers and conflict fixtures.
3. Qualified Apple toolchain, generated/validated Swift DTOs, Keychain coordinator,
   then one complete physical-device listening path. Server tests do not substitute
   for iPhone/iPad, interruption, AirPlay and network-transition acceptance.

The next server increment is documented in `NATIVE-PLAYBACK-CONTRACT.md`: scoped native media manifests, bounded catalog pages, and a small authoritative playback facade. The original broad catalog/identity and physical-device gates remain; see that document for exact support.
