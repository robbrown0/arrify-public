# Account security release

Public web and native signup require a 15–128 character password, a unique email,
email verification and independent administrator approval. Existing password hashes
and login behavior remain compatible. Internal `Accounts.register` is a storage/setup
primitive; HTTP signup always calls `register_public`. Bootstrap remains LAN-only.

Password UI supports autofill, paste, show/hide, and confirmation feedback. A green
length check is deliberately not a claim that a password is unguessable. Server-side
policy rejects common passwords (bundled SecLists 10k list, MIT license alongside it)
and predictable repeated characters. Spaces are accepted; no composition rules.
Source: https://github.com/danielmiessler/SecLists/tree/master/Passwords/Common-Credentials

Email grants use 256-bit random tokens, SHA-256 digests at rest, a 30-minute expiry,
purpose and email binding, and atomic single-use completion. Reset completion
revokes web and native sessions and other email grants, and invalidates an existing
owner-assisted recovery grant. New verification does not approve an account.
A changed email invalidates older grants and requires verification for recovery.

Links use the configured HTTPS public origin. Opening a link exchanges its fragment
for a Secure, HttpOnly, SameSite=Strict cookie scoped to the email API. The browser
removes the fragment immediately. Refresh works without putting tokens in local
storage. Opening a link does not consume it; a POST confirmation does. The browser
API checks Origin; public native requests have per-peer and per-identifier limits.
Responses to reset requests do not disclose account existence. Frozen and deleted
accounts cannot obtain or redeem grants. Unknown and unverified addresses do not
receive password reset messages.

SMTP uses certificate-validated STARTTLS, a dedicated mailbox and a secret file.
Set ARRIFY_SMTP_CONFIG to the container path of a private JSON file containing host,
port, username, from_address, and password_file (the container path of the password).
Mount these two files read-only for runtime UID/GID 21012. Do not commit credentials,
include them in images, or use them in CI. The configured server origin supplies links.
Delivery runs in a bounded background queue; failures log no recipient or message data.
The queue is in memory: restart or delivery failure may require requesting another
link. No tracking pixels or external email resources are included.

For this installation only, the owner authorized trusting existing email addresses.
Setting `trust_existing_emails: true` in private SMTP configuration performs a one-time,
transactional migration of preexisting accounts. A durable migration marker prevents
later changed addresses from being trusted on restart. New public accounts are excluded.
Do not enable this setting on other installations without an equivalent owner decision.

Web Settings exposes permanent deletion with current-password reauthentication and
explicit confirmation. It uses the existing erasure ledger and data-deletion mechanism.

Guidance consulted:
- https://pages.nist.gov/800-63-4/sp800-63b.html
- https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- https://developer.apple.com/support/offering-account-deletion-in-your-app/

These design choices are not a certification of NIST compliance.
