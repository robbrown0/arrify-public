#!/usr/bin/env python3
"""Read-only build status. Never print credentials, tester or review contact data."""
import urllib.parse
from testflight_beta_review import API, request, signing_token


def main():
    token = signing_token()
    def get(path):
        return request(API + path, token=token)
    apps = get('/v1/apps?' + urllib.parse.urlencode({'filter[bundleId]': 'com.robnets.arrify'}))['data']
    if len(apps) != 1:
        raise RuntimeError('Expected one Arrify app')
    result = get('/v1/builds?' + urllib.parse.urlencode({
        'filter[app]': apps[0]['id'], 'limit': 30, 'sort': '-uploadedDate',
        'include': 'preReleaseVersion,buildBetaDetail,betaAppReviewSubmission'}))
    included = {(x['type'], x['id']): x['attributes'] for x in result.get('included', [])}
    for build in result['data']:
        def related(name):
            ref = build.get('relationships', {}).get(name, {}).get('data')
            return included.get((ref['type'], ref['id']), {}) if ref else {}
        attrs = build['attributes']
        print('Version:', related('preReleaseVersion').get('version'),
              'build:', attrs['version'], 'processing:', attrs['processingState'],
              'audience:', attrs.get('buildAudienceType'), 'expired:', attrs.get('expired'),
              'internal:', related('buildBetaDetail').get('internalBuildState'),
              'external:', related('buildBetaDetail').get('externalBuildState'),
              'review:', related('betaAppReviewSubmission').get('betaReviewState'))
    if result.get('links', {}).get('next'):
        print('Only the latest 30 builds are shown.')


if __name__ == '__main__':
    main()
