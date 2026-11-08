#!/bin/bash
# Called only by the protected, manually dispatched TestFlight workflow.
set -euo pipefail
umask 077
: "${ASC_KEY_ID:?Missing ASC_KEY_ID}"
: "${ASC_ISSUER_ID:?Missing ASC_ISSUER_ID}"
: "${ASC_PRIVATE_KEY:?Missing ASC_PRIVATE_KEY}"
: "${RUNNER_TEMP:?This script runs on the disposable Apple CI runner}"
: "${BUILD_NUMBER:?Missing BUILD_NUMBER}"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid build number" >&2; exit 1; }
SIGNING_DIR=$(mktemp -d "$RUNNER_TEMP/arrify-signing.XXXXXX")
KEYCHAIN_PATH="$SIGNING_DIR/signing.keychain-db"
KEYCHAIN_PASSWORD=$(openssl rand -hex 32)
cleanup() {
  security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1 || true
  rm -rf "$SIGNING_DIR"
}
trap cleanup EXIT
export SIGNING_DIR
python3 - <<'PY'
import os, pathlib, plistlib
root = pathlib.Path(os.environ['SIGNING_DIR'])
(root/'AuthKey.p8').write_text(os.environ['ASC_PRIVATE_KEY'])
with (root/'ExportOptions.plist').open('wb') as f:
    plistlib.dump({'method':'app-store-connect','destination':'upload',
                  'teamID':'VW3VH2HPTV','signingStyle':'automatic',
                  'manageAppVersionAndBuildNumber':False,'uploadSymbols':True,
                  'testFlightInternalTestingOnly':False},f)
PY
openssl pkey -in "$SIGNING_DIR/AuthKey.p8" -check -noout >/dev/null
unset ASC_PRIVATE_KEY
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 3600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security list-keychains -d user -s "$KEYCHAIN_PATH"
# Export applies distribution signing using Apple's cloud-managed identity.
xcodebuild -project ios/Arrify.xcodeproj -scheme Arrify -configuration Release \
  -destination 'generic/platform=iOS' -archivePath build/Arrify.xcarchive \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER" CODE_SIGNING_ALLOWED=NO archive
xcodebuild -exportArchive -archivePath build/Arrify.xcarchive \
  -exportPath build/TestFlight -exportOptionsPlist "$SIGNING_DIR/ExportOptions.plist" \
  -allowProvisioningUpdates -authenticationKeyPath "$SIGNING_DIR/AuthKey.p8" \
  -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID"
