from urllib.parse import urlencode
from .matching import title, primary, norm, versions
from datetime import date

class Resolver:
    def __init__(self,http,contact,corrections=None):
        self.http=http
        self.headers={'User-Agent':f'HitDiscovery/0.1 ({contact})'}
        self.contact=contact
        self.corrections=corrections or {}
    def get(self,endpoint,**params):
        if not self.contact: raise ValueError('Set MUSICBRAINZ_CONTACT to a valid contact email or URL')
        return self.http.json('https://musicbrainz.org/ws/2/'+endpoint+'?'+urlencode({'fmt':'json',**params}),
                              headers=self.headers,interval=1.1,cache_ttl=86400 if "query" in params else 86400*30)
    def resolve(self,t):
        def quote(s): return '"'+s.replace('\\','\\\\').replace('"','\\"')+'"'
        query='recording:'+quote(t['title'])+' AND artist:'+quote(t['artist'])
        found=self.get('recording/',query=query,limit=25).get('recordings',[])
        if not found and primary(t['artist'])!=norm(t['artist']):
            found=self.get('recording/',query='recording:'+quote(t['title'].split(' (feat.')[0])+' AND artist:'+quote(primary(t['artist'])),limit=25).get('recordings',[])
        exact=[]
        for r in found:
            credits=[c['artist']['name'] for c in r.get('artist-credit',[]) if isinstance(c,dict) and 'artist' in c]
            if title(r['title'])!=title(t['title']): continue
            if not any(primary(t['artist'])==primary(a) for a in credits): continue
            if versions(r['title']+' '+r.get('disambiguation',''))!=versions(t['title']): continue
            if int(r.get('score',0))<90: continue
            exact.append(r)
        if not exact: return t|{'resolution_status':'unresolved','reason':'No confident recording match'}
        # Pick original studio recording, avoiding Atmos/remix disambiguations.
        def recording_order(recording):
            studio=any(rel.get('status')=='Official' and rel.get('release-group',{}).get('primary-type')=='Album'
                       and not set(rel.get('release-group',{}).get('secondary-types',[])) & {'Compilation','Live','Remix','DJ-mix'}
                       for rel in recording.get('releases',[]))
            disambiguation=norm(recording.get('disambiguation',''))
            variant_penalty=0 if disambiguation in ('','explicit','album version','original') else 1 if disambiguation=='clean' else 2
            return (not studio,variant_penalty,recording.get('first-release-date','9999'),-int(recording.get('score',0)))
        exact.sort(key=recording_order)
        r=self.get('recording/'+exact[0]['id'],inc='artists+releases+release-groups+isrcs')
        released=r.get('first-release-date') or exact[0].get('first-release-date','')
        # The earliest known release across exact recordings prevents remasters becoming "new" songs.
        dates=[x['first-release-date'] for x in exact if x.get('first-release-date')]
        if dates: released=min([released] + dates) if released else min(dates)
        correction=self.corrections.get(primary(t['artist'])+'|'+title(t['title']))
        if correction:
            date.fromisoformat(correction['release_date'])
            if not correction.get('source_url','').startswith('https://'): raise ValueError('Metadata correction requires a source URL')
            t=t|{'release_date_before_correction':released,'release_date_source':correction['source_url']}
            released=correction['release_date']
        channel=t.get('discovery_channel','')
        earliest='1900' if channel in ('spectrum','imported-playlist') or channel.startswith('sirius-') else '2015'
        if not released or released[:4]<earliest or released[:10]>date.today().isoformat():
            return t|{'resolution_status':'out-of-window','release_date':released,'recording_id':r['id']}
        credits=[x for x in r['artist-credit'] if isinstance(x,dict) and 'artist' in x]
        aid=credits[0]['artist']['id']
        albums=[]
        for rel in r.get('releases',[]):
            rg=rel.get('release-group',{}); secondary=rg.get('secondary-types',[])
            if rel.get('status')!='Official' or any(x in secondary for x in ('Compilation','Live','Remix','DJ-mix')): continue
            typ=rg.get('primary-type','')
            if typ not in ('Album','EP','Single'): continue
            date_text=rel.get('date','9999')
            if date_text[:10]>date.today().isoformat(): continue
            if len(date_text)>=10 and len(released)>=10 and date_text[:10]<released[:10]: continue
            relcredits=rel.get('artist-credit',[])
            if relcredits and not any(x.get('artist',{}).get('id')==aid for x in relcredits if isinstance(x,dict)): continue
            deluxe=any(s in norm(rel['title']+' '+rel.get('disambiguation','')) for s in ('deluxe','expanded','anniversary','bonus'))
            albums.append(((0 if typ=='Album' else 1 if typ=='EP' else 2,deluxe,date_text,rel.get('country')!='US'),rel,rg))
        if not albums: return t|{'resolution_status':'unresolved','reason':'No suitable official release containing recording','recording_id':r['id'],'release_date':released}
        albums.sort(key=lambda x:x[0]); _,rel,rg=albums[0]
        return t|{'resolution_status':'resolved','release_date':released,'recording_id':r['id'],'isrcs':r.get('isrcs',[]),
                  'artist_mbid':aid,'album':rg['title'],'album_type':rg.get('primary-type'), 'release_group_id':rg['id'],
                  'release_id':rel['id'],'metadata_url':'https://musicbrainz.org/recording/'+r['id']}

    def album_release(self,group):
        """Find an actual edition containing every identified missing song."""
        wanted={title(s['title']) for s in group['songs']}
        ids=list(dict.fromkeys(s['release_id'] for s in group['songs'] if s.get('release_id')))
        if not ids: return {'release_validation':'missing-release-metadata'}
        if len(wanted)==1:
            return {'release_validation':'recording-linked-release','release_id':ids[0]}
        matches=[]
        for rid in ids[:5]:
            r=self.get('release/'+rid,inc='recordings')
            titles={title(track.get('recording',{}).get('title') or track.get('title','')) for medium in r.get('media',[]) for track in medium.get('tracks',[])}
            if wanted.issubset(titles):
                deluxe=any(x in norm(r.get('title','')+' '+r.get('disambiguation','')) for x in ('deluxe','expanded','anniversary'))
                matches.append((deluxe,len(titles),rid,r.get('title','')))
        if not matches: return {'release_validation':'no-verified-edition-containing-all-hits'}
        matches.sort(); _,_,rid,label=matches[0]
        return {'release_validation':'all-hit-titles-verified-on-release','release_id':rid,'edition_title':label}
