#!/bin/bash
# Preserve requested capabilities in an unsigned archive before cloud distribution signing.
set -euo pipefail
APP_PATH=${1:?App bundle path required}
ENTITLEMENTS_PATH=${2:?Entitlements path required}
# Sign nested code first. There are no third-party dynamic frameworks in Arrify.
if [ -d "$APP_PATH/Frameworks" ]; then
  while IFS= read -r -d '' library; do codesign --force --sign - "$library"; done < <(find "$APP_PATH/Frameworks" -type f -name '*.dylib' -print0)
fi
codesign --force --sign - --generate-entitlement-der "$APP_PATH/PlugIns/ArrifyLiveActivity.appex"
codesign --force --sign - --generate-entitlement-der --entitlements "$ENTITLEMENTS_PATH" "$APP_PATH"
codesign --display --entitlements - --xml "$APP_PATH" 2>/dev/null | python3 -c 'import plistlib,sys; assert plistlib.loads(sys.stdin.buffer.read()).get("aps-environment") == "development", "Archive lost requested push entitlement"'
