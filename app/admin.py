"""Request views scoped by the HTTP handler; acquisition remains in its companion."""
import json
from . import requests as acquisition

def request_rows(accounts,user_id=None,offset=0,limit=50,library=None):
    where=' WHERE r.user_id=?' if user_id is not None else ''
    params=[user_id] if user_id is not None else []
    with accounts.lock:
        total=accounts.db.execute('SELECT count(*) FROM music_requests r'+where,params).fetchone()[0]
        rows=accounts.db.execute('''SELECT r.*,CASE WHEN u.deleted_at>0 THEN 'Deleted account' ELSE u.username END username
            FROM music_requests r LEFT JOIN users u ON u.id=r.user_id'''+where+' ORDER BY r.created DESC,r.user_id DESC,r.request_id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
    ids=list(dict.fromkeys(row['request_id'] for row in rows));states={};unavailable=False
    if ids:
        try:states={x['id']:x for x in acquisition.call('/statuses?ids='+','.join(ids))['items']}
        except ValueError:unavailable=True
    items=[]
    for row in rows:
        try:snapshot=json.loads(row['metadata'])
        except (ValueError,TypeError):snapshot={}
        if not isinstance(snapshot,dict):snapshot={}
        status=states.get(row['request_id'],{})
        data={k:status.get(k,snapshot.get(k,'')) for k in ('title','artist','album','detail')}
        data.update(id=row['request_id'],state=status.get('state','status-unavailable'),created=row['created'])
        data.update({k:status[k] for k in ('phase','progress_percent','updated','history') if k in status})
        from .request_progress import STAGES
        data['detail']=STAGES.get(data['state'],('In progress','We’re working on this request.',1))[1]
        if user_id is None:data.update(user_id=row['user_id'],username=row['username'] or 'Deleted account')
        items.append(data)
    return {'items':items,'total':total,'next_offset':offset+len(rows),'status_unavailable':unavailable}
