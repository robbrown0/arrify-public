#!/usr/bin/env python3
"""Submit an explicitly selected TestFlight build using protected CI credentials."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.appstoreconnect.apple.com'
DEMO = 'https://arrify-demo.robnets.com'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

OPENER = urllib.request.build_opener(NoRedirect())

def request(url, method='GET', payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        # Do not echo request bodies, credentials, or arbitrary API response details.
        try:
            errors = json.loads(exc.read()).get('errors', [])
            codes = ','.join(str(e.get('code', '')) for e in errors)
        except (ValueError, AttributeError):
            codes = ''
        raise RuntimeError(f'{method} {urllib.parse.urlsplit(url).path}: HTTP {exc.code} {codes}') from None

def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=')

def der_signature_to_raw(signature):
    # OpenSSL emits an ASN.1 DER sequence of two positive ECDSA integers.
    def length(offset):
        n = signature[offset]; offset += 1
        if n & 128:
            size = n & 127
            n = int.from_bytes(signature[offset:offset+size], 'big'); offset += size
        return n, offset
    if signature[0] != 0x30:
        raise ValueError('Invalid ECDSA signature')
    _, offset = length(1)
    parts = []
    for _ in range(2):
        if signature[offset] != 2:
            raise ValueError('Invalid ECDSA integer')
        size, offset = length(offset + 1)
        parts.append(int.from_bytes(signature[offset:offset+size], 'big').to_bytes(32, 'big'))
        offset += size
    if offset != len(signature):
        raise ValueError('Unexpected ECDSA bytes')
    return b''.join(parts)

def signing_token():
    now = int(time.time())
    header = {'alg': 'ES256', 'kid': os.environ['ASC_KEY_ID'], 'typ': 'JWT'}
    claims = {'iss': os.environ['ASC_ISSUER_ID'], 'iat': now, 'exp': now+600, 'aud': 'appstoreconnect-v1'}
    message = b64(json.dumps(header).encode()) + b'.' + b64(json.dumps(claims).encode())
    with tempfile.TemporaryDirectory(prefix='arrify-beta-') as directory:
        key = Path(directory)/'key.p8'
        key.write_text(os.environ['ASC_PRIVATE_KEY']); key.chmod(0o600)
        signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(key)], input=message, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    return (message + b'.' + b64(der_signature_to_raw(signature))).decode()

def resource(kind, attributes, identifier=None, relationships=None):
    value = {'type': kind, 'attributes': attributes}
    if identifier:
        value['id'] = identifier
    if relationships:
        value['relationships'] = relationships
    return {'data': value}

def relation(kind, identifier):
    return {'data': {'type': kind, 'id': identifier}}

def ensure_no_other_review(api, app, build):
    # Check all current review states before changing metadata or group access.
    reviews = api('/v1/builds?' + urllib.parse.urlencode({'filter[app]': app, 'limit': 200, 'include': 'betaAppReviewSubmission'}))
    if reviews.get('links', {}).get('next'):
        raise RuntimeError('Review history requires pagination before another submission')
    states = {x['id']: x['attributes']['betaReviewState'] for x in reviews.get('included', []) if x['type'] == 'betaAppReviewSubmissions'}
    for candidate in reviews['data']:
        reference = candidate.get('relationships', {}).get('betaAppReviewSubmission', {}).get('data')
        review_state = states.get(reference['id']) if reference else None
        if review_state:
            print('Existing build', candidate['attributes']['version'], 'review state:', review_state)
        if candidate['id'] != build and review_state in ('WAITING_FOR_REVIEW', 'IN_REVIEW'):
            raise RuntimeError('Another build is awaiting beta review; withdraw it in App Store Connect before submitting this build')

def main():
    number = os.environ['BUILD_NUMBER']; version = os.environ['APP_VERSION']
    if not re.fullmatch(r'[1-9][0-9]*', number) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Specify an exact build number and app version')
    config = json.loads(os.environ['ASC_BETA_REVIEW_CONFIG'])
    # Validate the real review endpoint and its limited account before submission.
    health = request(DEMO+'/health')
    if health.get('catalog_tracks') != 6:
        raise RuntimeError('Review server must expose exactly six original demo tracks')
    pair = request(DEMO+'/api/v1/native/sessions', 'POST', {'username': config['username'], 'password': config['password'], 'device_name': 'Beta review readiness'})
    try:
        catalog = request(DEMO+'/api/v1/native/catalog', token=pair['access_token'])
        expected = {'First Light', 'Open Windows', 'Bright Steps', 'Quiet Water', 'Evening Drift', 'Last Lantern'}
        if catalog.get('total') != 6 or {x.get('title') for x in catalog.get('items', [])} != expected or any(x.get('artist') != 'Arrify Studio' for x in catalog.get('items', [])):
            raise RuntimeError('Review account must contain only the six original demo tracks')
    finally:
        request(DEMO+'/api/v1/native/sessions/revoke', 'POST', {'session_id': pair['session_id']}, pair['access_token'])
    token = signing_token()
    def api(path, method='GET', payload=None):
        return request(API+path, method, payload, token)
    apps = api('/v1/apps?'+urllib.parse.urlencode({'filter[bundleId]': 'com.robnets.arrify'}))['data']
    if len(apps) != 1:
        raise RuntimeError('Expected one Arrify app')
    app = apps[0]['id']
    result = api('/v1/builds?'+urllib.parse.urlencode({'filter[app]': app, 'filter[version]': number, 'include': 'preReleaseVersion'}))
    builds = result['data']
    versions = {x['id']: x['attributes']['version'] for x in result.get('included', []) if x['type']=='preReleaseVersions'}
    builds = [b for b in builds if versions.get(b['relationships']['preReleaseVersion']['data']['id']) == version]
    if len(builds) != 1 or builds[0]['attributes']['processingState'] != 'VALID':
        raise RuntimeError('Selected build is absent or still processing: '+str([(b.get('attributes', {}).get('version'), b.get('attributes', {}).get('processingState')) for b in builds]))
    build = builds[0]['id']
    if builds[0]['attributes'].get('buildAudienceType') == 'INTERNAL_ONLY':
        raise RuntimeError('Selected build only supports internal testing')
    ensure_no_other_review(api, app, build)
    detail = api(f'/v1/apps/{app}/betaAppReviewDetail')['data']
    notes = ('Review server: '+DEMO+'. On the sign-in screen replace the Server address with this URL, then use the supplied review login. '
             'Open Library > Songs and play First Light by Arrify Studio. The isolated server contains six original synthesized instrumentals across Daylight and Afterglow albums, original artwork, two demo channels, and Feel Good / Unwind mood playlists. No commercial recordings or external music services are used on the demo server. '
             'Test browsing, search, next/previous, shuffle, repeat, seeking, background audio, lock-screen controls, and AirPlay. Privacy policy: https://arrify.robnets.com/privacy. The policy is also linked on sign-in and in Settings. Other users connect to their own Arrify server and approved account. No purchase or subscription is required. Account deletion: Settings > Account > Delete account, current password, permanent confirmation. Pending accounts can use Delete an existing account on sign-in. Deletion is real and irreversible, including for the review account.')
    attrs = {'contactFirstName': config['first_name'], 'contactLastName': config['last_name'], 'contactEmail': config['email'], 'contactPhone': config['phone'],
             'demoAccountRequired': True, 'demoAccountName': config['username'], 'demoAccountPassword': config['password'], 'notes': notes}
    api('/v1/betaAppReviewDetails/'+detail['id'], 'PATCH', resource('betaAppReviewDetails', attrs, detail['id']))
    localizations = api(f'/v1/apps/{app}/betaAppLocalizations')['data']
    localized = next((x for x in localizations if x['attributes']['locale']=='en-US'), None)
    attrs = {'description': 'Arrify is a personal music player for an Arrify server. Browse songs, albums, artists, playlists, channels, and moods; listen with background playback, lock-screen controls, and AirPlay.', 'feedbackEmail': config['email'], 'privacyPolicyUrl': 'https://arrify.robnets.com/privacy'}
    if localized:
        api('/v1/betaAppLocalizations/'+localized['id'], 'PATCH', resource('betaAppLocalizations', attrs, localized['id']))
    else:
        api('/v1/betaAppLocalizations', 'POST', resource('betaAppLocalizations', {**attrs, 'locale':'en-US'}, relationships={'app':relation('apps', app)}))
    localizations = api(f'/v1/builds/{build}/betaBuildLocalizations')['data']
    localized = next((x for x in localizations if x['attributes']['locale']=='en-US'), None)
    attrs = {'whatsNew': '27a (build 28): iPad-only mini-player layout correction. Center the native volume control vertically with the adjacent playback and AirPlay controls. iPhone and full-screen player layouts are unchanged. Please verify alignment on iPad in portrait and landscape; volume remains controlled by Apple’s native slider.'}
    if localized:
        api('/v1/betaBuildLocalizations/'+localized['id'], 'PATCH', resource('betaBuildLocalizations', attrs, localized['id']))
    else:
        api('/v1/betaBuildLocalizations', 'POST', resource('betaBuildLocalizations', {**attrs, 'locale':'en-US'}, relationships={'build':relation('builds', build)}))
    groups = api(f'/v1/apps/{app}/betaGroups?limit=200')['data']
    external = [g for g in groups if not g['attributes']['isInternalGroup'] and g['attributes']['name']=='Friends & Family']
    if len(external) != 1:
        raise RuntimeError('Expected the existing Friends & Family external group')
    for group in [g for g in groups if g['attributes']['isInternalGroup']] + external:
        if group['attributes']['isInternalGroup'] and group['attributes'].get('hasAccessToAllBuilds'):
            continue
        linked = api(f"/v1/betaGroups/{group['id']}/relationships/builds?limit=200")['data']
        if not any(item['id'] == build for item in linked):
            api(f"/v1/betaGroups/{group['id']}/relationships/builds", 'POST', {'data':[{'type':'builds','id':build}]})
    existing = api('/v1/betaAppReviewSubmissions?'+urllib.parse.urlencode({'filter[build]':build}))['data']
    if existing:
        state = existing[0]['attributes']['betaReviewState']
    else:
        submission = api('/v1/betaAppReviewSubmissions','POST',{'data':{'type':'betaAppReviewSubmissions','relationships':{'build':relation('builds',build)}}})
        state = submission['data']['attributes']['betaReviewState']
    print(f'Arrify {version} ({number}): beta review state {state}; internal and Friends & Family groups assigned.')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never log the protected configuration or a signing command environment.
        print(f'Beta submission stopped: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
