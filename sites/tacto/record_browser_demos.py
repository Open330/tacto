#!/usr/bin/env python3
"""Record real isolated-browser examples through Tacto's BrowserDriver and Flow.

Development tool: project Python + Playwright Chromium + ffmpeg. The host opens
public pages and records a viewport; all task inputs/clicks use Tacto replay.
The visible pointer is an aria-hidden recording annotation, not an OS cursor.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nlc.browser import BrowserDriver
from nlc.flow import Flow, replay

GUIDE = r'''(() => {
 function install() {
  if(document.getElementById('tacto-recording-guide'))return;
  const make=(tag,style,parent)=>{const e=document.createElement(tag);e.style.cssText=style;parent?.append(e);return e;};
  const root=make('div','position:fixed;inset:0;pointer-events:none;z-index:2147483647;font:14px Arial,sans-serif;color:white',document.documentElement);root.id='tacto-recording-guide';root.setAttribute('aria-hidden','true');
  const badge=make('div','position:absolute;right:12px;top:12px;background:#153864;border-radius:7px;padding:9px 13px',root);badge.textContent='TACTO · GUIDE POINTER';
  const target=make('div','position:absolute;border:3px solid #287de9;background:rgba(40,125,233,.07);border-radius:7px;opacity:0',root);
  const pointer=make('div','position:absolute;left:0;top:0;transform:translate(25px,70px);transition:transform .35s ease',root);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width','34');svg.setAttribute('height','42');svg.setAttribute('viewBox','0 0 34 42');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');for(const [k,v] of Object.entries({d:'M2 2V31L10 23L16 38L23 35L17 21H30Z',fill:'white',stroke:'#142133','stroke-width':'2.4','stroke-linejoin':'round'}))path.setAttribute(k,v);svg.append(path);pointer.append(svg);
  const label=make('span','position:absolute;left:30px;top:8px;background:#153864;border-radius:6px;padding:8px 11px;white-space:nowrap',pointer);label.textContent='READY';
  window.__tactoShow=({x,y,width:w,height:h,label:text})=>{pointer.style.transform=`translate(${Math.max(5,Math.min(innerWidth-190,x+w/2))}px,${Math.max(55,Math.min(innerHeight-70,y+h/2))}px)`;label.textContent=text;Object.assign(target.style,{left:x+'px',top:y+'px',width:w+'px',height:h+'px',opacity:'1'});};
  const pulse=make('div','position:absolute;width:42px;height:42px;border:3px solid #287de9;border-radius:50%;background:rgba(40,125,233,.12);opacity:0',root);
  document.addEventListener('pointerdown',event=>{if(!root.isConnected)return;pulse.style.left=(event.clientX-21)+'px';pulse.style.top=(event.clientY-21)+'px';pulse.animate([{opacity:.9,transform:'scale(.45)'},{opacity:0,transform:'scale(1.8)'}],{duration:700,easing:'ease-out'});},true);
  window.__tactoHide=()=>root.style.visibility='hidden';window.__tactoUnhide=()=>root.style.visibility='visible';
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
'''


class RecordingDriver:
    def __init__(self, inner, started):
        self.inner, self.started, self.actions = inner, started, []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def act(self, obs_id, target, action, **kwargs):
        page=self.inner._page
        page.evaluate(GUIDE)
        node=None;box=None
        current=self.inner._latest.get(target)
        index=action.get('element_index')
        if current and current['obs_id']==obs_id and isinstance(index,int):
            node=current['nodes'][index];handle=self.inner._locator(node)
            try: box=handle.bounding_box()
            finally: handle.dispose()
        if box:
            verb={'set_value':'INPUT','type_text':'INPUT','click':'CLICK','select_option':'SELECT','read':'CHECK'}.get(action['name'],'ACTION')
            label=verb+'  '+str(node.get('name',''))[:38]
            page.evaluate('(data)=>window.__tactoShow?.(data)',dict(box,label=label))
            page.wait_for_timeout(450)
        before=time.monotonic()
        result=self.inner.act(obs_id,target,action,**kwargs)
        self.actions.append({'at':round(before-self.started,3),'action':action['name'],
            'target':node.get('name') if node else None,'bounds':box,'outcome':result.get('type')})
        page.wait_for_timeout(650)
        return result


def scenario(name):
    def field(title,role='textbox'):return {'role':role,'title':title}
    def value(title,text):return {'field':field(title),'attribute':'Value','equals':text}
    def fill(title,text):return {'action':'set_value','selector':field(title),'params':{'value':text},'postcondition':value(title,text)}
    if name=='wikipedia':
        steps=[{'action':'set_value','selector':field('Search Wikipedia','searchbox'),'params':{'value':'Seoul'},'postcondition':{'field':{'title':'Search Wikipedia'},'attribute':'Value','equals':'Seoul'}},
            {'action':'click','selector':field('Search','button'),'postcondition':{'field':field('Seoul','heading'),'attribute':'Title','equals':'Seoul'}},
            {'action':'click','selector':field('History','link'),'postcondition':{'field':field('History','heading'),'attribute':'Title','equals':'History'}},
            {'action':'read','selector':field('History','heading'),'postcondition':{'field':field('History','heading'),'attribute':'Title','equals':'History'},'safety':{'effects':['read']},'answers':True}]
        return 'https://en.wikipedia.org/wiki/Main_Page',steps
    if name=='shopping':
        title='Full Moon over Noah’s Ark: An Odyssey to Mount Ararat and Beyond'
        steps=[{'action':'click','selector':field('Travel','link'),'postcondition':{'field':field('Travel','heading'),'attribute':'Title','equals':'Travel'}},
            {'action':'click','selector':field(title,'link'),'postcondition':{'field':field(title,'heading'),'attribute':'Title','equals':title}},
            {'action':'read','selector':field(title,'heading'),'postcondition':{'contains':'£49.43'},'safety':{'effects':['read']},'answers':True}]
        return 'https://books.toscrape.com/',steps
    steps=[fill('Text input','Tacto Demo'),fill('Date picker','10/20/2026'),fill('Textarea','Please confirm the weekend workshop details.'),
        {'action':'select_option','selector':field('Dropdown (select)','combobox'),'params':{'option_label':'Two','value':'2'},'postcondition':{'field':field('Dropdown (select)','combobox'),'attribute':'Value','equals':'Two'}},
        {'action':'read','selector':field('Text input'),'postcondition':value('Text input','Tacto Demo'),'safety':{'effects':['read']},'answers':True}]
    return 'https://www.selenium.dev/selenium/web/web-form.html',steps


def recording_flow(name, steps):
    """Emit explicit schema-2 contracts for published and development runtimes.

    Older schema-2 parsers silently ignore top-level authoring defaults. Expand
    them here so a portable recording never starts with missing action meaning.
    Final URL/DOM checks remain in the independent verifier.
    """
    from copy import deepcopy
    expanded = deepcopy(steps)
    defaults = {'effects':['write_ephemeral'], 'reversibility':'reversible',
                'resource':{'object_type':'owned disposable browser context','scope':'entry'}}
    for step in expanded:
        local = step.get('safety', {})
        contract = {**deepcopy(defaults), **local}
        for key in ('resource', 'destination'):
            if key in defaults or key in local:
                contract[key] = {**defaults.get(key, {}), **local.get(key, {})}
        step['safety'] = contract
    return Flow.from_json(json.dumps({'schema':2,'name':'browser-demo-'+name,
                                     'target':'page','steps':expanded}))


def run(name, output, assets, *, definition=None, verify=None, approve=False, inspect_unverified_transmit=False):
    directory=output/name;directory.mkdir(parents=True,mode=0o700,exist_ok=False)
    inner=BrowserDriver(headless=True)
    context=None;video=None;source_video=None;requests=[]
    try:
        context=inner._browser.new_context(viewport={'width':1280,'height':800},
            record_video_dir=str(directory),record_video_size={'width':1280,'height':800})
        context.add_init_script(GUIDE)
        old=inner._page.context;inner._page=context.new_page();inner._cdp=context.new_cdp_session(inner._page)
        inner._cdp.send('Accessibility.enable');old.close()
        page=inner._page;page.set_default_navigation_timeout(30000)
        page.on('request',lambda request:requests.append({'method':request.method,'url':request.url}))
        video=page.video;source_video=Path(video.path());started=time.monotonic()
        url,steps=definition if definition is not None else scenario(name);inner.goto(url);observed=inner.observe()
        assert observed['type']=='observation'
        (directory/'initial-observation.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2))
        flow=recording_flow(name,steps);driver=RecordingDriver(inner,started)
        (directory/'flow.json').write_text(flow.to_json())
        page.wait_for_timeout(900)
        approvals={}
        if approve:
            from nlc.agent import Agent
            approvals,blocked=Agent(driver=driver).authorize(flow,ask=lambda message:True)
            assert not blocked,blocked
        result=replay(driver,flow,approvals=approvals)
        (directory/'flow-result.json').write_text(json.dumps({'ok':result.ok,'log':result.log,'actions':driver.actions},ensure_ascii=False,indent=2))
        if not result.ok:
            last=result.log[-1]
            assert inspect_unverified_transmit and name=='translate' and last.get('error')=='commit_unverified' and last.get('status')=='unverified',result.log
        if verify is not None:
            verified,box=verify(page,driver)
        elif name=='wikipedia':
            assert page.url=='https://en.wikipedia.org/wiki/Seoul#History'
            assert page.locator('#History').is_visible() and page.evaluate('scrollY')>100
            verified={'article':'Seoul','section':'History','url':page.url}
            box=page.get_by_role('heading',name='History',exact=True).bounding_box()
        elif name=='shopping':
            title=page.locator('.product_main h1').inner_text();price=page.locator('.product_main .price_color').inner_text();stock=page.locator('.product_main .availability').inner_text().strip()
            assert title=='Full Moon over Noah’s Ark: An Odyssey to Mount Ararat and Beyond' and price=='£49.43' and stock=='In stock (15 available)'
            verified={'title':title,'price':price,'stock':stock,'url':page.url,'site_kind':'public shopping sandbox, not real prices or purchase'}
            box=page.locator('.product_main .price_color').bounding_box()
        else:
            actual={name:page.get_by_label(title,exact=True).input_value() for name,title in [('text','Text input'),('date','Date picker'),('message','Textarea')]}
            actual['choice']=page.get_by_role('combobox',name='Dropdown (select)',exact=True).input_value()
            assert actual=={'text':'Tacto Demo','date':'10/20/2026','message':'Please confirm the weekend workshop details.','choice':'2'}
            assert page.url=='https://www.selenium.dev/selenium/web/web-form.html'
            assert not any(row['action']=='click' and row['target']=='Submit' for row in driver.actions)
            verified={**actual,'submitted':False,'url':page.url,'site_kind':'Selenium public test form; draft only'}
            box=page.get_by_label('Text input',exact=True).bounding_box()
        page.evaluate(GUIDE)
        if box:page.evaluate('(d)=>window.__tactoShow?.(d)',dict(box,label='CHECK  verified result'))
        page.wait_for_timeout(1000)
        page.screenshot(path=str(assets/f'browser-{name}-poster.png'))
        page.evaluate('()=>window.__tactoHide?.()');page.screenshot(path=str(assets/f'browser-{name}-original.png'));page.evaluate('()=>window.__tactoUnhide?.()')
        page.wait_for_timeout(1300)
        report={'name':name,'recorded_on':'2026-10-15','entry_url':url,'final':verified,'flow_steps':len(steps),'flow_replay_ok':result.ok,
            'actions':driver.actions,'passed':result.ok,'result_verified':True,'model_calls_during_replay':0,'observed_page_non_get_requests':[r for r in requests if r['method'] not in ('GET','HEAD')],
            'scope':'Dedicated headless Chromium viewport, no user profile. Tacto BrowserDriver + Flow replay. Guided pointer is a decorative recording overlay. Pauses inserted for legibility; not a performance measurement.'}
        context.close();context=None
    finally:
        if context:context.close()
        inner.close()
    movie=assets/f'browser-{name}.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(source_video),'-c:v','libx264','-preset','slow','-crf','25','-pix_fmt','yuv420p','-movflags','+faststart','-an',str(movie)],check=True)
    report['video']={'file':movie.name,'bytes':movie.stat().st_size,'sha256':hashlib.sha256(movie.read_bytes()).hexdigest(),
        'seconds':round(float(subprocess.check_output(['ffprobe','-v','quiet','-show_entries','format=duration','-of','csv=p=0',str(movie)],text=True)),3)}
    assert movie.stat().st_size<4*1024*1024
    (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'scenario':name,'replay_ok':result.ok,'result_verified':True,'seconds':report['video']['seconds'],'bytes':report['video']['bytes'],'result':verified},ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--assets',type=Path,default=Path(__file__).parent/'dist/assets/demos')
    parser.add_argument('--scenario',choices=['wikipedia','shopping','form'])
    args=parser.parse_args();args.output.mkdir(parents=True,mode=0o700,exist_ok=True);args.assets.mkdir(parents=True,exist_ok=True)
    reports=[]
    for name in ([args.scenario] if args.scenario else ['wikipedia','shopping','form']):reports.append(run(name,args.output.resolve(),args.assets.resolve()))
    (args.output/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
