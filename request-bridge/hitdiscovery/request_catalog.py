"""Song-first metadata search with a recording-linked, pinned album destination."""
import re
import uuid
from datetime import date
from .matching import norm,primary,versions

ID=re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
def quote(value):return '"'+value.replace('\\','\\\\').replace('"','\\"')+'"'
def query_text(value):
    words=re.findall(r"\w+(?:['’]\w+)*",value,flags=re.UNICODE)[:16]
    if len(words)>2:words=[w for w in words if w.casefold()!='by']
    terms=' AND '.join('(recording:'+quote(w)+' OR artist:'+quote(w)+')' for w in words)
    return '(recording:'+quote(value)+'^6 OR artist:'+quote(value)+'^4'+(' OR ('+terms+')' if terms else '')+')'
def credits(recording):
    return [c['artist'] for c in recording.get('artist-credit',[]) if isinstance(c,dict) and isinstance(c.get('artist'),dict)]
def choose_release(recording):
    artists=credits(recording)
    if not artists:return None
    aid=artists[0].get('id');choices=[]
    for release in recording.get('releases',[]):
        group=release.get('release-group',{});typ=group.get('primary-type')
        if release.get('status')!='Official' or typ not in ('Album','EP','Single'):continue
        if set(group.get('secondary-types',[]))&{'Compilation','Live','Remix','DJ-mix'}:continue
        if not all(ID.fullmatch(x or '') for x in (release.get('id'),group.get('id'),aid)):continue
        if not group.get('title'):continue
        when=release.get('date') or group.get('first-release-date') or recording.get('first-release-date','')
        if not when or when>date.today().isoformat():continue
        rc=credits(release)
        if rc and not any(a.get('id')==aid for a in rc):continue
        deluxe=any(w in norm(release.get('title','')+' '+release.get('disambiguation','')) for w in ('deluxe','expanded','anniversary','bonus'))
        order=(('Album','EP','Single').index(typ),deluxe,when,release.get('country')!='US',release['id'])
        choices.append((order,release,group))
    if not choices:return None
    _,release,group=min(choices,key=lambda x:x[0])
    return {'resolution_status':'resolved','recording_id':recording['id'],'title':recording['title'],'artist':artists[0]['name'],
            'artist_mbid':aid,'album':group['title'],'album_year':(group.get('first-release-date') or release.get('date',''))[:4],'album_type':group['primary-type'],'release_group_id':group['id'],
            'release_id':release['id'],'release_date':recording.get('first-release-date') or release.get('date',''),
            'isrcs':recording.get('isrcs',[]),'metadata_url':'https://musicbrainz.org/recording/'+recording['id']}

