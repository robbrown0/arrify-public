"""Stable channel presentation, independent of airplay refresh/file ordering."""

CHANNELS = (
    ('sirius-80son8', '80s-on-8'),
    ('sirius-thebridge', 'the-bridge'),
    ('sirius-theblend', 'the-blend'),
    ('sirius-1stwave', '1st-wave'),
    ('sirius-thepulse', 'the-pulse'),
    ('sirius-classicrewind', 'classic-rewind'),
    ('the-spectrum', 'the-spectrum'),
)


def present_channels(collections):
    """Keep other collection slots intact; order channels and attach local logos."""
    order = {slug: position for position, (slug, _) in enumerate(CHANNELS)}
    logos = {slug: '/assets/channels/' + asset + '.svg' for slug, asset in CHANNELS}
    channels = []
    for item in collections:
        if item.get('section') != 'channels':
            continue
        item = dict(item)
        if item['slug'] in logos:
            item['channel_logo'] = True
            item['artwork'] = logos[item['slug']]
            item['artworks'] = [item['artwork']]
        channels.append(item)
    channels.sort(key=lambda item: order.get(item['slug'], len(order)))
    channels = iter(channels)
    return [next(channels) if item.get('section') == 'channels' else item
            for item in collections]


class PersonalStations:
    """Per-account layout; shared recordings and acquisition policy are unchanged."""
    def __init__(self, accounts):
        self.accounts = accounts

    def get(self, uid, collections):
        with self.accounts.lock:
            row = self.accounts.db.execute('SELECT items,revision FROM station_preferences WHERE user_id=?', (uid,)).fetchone()
        if row:
            import json
            items = json.loads(row['items']);revision = row['revision']
        else:
            by_slug = {x['slug']: x for x in collections if x.get('section') == 'channels'}
            items = [{'slug': slug, 'name': by_slug[slug]['name'], 'hidden': False}
                     for slug, _ in CHANNELS if slug in by_slug]
            revision = 0
        return {'items': items, 'revision': revision}

    def collections(self, uid, collections):
        prefs = self.get(uid, collections)
        available = {x['slug']: x for x in collections if x.get('section') == 'channels'}
        channels = []
        for item in prefs['items']:
            if item.get('hidden'):continue
            channels.append(dict(available.get(item['slug'], {'slug':item['slug'],'name':item['name'],
                'section':'channels','tracks':0,'artwork':None,'artworks':[],'preparing':True})))
            from pathlib import Path
            logo=Path('/spectrum-channel/artwork')/(item['slug']+'.webp')
            if logo.is_file():
                channels[-1].update(channel_logo=True,artwork='/api/v1/stations/artwork/'+item['slug'],artworks=['/api/v1/stations/artwork/'+item['slug']])
        others = [x for x in collections if x.get('section') != 'channels']
        position = next((i for i,x in enumerate(collections) if x.get('section')=='channels'),len(others))
        return others[:position] + channels + others[position:]

    def update(self, uid, data, collections, request_station):
        import json
        action=data.get('action');slug=data.get('slug');expected=data.get('revision')
        if action not in ('add','hide','move','remove') or not isinstance(slug,str) or len(slug)>100:
            raise ValueError('Choose a station action.')
        if type(expected) is not int or expected<0:raise ValueError('Refresh your stations before editing.')
        # Validate against the current personal list before making a helper request.
        snapshot=self.get(uid,collections)
        if expected!=snapshot['revision']:raise ValueError('Your stations changed on another device. Refresh and try again.')
        station=None
        if action=='add':
            if len(snapshot['items'])>=60 and not any(x['slug']==slug for x in snapshot['items']):raise ValueError('Your list can hold up to 60 stations.')
            channel='thespectrum' if slug=='the-spectrum' else slug.removeprefix('sirius-')
            response=request_station(channel);station=response['station']
            if station['slug']!=slug:raise ValueError('Choose a station from the directory.')
        with self.accounts.transaction():
            current=self.get(uid,collections)
            if current['revision']!=expected:raise ValueError('Your stations changed on another device. Refresh and try again.')
            items=current['items'];index=next((i for i,x in enumerate(items) if x['slug']==slug),None)
            if action=='add':
                if index is None:items.append({'slug':slug,'name':station['name'],'hidden':False})
                else:items[index]['hidden']=False
            else:
                if index is None:raise ValueError('This station is not in your list.')
                if action=='hide':
                    if type(data.get('hidden')) is not bool:raise ValueError('Choose whether to show this station.')
                    items[index]['hidden']=data['hidden']
                elif action=='remove':items.pop(index)
                else:
                    direction=data.get('direction')
                    target=data.get('target')
                    if target is None:
                        if type(direction) is not int or direction not in (-1,1):raise ValueError('Choose Move up or Move down.')
                        target=index+direction
                    if type(target) is not int:raise ValueError('Choose a station position.')
                    if not 0<=target<len(items):raise ValueError('This station is already at the end of the list.')
                    items.insert(target,items.pop(index))
            revision=current['revision']+1
            self.accounts.db.execute('INSERT INTO station_preferences(user_id,items,revision) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET items=excluded.items,revision=excluded.revision',(uid,json.dumps(items),revision))
        return {'items':items,'revision':revision}
