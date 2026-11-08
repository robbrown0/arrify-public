"""Small review-only copy corrections; does not touch playback behavior."""
from pathlib import Path
import sys
root=Path(sys.argv[1])
p=root/'app.js';s=p.read_text()
s=s.replace("item.tracks+' songs'","item.tracks+(item.tracks===1?' song':' songs')")
s=s.replace("'Metadata snapshot'","'No songs yet'")
s=s.replace("(x.tracks||0)+' songs'","(x.tracks||0)+(x.tracks===1?' song':' songs')")
s=s.replace("${count.toLocaleString()} songs", "${count.toLocaleString()} ${count===1?'song':'songs'}")
s=s.replace('Tracks started counts playback starts, not completed listens. Sign-in and last-seen timestamps begin with this release.','“Tracks started” counts playback starts, not completed listens. Sign-in times appear after a user signs in.')
p.write_text(s)
for filename in ('party-host.js','party-guest.js'):
 p=root/filename;s=p.read_text()
 s=s.replace("p.items.length+' songs'","p.items.length+(p.items.length===1?' song':' songs')")
 s=s.replace("rows.length+' songs</span>","rows.length+(rows.length===1?' song':' songs')+'</span>")
 s=s.replace("p.track_ids.length+' songs · '","p.track_ids.length+(p.track_ids.length===1?' song':' songs')+' · '")
 s=s.replace("p.items.length+' songs</small>","p.items.length+(p.items.length===1?' song':' songs')+'</small>")
 s=s.replace("total+' songs</span>","total+(total===1?' song':' songs')+'</span>")
 p.write_text(s)
p=root/'index.html';s=p.read_text().replace('Shared · everyone','Shared · listeners').replace('personal playlists, and listening history?','personal playlists, PartyQueues, and listening history?');p.write_text(s)