def catalog(query,get,offset=0):
    q=norm(query);qwords=set(q.split());wanted_versions=versions(query)
    artists=get('artist/',query='artist:'+quote(query),limit=5).get('artists',[])
    exact=[a for a in artists if norm(a.get('name',''))==q and int(a.get('score',0))>=95 and ID.fullmatch(a.get('id',''))]
    artist_id=exact[0]['id'] if exact else None
    query_value=('(arid:'+artist_id+' OR artist:'+quote(exact[0]['name'])+') AND primarytype:album AND status:official AND video:false') if artist_id else query_text(query)
    initial=get('recording/',query=query_value,limit=100)
    found=initial.get('recordings',[])
    compound_primary=None
    if not artist_id:
        # A full artist/title agreement supplies a structured lead. An album
        # sharing the song title is only a search lead; linkage is verified below.
        for r in found:
            cs=credits(r)
            if not cs:continue
            name=primary(cs[0].get('name',''));t=norm(r.get('title',''))
            if not name or not t:continue
            remainder=q[len(name):].strip() if q.startswith(name+' ') else q[:-len(name)].strip() if q.endswith(' '+name) else ''
            remainder=re.sub(r'^(by )|( by)$','',remainder).strip()
            if remainder!=t:continue
            if len(name.split())>=2:compound_primary=name
            try:
                extra=get('recording/',query='recording:'+quote(r['title'])+' AND artist:'+quote(cs[0]['name'])+' AND release:'+quote(r['title']),limit=25).get('recordings',[])
                found=list({x['id']:x for x in found+extra if x.get('id')}.values())
            except Exception:pass
            break
    def rank(r):
        names=' '.join(a.get('name','') for a in credits(r));t=norm(r.get('title',''));a=norm(names)
        overlap=len(qwords&set((t+' '+a).split()))
        studio=any(rel.get('release-group',{}).get('primary-type')=='Album' and not set(rel.get('release-group',{}).get('secondary-types',[]))&{'Compilation','Live','Remix','DJ-mix'} for rel in r.get('releases',[]))
        pa=primary(credits(r)[0].get('name',''))
        compound_artist=bool(pa and (' '+q+' ').find(' '+pa+' ')>=0 and q!=pa and any(w in set(t.split()) for w in qwords-set(pa.split())))
        return (not compound_artist,(not any(primary(x.get('name',''))==primary(exact[0]['name']) for x in credits(r)) if artist_id else t!=q),(t!=q if artist_id else a!=q),-overlap,versions(r.get('title','')+' '+r.get('disambiguation',''))!=wanted_versions,not studio,-int(r.get('score',0)),r.get('first-release-date','9999'),-len(r.get('releases',[])),t,a,r.get('id',''))
    found.sort(key=rank);items=[];seen=set();unverified={};lookups=0;position=offset;incomplete=False
    for candidate in found[offset:]:
        if len(items)>=6 or lookups>=8:break
        position+=1
        if not ID.fullmatch(candidate.get('id','')) or not credits(candidate):continue
        if compound_primary and not any(primary(a.get('name',''))==compound_primary for a in credits(candidate)):continue
        if artist_id and not any(a.get('id')==artist_id or primary(a.get('name',''))==primary(exact[0]['name']) for a in credits(candidate)):continue
        variant=versions(candidate.get('title','')+' '+candidate.get('disambiguation',''))
        if variant and not wanted_versions:continue
        key=(norm(candidate.get('title','')),primary(credits(candidate)[0].get('name','')),tuple(sorted(variant)))
        if key in seen:continue
        lookups+=1
        record=dict(candidate)
        try:record['releases']=get('release/',recording=candidate['id'],inc='release-groups+artist-credits',status='official',limit=100).get('releases',[])
        except Exception:
            incomplete=True;position-=1;break
        if record.get('id')!=candidate['id'] or not record.get('title') or not credits(record):continue
        resolved=choose_release(record)
        item={'selection':record['id'],'recording_id':record['id'],'kind':'Song','title':record['title'],'artist':credits(record)[0]['name'],
              'album':resolved['album'] if resolved else '', 'requestable':bool(resolved),
              'reason':'' if resolved else 'No verified official album, EP, or single is available for this recording.',
              'version':record.get('disambiguation','')}
        if resolved:
            item.update(album_type=resolved['album_type'],year=resolved['album_year'],resolved=resolved)
            item['selection']=str(uuid.uuid5(uuid.NAMESPACE_URL,'arrify-request:'+record['id']+':'+resolved['release_group_id']))
        if resolved:
            seen.add(key);items.append(item)
        else:unverified.setdefault(key,item)
    items.extend(item for key,item in unverified.items() if key not in seen and len(items)<10)
    return {'items':items[:6],'next_offset':position if position<len(found) and (position>offset or incomplete) else None,'incomplete':incomplete}


def recording_selection(recording_id,get):
    """Prepare one known recording through the same official-release rules as search."""
    if not ID.fullmatch(recording_id or ''):raise ValueError('Invalid recording.')
    record=get('recording/'+recording_id,inc='artist-credits+isrcs')
    if record.get('id')!=recording_id or not record.get('title') or not credits(record):raise ValueError('Recording could not be verified.')
    record['releases']=get('release/',recording=recording_id,inc='release-groups+artist-credits',status='official',limit=100).get('releases',[])
    resolved=choose_release(record)
    item={'selection':recording_id,'recording_id':recording_id,'kind':'Song','title':record['title'],'artist':credits(record)[0]['name'],
          'album':resolved['album'] if resolved else '', 'requestable':bool(resolved),'reason':'' if resolved else 'No verified official album is available for this recording.'}
    if resolved:item.update(selection=str(uuid.uuid5(uuid.NAMESPACE_URL,'arrify-request:'+recording_id+':'+resolved['release_group_id'])),album_type=resolved['album_type'],year=resolved['album_year'],resolved=resolved)
    return {'items':[item],'next_offset':None,'incomplete':False}


