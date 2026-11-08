"""Generate six original instrumental demo recordings (no samples or external media).
Usage: python generate.py OUTPUT_DIRECTORY; convert WAVs to tagged FLAC with ffmpeg.
"""
import json,math,struct,sys,wave
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(sys.argv[1]);root.mkdir(parents=True,exist_ok=True)
titles=['First Light','Open Windows','Bright Steps','Quiet Water','Evening Drift','Last Lantern']
manifest=[]
for n,title in enumerate(titles):
 album='Daylight' if n<3 else 'Afterglow';artist='Arrify Studio';folder=root/artist/album;folder.mkdir(parents=True,exist_ok=True)
 if not (folder/'cover.png').exists():
  im=Image.new('RGB',(1000,1000),'#102b28' if n<3 else '#28213c');d=ImageDraw.Draw(im)
  for k in range(12):
   x=80+k*72;h=100+int(240*(1+math.sin(k*.6+n)))
   d.rounded_rectangle((x,500-h//2,x+38,500+h//2),radius=19,fill='#65e6b0' if n<3 else '#bdacf1')
  im.save(folder/'cover.png')
 rate=22050;duration=45+n*3;tempo=[.4,.45,.3,.75,.65,.8][n];transpose=[0,2,5,-3,-5,-2][n]
 chords=((48,52,55,59),(45,48,52,55),(41,45,48,52),(43,47,50,55))
 with wave.open(str(folder/(title+'.wav')),'wb') as out:
  out.setnchannels(2);out.setsampwidth(2);out.setframerate(rate)
  for second in range(duration):
   buf=bytearray()
   for i in range(rate):
    t=second+i/rate;chord=chords[int(t//(tempo*8))%4];beat=t%tempo
    freq=lambda note:440*2**((note+transpose-69)/12)
    f=freq(chord[int(t//tempo)%4]+12)
    pluck=(1-math.exp(-beat*100))*math.exp(-beat*(5 if n<3 else 3))*math.sin(2*math.pi*f*t)
    phase=t%(tempo*8);env=min(1,phase/.2,(tempo*8-phase)/.3)
    pad=sum(math.sin(2*math.pi*freq(note)*t) for note in chord)*.035*env
    sample=int((pluck*.14+pad)*min(1,t/2,(duration-t)/3)*32767)
    buf.extend(struct.pack('<hh',sample,sample))
   out.writeframes(buf)
 manifest.append(dict(title=title,artist=artist,album=album,track=n%3+1,path=str((folder/(title+'.wav')).relative_to(root))))
(root/'manifest.json').write_text(json.dumps(manifest,indent=2))
tracks=[dict(title=x['title'],artist=x['artist'],album=x['album']) for x in manifest]
collections=[]
for slug,name,section,selected in [('arrify-demo','Arrify Originals','playlists',tracks),('mood-feel-good','Daylight Demo','moods',tracks[:3]),('mood-unwind','Afterglow Demo','moods',tracks[3:]),('demo-daylight','Daylight — Demo Channel','channels',tracks[:3]),('demo-afterglow','Afterglow — Demo Channel','channels',tracks[3:])]:
 collections.append(dict(slug=slug,name=name,section=section,description='Original Arrify instrumentals for demonstration and playback testing.',tracks=selected))
(root/'playlists.json').write_text(json.dumps(collections,indent=2))
print('Generated six original demo recordings and five collections.')
