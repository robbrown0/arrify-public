# Security reporting

Report suspected vulnerabilities using [GitHub private vulnerability reporting](https://github.com/robbrown0/arrify-public/security/advisories/new). Include the
version, affected component, reproduction steps, and potential impact. Do not
include passwords, signing keys, session tokens, or private music files in
public issues, pull requests, screenshots, or workflow logs.

Use an isolated test instance and accounts you control. Public source does not
authorize testing another person's server or account.

Release signing credentials belong in the protected `testflight` environment.
Pull-request checks must not receive signing credentials or production access.