def song_selection(artist,song,get):
    """Resolve a named chart song to one verified official release, without a UI picker."""
    if not isinstance(artist,str) or not isinstance(song,str) or not artist.strip() or not song.strip() or len(artist)>500 or len(song)>500:raise ValueError('Invalid song identity.')
    from .matching import title as clean_title
    def artist_identity(value):
        return ' '.join(re.sub(r'\b(?:and|feat|ft|featuring|with|x)\b',' ',norm(value)).split())
    def full_credit(record):
        return ' '.join(c.get('name',c.get('artist',{}).get('name',''))+c.get('joinphrase','') for c in record.get('artist-credit',[]) if isinstance(c,dict))
    guests=re.findall(r'[\[(](?:feat\.?|ft\.?|featuring)\s+([^\])]+)',song,re.I)
    expected=artist_identity(artist)
    for guest in guests:
        if artist_identity(guest) not in expected:expected+=' '+artist_identity(guest)
    def variant(value):return versions(value)|set(re.findall(r'\b(?:edit|extended|rerecorded)\b',norm(value)))
    wanted_variant=variant(song)
    def matches(record):
        return clean_title(record.get('title',''))==clean_title(song) and artist_identity(full_credit(record))==expected and variant(record.get('title','')+' '+record.get('disambiguation',''))==wanted_variant and not record.get('video',False)
    result=get('recording/',query='recording:'+quote(clean_title(song))+' AND (artist:'+quote(artist)+' OR artist:'+quote(primary(artist))+') AND status:official AND video:false',limit=25)
    rows=result.get('recordings',[])
    if result.get('recording-count',len(rows))>len(rows):raise ValueError('This song has too many possible recordings to verify automatically. Nothing was requested.')
    candidates={r['id']:r for r in rows if isinstance(r,dict) and ID.fullmatch(r.get('id','')) and matches(r)}
    if not candidates or len(candidates)>5:raise ValueError('The exact song and version could not be verified. Nothing was requested.')
    choices=[]
    for rid in sorted(candidates):
        record=get('recording/'+rid,inc='artists+isrcs')
        if record.get('id')!=rid or not matches(record):raise ValueError('The song identity changed during verification. Nothing was requested.')
        record['releases']=get('release/',recording=rid,inc='release-groups+artist-credits',status='official',limit=100).get('releases',[])
        resolved=choose_release(record)
        if resolved:choices.append((record,resolved))
    if choices:
        preferred=min(('Album','EP','Single').index(r['album_type']) for _,r in choices)
        choices=[pair for pair in choices if ('Album','EP','Single').index(pair[1]['album_type'])==preferred]
    groups={r['release_group_id'] for _,r in choices}
    # Multiple recording rows are acceptable only when they lead to the same
    # official album. Never choose an unrelated cover or a different version.
    if len(groups)!=1:raise ValueError('A single official album containing this song could not be verified. Nothing was requested.')
    record,resolved=min(choices,key=lambda pair:(('Album','EP','Single').index(pair[1]['album_type']),pair[1]['release_date'],pair[0]['id']))
    item={'selection':str(uuid.uuid5(uuid.NAMESPACE_URL,'arrify-request:'+record['id']+':'+resolved['release_group_id'])),'recording_id':record['id'],'kind':'Song','title':record['title'],'artist':credits(record)[0]['name'],'album':resolved['album'],'album_type':resolved['album_type'],'year':resolved['album_year'],'requestable':True,'resolved':resolved}
    return {'items':[item],'requested':{'artist':artist,'title':song},'next_offset':None}


def album_tracks(selected,get,explicit=False):
    """Expand only a previously verified release; preserve its recording identities."""
    destination=selected.get('resolved',{});rid=destination.get('release_id');gid=destination.get('release_group_id')
    if not ID.fullmatch(rid or '') or not ID.fullmatch(gid or ''):raise ValueError('Search again to verify this album.')
    release=get('release/'+rid,inc='recordings+artist-credits+release-groups')
    group=release.get('release-group',{})
    if release.get('id')!=rid or release.get('status')!='Official' or group.get('id')!=gid:raise ValueError('Album identity could not be verified.')
    if group.get('primary-type') not in ('Album','EP','Single') or (not explicit and set(group.get('secondary-types',[]))&{'Compilation','Live','Remix','DJ-mix'}):raise ValueError('This release is not the verified album.')
    items=[]
    for medium in release.get('media',[]):
        for track in medium.get('tracks',[]):
            record=track.get('recording',{});record_id=record.get('id');artists=credits(track) or credits(record) or credits(release)
            if not ID.fullmatch(record_id or '') or not artists or not ID.fullmatch(artists[0].get('id','')):continue
            title=record.get('title') or track.get('title')
            if not title:continue
            resolved={**destination,'recording_id':record_id,'title':title,'artist':artists[0]['name'],'artist_mbid':artists[0]['id'],'isrcs':record.get('isrcs',[]),'metadata_url':'https://musicbrainz.org/recording/'+record_id}
            items.append({'selection':str(uuid.uuid5(uuid.NAMESPACE_URL,'arrify-request:'+record_id+':'+gid)),'recording_id':record_id,'kind':'Song','title':title,'artist':artists[0]['name'],'album':destination['album'],'album_type':destination['album_type'],'year':destination.get('album_year',''),'requestable':True,'resolved':resolved,'disc':medium.get('position',1),'number':str(track.get('number') or track.get('position') or len(items)+1),'duration_ms':track.get('length') or record.get('length')})
    if not items or len(items)>300:raise ValueError('A complete, bounded album track list is not available.')
    expected=sum(int(m.get('track-count',len(m.get('tracks',[])))) for m in release.get('media',[]))
    if len(items)!=expected:raise ValueError('Some album tracks could not be verified. Please try another release.')
    return {'album':{'name':destination['album'],'artist':selected['artist'],'year':destination.get('album_year',''),'edition':release.get('title',''),'release_id':rid,'release_group_id':gid},'items':items,'next_offset':None}


