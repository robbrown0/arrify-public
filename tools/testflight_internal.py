#!/usr/bin/env python3
"""Assign an exact internal-only build; never touches external groups or review."""
import os
import re
import time
import urllib.parse
from testflight_beta_review import API, request, signing_token


def main():
    number, version = os.environ['BUILD_NUMBER'], os.environ['APP_VERSION']
    if not re.fullmatch(r'[1-9][0-9]*', number) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Exact version and build required')
    def api(path, method='GET', payload=None):
        return request(API+path, method, payload, signing_token())
    apps = api('/v1/apps?'+urllib.parse.urlencode({'filter[bundleId]':'com.robnets.arrify'}))['data']
    if len(apps) != 1: raise RuntimeError('Expected one app')
    app = apps[0]['id']
    for attempt in range(30):
        result = api('/v1/builds?'+urllib.parse.urlencode({'filter[app]':app,'filter[version]':number,'include':'preReleaseVersion'}))
        versions = {x['id']:x['attributes']['version'] for x in result.get('included',[]) if x['type']=='preReleaseVersions'}
        builds = [x for x in result['data'] if versions.get(x['relationships']['preReleaseVersion']['data']['id']) == version]
        if len(builds) == 1 and builds[0]['attributes']['processingState'] == 'VALID': break
        if any(x['attributes']['processingState'] in ('FAILED','INVALID') for x in builds): raise RuntimeError('Apple processing failed')
        print('Waiting for Apple build processing', flush=True); time.sleep(20)
    else: raise RuntimeError('Apple processing still pending; retry assignment later')
    build = builds[0]
    if build['attributes'].get('buildAudienceType') != 'INTERNAL_ONLY': raise RuntimeError('Refusing a build not marked internal-only')
    groups = api(f'/v1/apps/{app}/betaGroups?limit=200')['data']
    internal = [g for g in groups if g['attributes']['isInternalGroup']]
    if not internal: raise RuntimeError('No internal test groups')
    for group in internal:
        path = f"/v1/betaGroups/{group['id']}/relationships/builds"
        if not any(x['id']==build['id'] for x in api(path+'?limit=200')['data']):
            api(path,'POST',{'data':[{'type':'builds','id':build['id']}]})
    print(f'Arrify {version} ({number}) assigned to internal groups only. No beta-review submission.')

if __name__ == '__main__':
    try: main()
    except Exception as exc: raise SystemExit(f'Internal distribution stopped: {type(exc).__name__}: {exc}')
