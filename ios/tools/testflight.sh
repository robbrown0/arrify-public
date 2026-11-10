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
    plistlib.dump({'method':'app-store-connect','destination':'export',
                  'teamID':'VW3VH2HPTV','signingStyle':'automatic',
                  'manageAppVersionAndBuildNumber':False,'uploadSymbols':True,
                  'testFlightInternalTestingOnly':os.environ.get('INTERNAL_ONLY', 'true') == 'true'},f)
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
bash ios/tools/prepare_entitlements.sh build/Arrify.xcarchive/Products/Applications/Arrify.app ios/App/Arrify.entitlements
xcodebuild -exportArchive -archivePath build/Arrify.xcarchive \
  -exportPath build/TestFlight -exportOptionsPlist "$SIGNING_DIR/ExportOptions.plist" \
  -allowProvisioningUpdates -authenticationKeyPath "$SIGNING_DIR/AuthKey.p8" \
  -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID"

# Inspect the exact distribution-signed payload before uploading it.
IPA_PATH=$(find build/TestFlight -maxdepth 1 -name '*.ipa' -print -quit)
[ -n "$IPA_PATH" ] || { echo "Missing exported IPA" >&2; exit 1; }
VERIFY_DIR="$SIGNING_DIR/verify"
mkdir "$VERIFY_DIR"
unzip -q "$IPA_PATH" -d "$VERIFY_DIR"
codesign --display --entitlements - --xml "$VERIFY_DIR/Payload/Arrify.app" 2>/dev/null > "$SIGNING_DIR/app-entitlements.plist"
security cms -D -i "$VERIFY_DIR/Payload/Arrify.app/embedded.mobileprovision" > "$SIGNING_DIR/profile.plist"
python3 - <<'PYVERIFY'
import os, pathlib, plistlib
root=pathlib.Path(os.environ['SIGNING_DIR'])
app=plistlib.loads((root/'app-entitlements.plist').read_bytes())
profile=plistlib.loads((root/'profile.plist').read_bytes())['Entitlements']
assert app.get('aps-environment') == 'production', 'Distribution app is missing production push entitlement'
assert profile.get('aps-environment') == 'production', 'Provisioning profile is missing production push entitlement'
assert app.get('application-identifier') == 'VW3VH2HPTV.com.robnets.arrify', 'Unexpected signed app identifier'
print('Verified production push entitlement in the signed app and provisioning profile.')
PYVERIFY
cp "$SIGNING_DIR/AuthKey.p8" "$SIGNING_DIR/AuthKey_${ASC_KEY_ID}.p8"
export API_PRIVATE_KEYS_DIR="$SIGNING_DIR"
xcrun altool --upload-app --type ios --file "$IPA_PATH" --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID"