def album_for_song(artist,title,get):
    """Resolve library artist spellings through verified MusicBrainz aliases."""
    if not artist.strip() or not title.strip() or max(len(artist),len(title))>500:raise ValueError('Invalid song identity.')
    results=get('artist/',query='artist:'+quote(artist)+' OR alias:'+quote(artist),limit=100).get('artists',[])
    matches=[a for a in results if ID.fullmatch(a.get('id','')) and any(norm(name)==norm(artist) for name in [a.get('name',''),*[v.get('name','') for v in a.get('aliases',[])]])]
    if not matches or len(matches)>30:raise ValueError('The album artist could not be verified unambiguously.')
    owners={a['id']:a for a in matches}
    found=get('recording/',query='('+' OR '.join('arid:'+aid for aid in owners)+') AND recording:'+quote(title)+' AND video:false',limit=100)
    if found.get('recording-count',len(found.get('recordings',[])))>100:raise ValueError('Too many recordings to verify this album.')
    linked={}
    for record in found.get('recordings',[]):
        if not ID.fullmatch(record.get('id','')) or norm(record.get('title',''))!=norm(title) or versions(record.get('title','')+' '+record.get('disambiguation',''))!=versions(title):continue
        for credit in credits(record):
            if credit.get('id') in owners:linked.setdefault(credit['id'],[]).append(record)
    if len(linked)!=1:raise ValueError('The album artist and song could not be verified unambiguously.')
    aid,records=next(iter(linked.items()));owner=owners[aid]
    records.sort(key=lambda r:(r.get('first-release-date','9999'),r['id']))
    choices=[]
    for record in records[:4]:
        result=recording_selection(record['id'],get)['items'][0]
        if result.get('requestable') and result['resolved']['artist_mbid']==owner['id']:choices.append(result)
    if not choices:raise ValueError('No original album could be verified for this song.')
    selected=min(choices,key=lambda x:(('Album','EP','Single').index(x['album_type']),x.get('year') or '9999'))
    result=album_tracks(selected,get);result['requested_artist']=artist
    return result


def artist_albums(artist,title,get,offset=0):
    """Browse release groups, verifying namesakes against a library recording."""
    if ID.fullmatch(artist) and not title:
        owner=get('artist/'+artist);aid=owner.get('id')
        if aid!=artist:raise ValueError('Artist identity changed.')
    else:
        found=get('artist/',query='artist:'+quote(artist)+' OR alias:'+quote(artist),limit=100).get('artists',[])
        owners={a['id']:a for a in found if ID.fullmatch(a.get('id','')) and any(norm(n)==norm(artist) for n in [a.get('name',''),*[v.get('name','') for v in a.get('aliases',[])]])}
        if not owners or len(owners)>30:raise ValueError('Artist identity could not be verified.')
        records=get('recording/',query='('+' OR '.join('arid:'+aid for aid in owners)+') AND recording:'+quote(title),limit=100)
        linked={a['id'] for r in records.get('recordings',[]) if norm(r.get('title',''))==norm(title) for a in credits(r) if a.get('id') in owners}
        if len(linked)!=1 or records.get('recording-count',0)>100:raise ValueError('Artist identity is ambiguous. Your library remains available below.')
        aid=next(iter(linked));owner=owners[aid]
    page=get('release-group/',artist=aid,type='album',limit=50,offset=offset)
    groups=page.get('release-groups',[])
    items=[{'group_id':g['id'],'title':g['title'],'year':g.get('first-release-date','')[:4],'types':g.get('secondary-types',[]),'requestable':False} for g in groups if ID.fullmatch(g.get('id','')) and g.get('title')]
    return {'artist':owner['name'],'items':items,'total':page.get('release-group-count',len(groups)), 'next_offset':offset+len(groups) if groups and offset+len(groups)<page.get('release-group-count',0) else None}

