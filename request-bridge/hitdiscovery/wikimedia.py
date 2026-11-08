"""Licensed secondary year-end transcriptions through the official MediaWiki API.

This is not a Billboard API. Provenance and revision IDs accompany every fact.
Derived exports containing these transcriptions are distributed under CC BY-SA 4.0.
"""
import html
import re
from datetime import date
from urllib.parse import urlencode

LICENSE='https://creativecommons.org/licenses/by-sa/4.0/'

def clean(cell):
    cell=re.sub(r"\{\{'\s*\}\}","'",cell)
    cell=re.sub(r'\{\{efn\|[^{}]*\}\}','',cell,flags=re.S)
    cell=re.sub(r'<ref\b[^>]*(?:/>|>.*?</ref>)','',cell,flags=re.S|re.I)
    cell=re.sub(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]',r'\1',cell)
    cell=re.sub(r'\{\{(?:nowrap|nobr)\|([^{}]*)\}\}',r'\1',cell,flags=re.I)
    cell=re.sub(r'<[^>]+>','',cell)
    cell=html.unescape(cell).replace("'''",'').replace("''",'').strip()
    if any(x in cell for x in ('{{','[[','}}',']]')): raise ValueError('Unsupported wiki markup: '+cell[:120])
    return cell.strip('"“” ')

def parse_year(wikitext,year,url,revision):
    wikitext=re.sub(r'<!--.*?-->','',wikitext,flags=re.S)
    wikitext=re.sub(r'\|\s*scope="row"\s*\|\s*(\d+)',r'|\1',wikitext)
    tables=re.findall(r'\{\|.*?\|\}',wikitext,re.S)
    for table in tables:
        rows=[]; inherited_artist=None; artist_rows=0
        for chunk in re.split(r'\n\|-.*?\n',table[:-2]):
            chunk=chunk.strip()
            if not re.match(r'^\|\s*\d+\s*(?:\||\n)',chunk): continue
            cells=re.split(r'\s*\|\|\s*|\n\|',chunk.lstrip('|').strip())
            if len(cells)==2 and artist_rows>0:
                cells.append(inherited_artist); artist_rows-=1
            if len(cells)!=3: raise ValueError(f'Unexpected chart row in {year}')
            span=re.match(r'rowspan="?(\d+)"?\s*\|\s*(.*)',cells[2],re.S)
            if span:
                artist_rows=int(span[1])-1; cells[2]=span[2]; inherited_artist=cells[2]
            rank=int(cells[0].strip())
            rows.append({'artist':clean(cells[2]),'title':clean(cells[1]),'release_date_hint':str(year),
                         'genres':[], 'evidence':[{'source':'Billboard US year-end Hot 100 (Wikipedia transcription)',
                         'rank':rank,'kind':'year-end','date':f'{year}-12-31','chart_year':year,
                         'url':url,'revision_id':revision,'license':LICENSE,'verification':'secondary-transcription',
                         'primary_source_url':f'https://www.billboard.com/charts/year-end/{year}/hot-100-songs/',
                         'independent_family':'Billboard'}]})
        if len(rows)==100 and {r['evidence'][0]['rank'] for r in rows}==set(range(1,101)):
            return rows
    raise ValueError(f'No complete validated 100-row chart for {year}')

def fetch(http,contact):
    rows=[]; health=[]
    for year in range(2015,date.today().year):
        page=f'Billboard_Year-End_Hot_100_singles_of_{year}'
        url='https://en.wikipedia.org/w/api.php?'+urlencode({'action':'parse','page':page,'prop':'wikitext|revid','format':'json','maxlag':5})
        try:
            d=http.json(url,headers={'User-Agent':f'HitDiscovery/0.1 ({contact})'},interval=1.1,cache_ttl=86400*30)
            p=d['parse']; rev=p['revid']; article=f'https://en.wikipedia.org/w/index.php?title={page}&oldid={rev}'
            found=parse_year(p['wikitext']['*'],year,article,rev)
            rows.extend(found); health.append({'year':year,'ok':True,'rows':len(found),'revision':rev})
        except Exception as e:
            health.append({'year':year,'ok':False,'error':str(e)})
    return rows,health
