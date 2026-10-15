#!/usr/bin/env python3
"""Record additional real public browser applications through Tacto Flow."""
import argparse,json,re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from record_browser_demos import run
from nlc.flow import pin_params

MARKDOWN='# Weekend plan\n\n## Saturday\n- Visit the museum\n- Meet friends at 2 pm\n\n**Bring a notebook.**\n'
QUERY='is:issue state:open label:bug sort:comments-desc'
def field(name,role='textbox'):return {'role':role,'title':name}
def check(name,value,role='textbox'):return {'field':field(name,role),'attribute':'Value','equals':value}
def definition(name):
 if name=='translate':return 'https://translate.google.com/?sl=en&tl=ko&op=translate',[
  {'action':'set_value','selector':field('Source text','combobox'),'params':{'value':'Hello. Thank you.'},'postcondition':check('Source text','Hello. Thank you.','combobox')},
  {'action':'read','selector':field('Translation results','region'),'postcondition':{'contains':'감사'},'safety':{'effects':['read']},'answers':True}]
 if name=='github':return 'https://github.com/microsoft/vscode/issues',[
  {'action':'set_value','selector':field('Search issues','combobox'),'params':{'value':QUERY},'postcondition':check('Search issues',QUERY,'combobox')},
  {'action':'click','selector':field('Search','button'),'postcondition':{'contains':'Open'}},
  {'action':'read','selector':field('Issues','list'),'postcondition':check('Search issues',QUERY,'combobox'),'safety':{'effects':['read']},'answers':True}]
 if name=='units':
  to={'role':'listbox','allow_nth':1}
  return 'https://www.unitconverters.net/length-converter.html',[
   {'action':'click','selector':{'role':'option','title':'foot [ft]','allow_nth':1},'postcondition':{'field':field('To:'),'attribute':'Value','contains':'11.482'}},
   {'action':'set_value','selector':field('From:'),'params':{'value':'3.5'},'postcondition':check('From:','3.5')},
   {'action':'read','selector':field('To:'),'postcondition':{'field':field('To:'),'attribute':'Value','contains':'11.482'},'safety':{'effects':['read']},'answers':True}]
 return 'https://markdownlivepreview.com/',[
  {'action':'set_value','selector':field('Editor content'),'params':{'value':MARKDOWN},'postcondition':{'field':field('Weekend plan','heading'),'attribute':'Title','equals':'Weekend plan'}},
  {'action':'read','selector':field('Weekend plan','heading'),'postcondition':{'contains':'Bring a notebook.'},'safety':{'effects':['read']},'answers':True}]

def verifier(name):
 def verify(page,driver):
  if name=='translate':
   output=page.get_by_role('region',name='Translation results',exact=True);output.get_by_text(re.compile('감사')).first.wait_for();text=output.inner_text();assert '감사' in text and re.search('[가-힣]',text)
   assert page.get_by_role('combobox',name='Source text',exact=True).input_value()=='Hello. Thank you.'
   return {'source':'Hello. Thank you.','korean_output_present':True,'output':text[:450],'signed_in':False},output.bounding_box()
  if name=='github':
   page.wait_for_function('(query)=>new URL(location.href).searchParams.get("q")===query',arg=QUERY)
   items=page.get_by_role('list',name='Issues',exact=True);assert items.get_by_role('listitem').count()>0
   assert page.get_by_role('combobox',name='Search issues',exact=True).input_value()==QUERY
   return {'query':QUERY,'result_count_on_page':items.get_by_role('listitem').count(),'url':page.url,'issue_created':False},page.get_by_role('combobox',name='Search issues',exact=True).bounding_box()
  if name=='units':
   value=page.get_by_role('textbox',name='To:',exact=True).input_value();assert abs(float(value)-3.5/.3048)<.00001
   assert page.locator('select').nth(0).locator('option:checked').inner_text()=='meter [m]'
   selected=page.locator('select').nth(1).locator('option:checked').inner_text().strip();assert selected.startswith('foot [ft] ('),repr(selected)
   assert page.locator('select').nth(1).input_value()=='foot [ft] [3.2808398950131E+31]'
   return {'meters':3.5,'feet':value,'independent_formula':'3.5 / 0.3048','matches_within':0.00001},page.get_by_role('textbox',name='To:',exact=True).bounding_box()
  assert page.get_by_role('heading',name='Weekend plan',exact=True).count()==1
  assert page.get_by_role('heading',name='Saturday',exact=True).count()==1
  assert page.locator('strong').filter(has_text='Bring a notebook.').count()==1
  assert page.get_by_role('listitem').filter(has_text='Visit the museum').count()==1
  return {'heading':'Weekend plan','subheading':'Saturday','plan_list_items':2,'remaining_sample_content':True,'bold_text':'Bring a notebook.','exported':False},page.get_by_role('heading',name='Weekend plan',exact=True).bounding_box()
 return verify

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--assets',type=Path,default=Path(__file__).parent/'dist/assets/demos');p.add_argument('--scenario',choices=['translate','github','units','markdown']);a=p.parse_args();a.output.mkdir(parents=True,mode=0o700,exist_ok=True);a.assets.mkdir(exist_ok=True,parents=True)
 reports=[]
 for name in [a.scenario] if a.scenario else ['translate','github','units','markdown']:
  url,steps=definition(name)
  if name=='translate':
   steps[0]['safety']={'effects':['transmit'],'data_classes':['none'],'resource':{'object_type':'synthetic demo sentence','scope':'entry'},'destination':{'kind':'third_party','identity':'https://translate.google.com'},'reversibility':'irreversible'}
   steps[0]['binding']={'bundle_id':'https://translate.google.com','role':'combobox','title':'Source text','operation':'set_value','params_digest':pin_params({'value':'Hello. Thank you.'})}
  if name=='units':steps=[steps[1],steps[0],steps[2]]
  reports.append(run(name,a.output.resolve(),a.assets.resolve(),definition=(url,steps),verify=verifier(name),approve=name=='translate',inspect_unverified_transmit=name=='translate'))
 (a.output/'report.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