def album_for_group(gid,get):
    if not ID.fullmatch(gid or ''):raise ValueError('Invalid album identity.')
    group=get('release-group/'+gid,inc='artist-credits')
    artists=credits(group)
    if group.get('id')!=gid or not artists:raise ValueError('Album artist could not be verified.')
    releases=get('release/',**{'release-group':gid,'status':'official','inc':'release-groups+artist-credits','limit':100}).get('releases',[])
    # Explicitly browsed live/compilation albums retain their published identity.
    eligible=[r for r in releases if r.get('status')=='Official' and r.get('release-group',{}).get('id')==gid and ID.fullmatch(r.get('id',''))]
    if not eligible:raise ValueError('No official edition is available for this album.')
    release=min(eligible,key=lambda r:(any(w in norm(r.get('title','')) for w in ('deluxe','expanded','anniversary')),r.get('date') or '9999',r.get('country')!='US',r['id']))
    destination={'release_id':release['id'],'release_group_id':gid,'album':group['title'],'album_type':group.get('primary-type','Album'),'album_year':group.get('first-release-date','')[:4],'resolution_status':'resolved','release_date':release.get('date','')}
    result=album_tracks({'artist':artists[0]['name'],'resolved':destination},get,explicit=True)
    owner=get('artist/'+artists[0]['id'],inc='aliases')
    result['artist_aliases']=[a['name'] for a in owner.get('aliases',[]) if a.get('name')] if owner.get('id')==artists[0]['id'] else []
    return result


def explore_catalog(query,get,offset=0):
    words=re.findall(r"\w+(?:['’]\w+)*",query,flags=re.UNICODE)[:16]
    if not words:return {'items':[],'artists':[],'next_offset':None}
    terms=' AND '.join('(releasegroup:'+quote(w)+' OR alias:'+quote(w)+' OR artist:'+quote(w)+')' for w in words)
    names=[query]+[' '.join(words[:n]) for n in range(1,min(4,len(words)))]+[' '.join(words[-n:]) for n in range(1,min(4,len(words)))]
    candidates=get('artist/',query=' OR '.join('(artist:'+quote(n)+' OR alias:'+quote(n)+')' for n in dict.fromkeys(names)),limit=100).get('artists',[])
    alternatives=[];artists=[]
    for a in candidates:
        if not ID.fullmatch(a.get('id','')):continue
        aliases=[a.get('name',''),*[v.get('name','') for v in a.get('aliases',[])]]
        if any(norm(n)==norm(query) for n in aliases):artists.append(a)
        for name in names:
            if not any(norm(n)==norm(name) for n in aliases):continue
            whole=norm(query);part=norm(name)
            remainder=whole[len(part):].strip() if whole.startswith(part+' ') else whole[:-len(part)].strip() if whole.endswith(' '+part) else ''
            if remainder:alternatives.append('(arid:'+a['id']+' AND (releasegroup:'+quote(remainder)+' OR alias:'+quote(remainder)+'))^8')
    album_query='releasegroup:'+quote(query)+'^6 OR alias:'+quote(query)+'^6 OR ('+terms+')'+(' OR '+' OR '.join(dict.fromkeys(alternatives)) if alternatives else '')
    albums=get('release-group/',query='('+album_query+') AND primarytype:album',limit=20,offset=offset)
    if offset:artists=[]
    rows=albums.get('release-groups',[]);page_length=len(rows)
    if offset==0 and not alternatives:
        alias_rows=get('release-group/',query='alias:'+quote(query)+' AND primarytype:album',limit=5).get('release-groups',[])
        rows=list({g['id']:g for g in alias_rows+rows if g.get('id')}.values())
    return {'items':[{'group_id':g['id'],'title':g['title'],'artist':', '.join(a['name'] for a in credits(g)),'year':g.get('first-release-date','')[:4],'types':g.get('secondary-types',[]),'requestable':False} for g in rows if ID.fullmatch(g.get('id','')) and g.get('title')], 'artists':[{'id':a['id'],'name':a['name'],'description':a.get('disambiguation','')} for a in artists if ID.fullmatch(a.get('id',''))], 'next_offset':offset+page_length if page_length and offset+page_length<albums.get('count',0) else None}
