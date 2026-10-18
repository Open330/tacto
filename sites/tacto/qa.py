#!/usr/bin/env python3
"""Browser checks for the served Tacto page. Playwright is a QA-only dependency."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright


def audit(url, output, axe_source=None, browser_executable=None):
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    checks = {}
    errors, requests, accessibility = [], [], {}
    origin = urlsplit(url)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=browser_executable)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.on('console', lambda message: errors.append(message.text)
                if message.type == 'error' else None)
        page.add_init_script('''
          window.__metrics = {lcp:0, cls:0};
          new PerformanceObserver(list => {for(const e of list.getEntries())
            window.__metrics.lcp=e.startTime}).observe({type:'largest-contentful-paint',buffered:true});
          new PerformanceObserver(list => {for(const e of list.getEntries())
            if(!e.hadRecentInput)window.__metrics.cls+=e.value}).observe({type:'layout-shift',buffered:true});
          Object.defineProperty(navigator,'clipboard',{value:undefined});
          const nativeCopy=document.execCommand.bind(document);
          document.execCommand=(name,...args)=>{
            if(name==='copy')window.__copied=document.querySelector('textarea.clipboard-fallback')?.value;
            return nativeCopy(name,...args);
          };
        ''')
        response = page.goto(url, wait_until='networkidle')
        checks['http_200'] = response.status == 200
        checks['one_h1'] = page.locator('h1').count() == 1
        checks['eleven_recorded_cases'] = page.locator('#use-cases .recorded-case').count() == 11
        checks['ten_everyday_cases'] = page.locator('.everyday-case').count() == 10
        checks['eight_real_app_cases'] = page.locator('.everyday-case[data-real="true"]').count() == 8
        checks['case_screenshots'] = page.locator('.case-source').evaluate_all(
            '(items)=>items.length===11&&items.every(e=>e.href.endsWith(".png"))')
        checks['seven_browser_examples'] = page.locator('.everyday-case video[width="1280"]').count() == 7
        checks['workflow_has_real_video'] = page.locator('#task-carousel video source').count() == 1

        checks['product_icons_loaded'] = page.locator('.agent-icon').evaluate_all(
            '(items)=>items.length===4&&items.every(e=>e.complete&&e.naturalWidth>0)')
        checks['no_placeholder_icon_glyphs'] = page.evaluate(
            r'!/[' + '✳⌘↗↓↳↔∅✓↻■⌁' + r']/.test(document.body.innerText)')
        checks['vector_icons_render'] = page.locator('svg.ui-icon use').evaluate_all(
            '(items)=>items.length>20&&items.every(e=>e.getBBox().width>0&&e.getBBox().height>0)')

        checks['anchors_resolve'] = page.evaluate('''() => [...document.querySelectorAll('a[href^="#"]')]
          .every(a=>!a.hash||document.getElementById(decodeURIComponent(a.hash.slice(1))))''')
        initial = page.evaluate('''({vitals:window.__metrics,
          resources:performance.getEntriesByType('resource').map(r=>({url:r.name,
          encoded:r.encodedBodySize,decoded:r.decodedBodySize}))})''')
        page.keyboard.press('Tab')
        checks['skip_link_first'] = page.locator('.skip').evaluate('(e)=>e===document.activeElement')
        page.keyboard.press('Enter')
        checks['skip_focuses_main'] = page.evaluate('document.activeElement.id==="main"')

        checks['four_workflow_choices'] = page.locator('#task-carousel [data-workflow]').count() == 4
        for task, evidence in [('triage', 'github'), ('expenses', 'finder'),
                               ('support', 'translate'), ('backoffice', 'form')]:
            page.locator('[data-workflow="'+task+'"]').click()
            checks['workflow_'+task] = page.evaluate('''evidence => {
                const video = document.querySelector('#task-carousel video');
                const source = document.querySelector('#example-'+evidence+' video source');
                return video.src === source.src &&
                    document.querySelector('#carousel-detail').getAttribute('href') === '#example-'+evidence &&
                    document.querySelector('.scenario-status').textContent.includes('전체 실행 미검증');
            }''', evidence)
        page.locator('[data-workflow="triage"]').click()

        for name, status in [('repair', 'CHECKED CONTINUATION'),
                             ('stop', 'STOPPED · NO RESEND'), ('replay', 'VERIFIED REPLAY')]:
            tab = page.locator('[data-demo="'+name+'"]')
            tab.click()
            checks['demo_'+name] = (page.locator('#demo-status').inner_text() == status
                                   and tab.get_attribute('aria-selected') == 'true')
        page.locator('[data-demo="replay"]').focus()
        for key, name in [('ArrowRight', 'repair'), ('ArrowRight', 'stop'),
                          ('ArrowRight', 'replay'), ('ArrowLeft', 'stop'),
                          ('Home', 'replay'), ('End', 'stop')]:
            page.keyboard.press(key)
            tab = page.locator('[data-demo="'+name+'"]')
            checks['keyboard_'+key+'_'+name] = (tab.get_attribute('aria-selected') == 'true'
                and tab.evaluate('(e)=>e===document.activeElement')
                and page.locator('#demo-panel').get_attribute('aria-labelledby') == 'demo-tab-'+name)
        checks['one_tab_stop'] = page.locator('[role="tab"][tabindex="0"]').count() == 1
        checks['no_autoplay_or_preload'] = page.locator('video').evaluate_all(
            '(items)=>items.length===12&&items.every(e=>!e.autoplay&&e.preload==="none"&&e.parentElement.querySelector(".video-start"))')
        page.locator('.fixture-example summary').click()
        for index in range(page.locator('video').count()):
            video = page.locator('video').nth(index)
            video.evaluate('(e)=>e.load()')
            page.wait_for_function('(i)=>document.querySelectorAll("video")[i].readyState>=2', arg=index)
            checks['video_decodes_'+str(index)] = video.evaluate('(e)=>e.duration>3&&e.videoWidth>0&&!e.error')
            video.evaluate('(e)=>e.currentTime=3')
            page.wait_for_function('(i)=>!document.querySelectorAll("video")[i].seeking', arg=index)
            checks['video_seeks_'+str(index)] = video.evaluate('(e)=>Math.abs(e.currentTime-3)<0.3')
            video.evaluate('(e)=>{e.currentTime=0;e.pause();}')

        for kind, expected in [('native',3),('web',5),('test',2),('all',10)]:
            button = page.locator('[data-example-filter="'+kind+'"]')
            button.click()
            checks['gallery_filter_'+kind] = (page.locator('.everyday-case:visible').count()==expected
                and button.get_attribute('aria-pressed')=='true'
                and page.locator('#example-count').inner_text().startswith(str(expected)+'개'))
        page.locator('.everyday-case .video-start').first.click()
        page.wait_for_function('()=>document.querySelector(".everyday-case video").currentTime>.2')
        page.locator('[data-example-filter="web"]').click()
        checks['filter_pauses_hidden_video'] = page.locator('.everyday-case video').first.evaluate('(v)=>v.paused')
        page.locator('[data-example-filter="all"]').click()

        for agent in ['Codex', 'Claude']:
            page.locator('[data-agent="'+agent+'"]').click()
            checks['agent_'+agent] = page.locator('#agent-label').inner_text() == agent
        for key in ['install-command', 'doctor-command', 'agent-prompt', 'demo-command']:
            button = page.locator('[data-copy="'+key+'"]')
            button.evaluate('''button => {
                for (let node = button.parentElement; node; node = node.parentElement)
                    if (node.tagName === 'DETAILS') node.open = true;
            }''')
            button.click()
            checks['copy_'+key] = (page.evaluate('window.__copied') == page.locator('#'+key).inner_text().strip()
                and page.locator('.clipboard-fallback').count() == 0
                and button.evaluate('(e)=>e===document.activeElement'))
        for failure in ['return false', 'throw new Error("denied")']:
            page.evaluate('() => { document.execCommand=()=>{'+failure+';}; }')
            button = page.locator('[data-copy="doctor-command"]')
            button.click()
            checks['copy_cleanup_'+failure] = (page.locator('.clipboard-fallback').count() == 0
                and page.locator('.toast').inner_text().startswith('자동 복사가 불가능')
                and button.evaluate('(e)=>e===document.activeElement'))

        def run_axe(name):
            if axe_source:
                page.evaluate(axe_source)
                result = page.evaluate('axe.run()')
                accessibility[name] = result
                checks['axe_'+name] = not result['violations']

        # The collapsed page and expanded content are separate visible states.
        run_axe('desktop')
        page.locator('.methodology summary').click()
        checks['methodology_opens'] = page.locator('.methodology').get_attribute('open') is not None
        page.locator('.faq-items summary').first.click()
        checks['faq_opens'] = page.locator('.faq-items details').first.get_attribute('open') is not None
        page.locator('details').evaluate_all('(items)=>items.forEach(e=>e.open=true)')
        run_axe('expanded')
        for width in [320, 360, 390, 768]:
            page.set_viewport_size({'width': width, 'height': 844})
            checks['no_overflow_'+str(width)] = page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.set_viewport_size({'width': 390, 'height': 844})
        run_axe('mobile')
        checks['mobile_touch_targets'] = page.locator('button:visible').evaluate_all(
            '(items)=>items.every(e=>e.getBoundingClientRect().height>=44)')
        page.locator('details').evaluate_all('(items)=>items.forEach(e=>e.open=false)')
        page.locator('[data-demo="replay"]').click()
        page.wait_for_timeout(2800)
        page.locator('header').scroll_into_view_if_needed()
        page.screenshot(path=str(output/'mobile.png'), full_page=True)
        page.screenshot(path=str(output/'mobile-hero.png'))
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('header').scroll_into_view_if_needed()
        page.screenshot(path=str(output/'desktop.png'), full_page=True)
        page.screenshot(path=str(output/'desktop-hero.png'))
        page.emulate_media(reduced_motion='reduce')
        checks['reduced_motion'] = page.evaluate('getComputedStyle(document.documentElement).scrollBehavior==="auto"')
        checks['no_external_requests'] = all(urlsplit(item).netloc == origin.netloc for item in requests)
        checks['no_script_or_console_errors'] = not errors
        checks['no_browser_storage'] = page.evaluate('localStorage.length===0&&sessionStorage.length===0&&document.cookie===""')
        context.close()

        nojs = browser.new_context(java_script_enabled=False, viewport={'width':390,'height':844})
        fallback = nojs.new_page()
        fallback.goto(url, wait_until='networkidle')
        checks['nojs_all_ten_cases'] = fallback.locator('.everyday-case:visible').count()==10
        checks['nojs_native_video_controls'] = fallback.locator('video').evaluate_all('(items)=>items.length===12&&items.every(e=>e.controls)')
        checks['nojs_example_complete'] = fallback.locator('#demo-lines').inner_text().endswith('결과 조건 검증 완료')
        checks['nojs_install_present'] = fallback.locator('#start a[href*="/releases/download/"][href$=".dmg"]').is_visible()
        checks['nojs_controls_disabled'] = fallback.locator('[data-copy="install-command"]').is_disabled()
        fallback.locator('.faq-items summary').first.click()
        checks['nojs_faq_works'] = fallback.locator('.faq-items details').first.get_attribute('open') is not None
        checks['nojs_no_overflow'] = fallback.locator('html').bounding_box()['width'] <= 390
        nojs.close()
        browser.close()

    report = {'url':url,'checks':checks,'errors':errors,'initial_load':initial,
              'axe_version':next(iter(accessibility.values()))['testEngine']['version'] if accessibility else None,
              'axe_violations':{name:value['violations'] for name,value in accessibility.items()}}
    (output/'qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':sum(checks.values()),'total':len(checks),
                      'failed':[name for name,value in checks.items() if not value],
                      'axe_violations':{name:[x['id'] for x in value['violations']]
                                        for name,value in accessibility.items()},'errors':errors},
                     ensure_ascii=False,indent=2))
    return all(checks.values())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8787')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--axe',type=Path,help='Local axe.min.js; omit to skip automated axe checks')
    parser.add_argument('--browser-executable', help='Optional local Chromium executable for QA')
    args=parser.parse_args()
    passed=audit(args.url,args.output,args.axe.read_text() if args.axe else None,args.browser_executable)
    raise SystemExit(0 if passed else 1)


if __name__=='__main__':
    main()
