"""Review name variants; apply only an explicit, reviewed alias manifest.

Input: exported effective tracks with artist_names, artist, title and album.
Candidate similarity is NOT an identity decision (e.g. the two Kingsmen).
No audio files or track IDs are modified.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


def name_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').split()).casefold()


def candidate_key(value):
    value=re.sub(r'^(.+),\s*(the|a|an)$',r'\2 \1',name_key(value))
    value=''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c))
    return re.sub(r'[^\w]','',value.replace('&',' and '))


def aliases(manifest):
    result={}
    for group in manifest['corrections']:
        target=group['canonical']
        if not group.get('evidence'):raise ValueError('Reviewed evidence required')
        for name in group['aliases']+[target]:
            key=name_key(name)
            if key in result and result[key]!=target:raise ValueError('Ambiguous alias: '+name)
            result[key]=target
    return result


def merge_names(existing,manifest):
    reviewed=aliases(manifest)
    # Resolve old canonical targets too, including conflicting case-only keys.
    result={k:reviewed.get(name_key(k),reviewed.get(name_key(v),v)) for k,v in existing.items()}
    for group in manifest['corrections']:
        for name in group['aliases']+[group['canonical']]:result[name]=group['canonical']
    checked={}
    for k,v in result.items():
        key=name_key(k)
        if key in checked and checked[key]!=v:raise ValueError('Conflicting normalized aliases: '+k)
        checked[key]=v
    return result


def audit(tracks,manifest):
    artists=collections.defaultdict(set)
    for track in tracks:
        for name in track.get('artist_names',[track['artist']]):artists[name].add(track['id'])
    candidates=collections.defaultdict(list)
    for name in artists:candidates[candidate_key(name)].append(name)
    reviewed=aliases(manifest);after=collections.defaultdict(set);affected=set();changed=[]
    for name,ids in artists.items():
        target=reviewed.get(name_key(name),name);after[target].update(ids)
        if target!=name:affected.update(ids);changed.append({'from':name,'to':target,'tracks':len(ids)})
    return {'tracks_audited':len(tracks),'artist_groups_before':len(artists),'artist_groups_projected':len(after),'affected_tracks':len(affected),'changes':changed,'similarity_candidates':[v for v in candidates.values() if len(v)>1],'kept_separate':manifest.get('kept_separate',[]),'review_remaining':manifest.get('review_remaining',[])}


def apply(path,manifest,backup):
    raw=path.read_bytes();data=json.loads(raw);data['names']=merge_names(data.get('names',{}),manifest)
    with backup.open('xb') as f:f.write(raw)
    os.chmod(backup,0o600)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.chmod(tmp,path.stat().st_mode & 0o777)
        if path.read_bytes()!=raw:raise RuntimeError('Cleanup changed concurrently; no changes applied')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--tracks',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--apply',type=Path);parser.add_argument('--backup',type=Path)
    args=parser.parse_args();manifest=json.loads(args.manifest.read_text());report=audit(json.loads(args.tracks.read_text()),manifest)
    if args.apply:
        if not args.backup:parser.error('--apply requires --backup')
        apply(args.apply,manifest,args.backup)
    print(json.dumps(report,ensure_ascii=False,indent=2))
