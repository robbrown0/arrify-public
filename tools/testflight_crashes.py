#!/usr/bin/env python3
"""Read TestFlight crash stacks without publishing tester contact data or full logs."""
import json
import re
import urllib.parse
from testflight_beta_review import API, request, signing_token


def summarize(text):
    decoder = json.JSONDecoder()
    objects = []
    remaining = text.strip()
    while remaining:
        try:
            value, end = decoder.raw_decode(remaining)
        except ValueError:
            break
        if isinstance(value, dict):
            objects.append(value)
        remaining = remaining[end:].strip()
    report = next((x for x in objects if 'threads' in x), None)
    if report is None:
        # Legacy symbolicated text: only exception classifications and stack frames.
        for line in text.splitlines():
            if re.match(r'^(Exception Type|Exception Codes|Termination Reason|Triggered by Thread):', line):
                print(line[:300])
            elif re.match(r'^\d+\s+(Arrify|libswiftCore\.dylib|AVFoundation|AVFCore|CoreMedia|AudioToolbox|ImageIO)\s+0x', line):
                print(line[:500])
        return
    info = report.get('bundleInfo', {})
    print('App version:', info.get('CFBundleShortVersionString'), 'build:', info.get('CFBundleVersion'))
    exception = report.get('exception', {})
    print('Exception:', {k: exception.get(k) for k in ('type', 'signal', 'codes')})
    termination = report.get('termination', {})
    print('Termination:', {k: termination.get(k) for k in ('namespace', 'code')})
    images = report.get('usedImages', [])
    fault = report.get('faultingThread')
    for index, thread in enumerate(report.get('threads', [])):
        if index != fault and not thread.get('triggered'):
            continue
        print('Faulting thread:', index)
        for frame in thread.get('frames', [])[:40]:
            image_index = frame.get('imageIndex', -1)
            image = images[image_index] if isinstance(image_index, int) and 0 <= image_index < len(images) else {}
            print(json.dumps({'image': image.get('name'), 'symbol': frame.get('symbol'),
                              'offset': frame.get('imageOffset'), 'symbolOffset': frame.get('symbolLocation'),
                              'uuid': image.get('uuid')}, ensure_ascii=True))


def main():
    token = signing_token()
    def get(path):
        return request(API + path, token=token)
    apps = get('/v1/apps?' + urllib.parse.urlencode({'filter[bundleId]': 'com.robnets.arrify'}))['data']
    if len(apps) != 1:
        raise RuntimeError('Expected one Arrify app')
    query = urllib.parse.urlencode({'limit': 30, 'fields[betaFeedbackCrashSubmissions]':
        'createdDate,deviceModel,osVersion,appUptimeInMilliseconds,build,crashLog', 'include': 'build'})
    response = get('/v1/apps/' + apps[0]['id'] + '/betaFeedbackCrashSubmissions?' + query)
    builds = {x['id']: x.get('attributes', {}).get('version') for x in response.get('included', []) if x['type'] == 'builds'}
    print('Crash submissions returned:', len(response['data']))
    for entry in response['data']:
        attrs = entry.get('attributes', {})
        ref = entry.get('relationships', {}).get('build', {}).get('data') or {}
        print('Submission:', {k: attrs.get(k) for k in ('createdDate', 'deviceModel', 'osVersion', 'appUptimeInMilliseconds')}, 'build:', builds.get(ref.get('id')))
        crash = get('/v1/betaFeedbackCrashSubmissions/' + entry['id'] + '/crashLog')
        summarize(crash.get('data', {}).get('attributes', {}).get('logText', ''))
    if response.get('links', {}).get('next'):
        print('Additional submissions are available beyond this page.')


if __name__ == '__main__':
    main()
