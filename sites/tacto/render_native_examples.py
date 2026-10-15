#!/usr/bin/env python3
"""Render signed-helper native window captures; never send input to any app.

Input is a private directory containing textedit-*/finder-* recording reports.
ffmpeg crops Finder's sidebar/path to exclude personal account/device names.
Pointer marks are explanatory annotations, not measured system cursor motion.
"""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
from annotate_recordings import render_svg,svg_frame

def call(*args):subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)],check=True)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--recordings',type=Path,required=True);p.add_argument('--assets',type=Path,default=Path(__file__).parent/'dist/assets/demos');a=p.parse_args();a.assets.mkdir(parents=True,exist_ok=True);summary=[]
 for name in ['textedit','finder']:
  candidates=sorted(p for p in a.recordings.glob(name+'-*') if (p/'report.json').exists());source=candidates[-1];report=json.loads((source/'report.json').read_text());frames=report['frames'];start=frames[0]['at'];duration=round(report['seconds']-start+2,3)
  is_finder=name=='finder';width,height=(1520,870) if is_finder else (1312,844)
  markers=[{'time':0,'x':140 if is_finder else 35,'y':130 if is_finder else 100,'kind':'idle','label':'FINDER · FILE LIST' if is_finder else 'TEXTEDIT · NOTE'}]
  for action in report['actions']:
   kind=action['action']['name']
   if is_finder:
    number=len(markers);x=1290 if number==1 else 145;y=130;label='SORT BY KIND' if number==1 else 'SORT BY NAME' if number==2 else 'CHECK 5 FILES';box=[0,100,1520,300]
   else:x,y=35,180;label={'prepare_window':'READY','set_value':'WRITE NOTE','menu':'CHECK SAVED STATE'}[kind];box=[10,66,1240,320]
   markers.append({'time':max(0,round(action['at']-start,3)),'x':x,'y':y,'kind':'click' if kind=='click' else 'check','label':label,'box':box})
  markers.sort(key=lambda x:x['time']);spec={'markers':markers,'poster_marker':markers[-1],'width':width,'height':height}
  with tempfile.TemporaryDirectory(prefix='tacto-native-render-') as tmp:
   work=Path(tmp);raw=[];guided=[]
   for i,row in enumerate(frames):
    crop=work/f'raw-{i}.png';call('-i',source/row['file'],'-vf','crop=1520:870:320:0' if is_finder else 'null','-frames:v',1,crop)
    overlay=work/f'overlay-{i}.png';render_svg(svg_frame(spec,max(0,row['at']-start)),overlay)
    rendered=work/f'guide-{i}.png';call('-i',crop,'-i',overlay,'-filter_complex','[0:v][1:v]overlay=0:0:format=auto','-frames:v',1,rendered)
    hold=(frames[i+1]['at']-row['at']) if i+1<len(frames) else 2
    for seq,img in [(raw,crop),(guided,rendered)]:seq.extend([f"file '{img}'",f'duration {hold:.6f}'])
   for label,seq,last in [('raw',raw,crop),('guided',guided,rendered)]:
    seq.append(f"file '{last}'");manifest=work/f'{label}.txt';manifest.write_text('\n'.join(seq)+'\n');movie=a.assets/f'native-{name}{"-raw" if label=="raw" else ""}.mp4'
    call('-f','concat','-safe',0,'-i',manifest,'-vf','fps=24','-c:v','libx264','-crf','24','-pix_fmt','yuv420p','-movflags','+faststart','-an',movie)
   (a.assets/f'native-{name}-original.png').write_bytes(crop.read_bytes());(a.assets/f'native-{name}-poster.png').write_bytes(rendered.read_bytes())
  movie=a.assets/f'native-{name}.mp4';summary.append({'name':name,'target':report['target'],'final':report['final'],'seconds':duration,'capture_frames':len(frames),'width':width,'height':height,'video':{'file':movie.name,'sha256':digest(movie),'bytes':movie.stat().st_size},'screenshots':[{'file':f'native-{name}-{suffix}.png','sha256':digest(a.assets/f'native-{name}-{suffix}.png')} for suffix in ['original','poster']],'annotation':'Explanatory pointer over timestamped real window captures. Last frame held 2 seconds. Not a runtime benchmark.','privacy_crop':'Sidebar and path bar removed' if is_finder else 'Only the owned TextEdit document window'})
  captions='파일 목록을 종류별로 정렬한 뒤 이름순으로 정렬합니다. 다섯 파일과 정렬 상태를 확인했습니다.' if is_finder else 'TextEdit에 워크숍 메모를 입력합니다. 입력 단계의 저장 증거는 미확정이었으며, 별도 Save 후 문서와 디스크 내용 일치를 확인했습니다.'
  end=f'00:00:{duration:06.3f}';(a.assets/f'native-{name}.ko.vtt').write_text(f'WEBVTT\n\n00:00:00.000 --> {end}\n{captions}\n')
 (a.assets/'native-everyday-examples.json').write_text(json.dumps({'recorded_on':'2026-10-15','scenarios':summary,'limits':['TextEdit edit replay stopped commit_unverified; a separate built-in Save replay passed saved-state verification. No automatic completion or causal writer claim.','Finder only sorts/displays the owned folder; it does not move/delete files.','Preview PDF experiment excluded: window preparation refused no blank native titlebar point; page transition was not verified.']},ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
