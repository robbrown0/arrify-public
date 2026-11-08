# Release baseline

This is the first preparation step for native iOS development. It reconciles the
player fixes previously supplied through production bind mounts and establishes
an independently buildable, tested player image. The request/acquisition helper
image is a separate follow-up; `Dockerfile.requests` still uses its historical base.

## Build and validate

Run from the repository root with Docker and Node.js 22 available:

```sh
node --test tests/*.test.cjs
docker build --target test -t arrify:test .
docker run --rm --network none --cpus=2 --memory=2g arrify:test
revision=$(git rev-parse HEAD)
docker build --build-arg VCS_REF="$revision" -t "arrify:$revision" .
bash tools/smoke_release.sh "arrify:$revision" "$revision"
```

The GitHub `Release baseline` workflow performs these checks for pull requests and
main. Tests run without network access. The runtime smoke check starts a disposable
non-root, read-only container with temporary data and no published ports or external
network. It checks startup, build identity, and MP3/AAC/FLAC encode/probe/decode.
It neither touches the production library nor starts music on a user's device.

The base image is pinned by digest; Python dependencies require wheel hashes;
Debian packages resolve from a dated snapshot. This pins build inputs, but does
not claim bit-for-bit identical OCI layers. Update the base digest, snapshot and
dependency hashes through reviewed, tested changes to receive security fixes.
Do not freeze these indefinitely. `/health` reports the source revision embedded
in the image. Local unbuilt source identifies itself as `development`.

## Candidate compatibility note

The initial baseline candidate uses Python 3.13.18 and Debian FFmpeg 5.1.9;
the inspected production image used Python 3.13.5 and FFmpeg 6.1.2. The Debian
version is maintained on its distribution branch, but the major-version change
requires playback/seek/AirPlay validation before replacing the working player.
The baseline source reconciliation does not itself require that runtime upgrade.

## Upgrade and rollback

The root `deploy.sh` and compose examples describe the original host and migration;
they are not the production deployment procedure for the current server. Do not
run them to replace the current catalog or to upgrade the current installation.

1. Build and test an image labeled with the reviewed commit, then record its digest.
2. Back up the private deployment configuration and take consistent SQLite backups
   using SQLite's backup API. Preserve data ownership and permissions.
3. Compare image `app/` and `web/` against every production code override. Resolve
   unexplained differences before replacing any mounts. Media and data mounts stay.
4. Check `/health` and active streams immediately before recreation. Defer the
   restart during playback. A zero count reduces risk but is not a drain mechanism.
5. Change only the player image in the private deployment and remove reconciled
   code mounts. Keep the prior image and private configuration for rollback.
6. Check health/build revision, catalog count, local and public HTTPS, and logs.
   Have a real iPhone/iPad verify playback, seeking, background/lock screen,
   AirPlay, and next-track caching before declaring native-device compatibility.
7. On failure, restore the saved image/configuration. This baseline introduces no
   database schema migration; do not overwrite newer listening data with backups
   unless an independently diagnosed data failure requires restoration.

## Security boundary and next work

The ordinary pinned Python slim image is a build baseline, not a hardened-image
certification. Production retains UID/GID 21012, a read-only root filesystem,
dropped capabilities, no-new-privileges, resource limits and no Docker socket.
Removing unnecessary executable utilities reduces attack surface independently of
vulnerability scanning; both matter.

Evaluate Docker Hardened Python (Debian variant), Chainguard, or Google's
Distroless as a separate, bounded change. Docker's hardened registry required
login in this environment. A smaller Python-only image is not a drop-in guarantee:
Arrify invokes FFmpeg, FFprobe and OpenSSL and needs their shared libraries.
Compare the finished image's package inventory and vulnerabilities, verify image
provenance, and run the same tests before adoption. Avoid adding a shell or package
manager back into a minimal runtime just to simplify deployment. Hardened-image
migration is not a prerequisite for continuing the approved iOS preparation.

Remaining native readiness gates include an explicit API contract, renewable
native authentication, stream/download grants, native playback interruption and
route handling, on-device cache lifecycle, and qualified macOS/Xcode CI.
This baseline does not establish those gates or replace physical-device testing.
