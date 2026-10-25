"""Window-bound drag: one stroke, fresh observation, no automatic retry."""
import argparse
import json
import math
import re


def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def drag_problem(action):
    required = {'from', 'to', 'duration'}
    if not required <= action.keys() or action.keys() - (required | {'name', 'element_index', 'via'}):
        return 'drag requires from, to, duration; optional via'
    duration = action['duration']
    if not _finite_number(duration) or not 0.1 <= duration <= 2:
        return 'duration must be 0.1 to 2 seconds'
    via = action.get('via', [])
    if not isinstance(via, list) or len(via) > 12:
        return 'via must contain at most 12 points'
    for point in [action['from'], *via, action['to']]:
        if (not isinstance(point, dict) or set(point) != {'x', 'y'} or any(
                not _finite_number(v) or v < 0 for v in point.values())):
            return 'each point must be finite nonnegative {x,y} window points'
    if all(point == action['from'] for point in [*via, action['to']]):
        return 'drag stroke must move'
    return None


def drag_event_count(action):
    points=[action['from'],*action.get('via',[]),action['to']]
    lengths=[math.hypot(b['x']-a['x'],b['y']-a['y']) for a,b in zip(points,points[1:])]
    total=sum(lengths)
    if not math.isfinite(total) or total<=0:return None
    count=max(2,math.ceil(action['duration']*30))
    return 2+sum(max(1,math.ceil(count*length/total)) for length in lengths if length>0)


def _main(argv, driver):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', help='application bundle ID')
    parser.add_argument('--helper', action='store_true', required=True)
    parser.add_argument('--prepare', action='store_true', help='Prepare the background AppKit window before this stroke')
    parser.add_argument('--json', action='store_true', help='Include the complete fresh observation and diagnostics')
    parser.add_argument('--window', help='exact observed window title; required when multiple windows exist')
    parser.add_argument('--from', dest='start', nargs=2, type=float, required=True, metavar=('X','Y'))
    parser.add_argument('--to', nargs=2, type=float, required=True, metavar=('X','Y'))
    parser.add_argument('--via', nargs=2, type=float, action='append', default=[])
    parser.add_argument('--duration', type=float, default=0.6)
    args = parser.parse_args(argv)
    from .flow import Selector
    action = {'name':'drag', 'duration':args.duration,
              'from':dict(zip(('x','y'),args.start)), 'to':dict(zip(('x','y'),args.to))}
    if args.via:
        action['via'] = [dict(zip(('x','y'),point)) for point in args.via]
    problem = drag_problem(action)
    if problem:
        parser.error(problem)
    if 'action.drag' not in getattr(driver,'capabilities', ()):
        print(json.dumps({'error':'drag_unavailable','effect':'not_started','detail':'update the signed helper'}))
        return 1
    obs = driver.observe(args.target, 'tree_full')
    if obs.get('type') != 'observation':
        print(json.dumps(obs,ensure_ascii=False)); return 1
    indices = (Selector(role='Window',title=args.window).resolve_all(obs['tree']) if args.window else
               [int(match.group(1)) for line in obs['tree'].splitlines()
                if (match := re.match(r'^(\d+) Window(?:\s|$)',line))])
    if len(indices) != 1:
        print(json.dumps({'error':'window_missing_or_ambiguous','effect':'not_started','detail':'specify --window with an exact observed title'}))
        return 1
    action['element_index'] = indices[0]
    if args.prepare:
        prepared = driver.act(obs['obs_id'],args.target,{'name':'prepare_window','element_index':indices[0]},
                              risk_class='preapproved',approved=True,intent_text='Prepare requested drag window')
        if prepared.get('type') != 'acted':
            print(json.dumps({'stage':'prepare','dispatch':prepared},ensure_ascii=False));return 1
        # A prepare can change the element map. Resolve the same title again.
        from .document_label import window_attributes
        header=next(line for line in obs['tree'].splitlines() if re.match(r'^'+str(indices[0])+r' Window(?:\s|$)',line))
        title=(window_attributes(header) or {}).get('Title')
        obs=driver.observe(args.target,'tree_full')
        if obs.get('type') != 'observation' or not title:
            print(json.dumps({'stage':'prepare_observe','observation':obs},ensure_ascii=False));return 1
        indices=Selector(role='Window',title=title).resolve_all(obs['tree'])
        if len(indices)!=1:
            print(json.dumps({'error':'prepared_window_changed','effect':'unknown'}));return 1
        action['element_index']=indices[0]
    reply = driver.act(obs['obs_id'], args.target, action, risk_class='preapproved', approved=True,
                       intent_text='Requested window-bound drag')
    # Posting is not a receiving-side proof. Expose a fresh observation for the
    # caller to check; a partially completed stroke is never sent again here.
    fresh = driver.observe(args.target, 'tree_full') if reply.get('type') == 'acted' or reply.get('effect') == 'unknown' else None
    if args.json:
        output={'dispatch':reply,'observation':fresh}
    else:
        output={k:v for k,v in reply.items() if k in ('type','error','effect','detail','action','input_route','posted_events','reobserve_required','unverified')}
        if fresh:
            output['observed']=fresh.get('type')=='observation'
            if fresh.get('error'):output['observation_error']=fresh['error']
        output['verification']='check the receiving result; posted events alone do not prove it'
    print(json.dumps(output,ensure_ascii=False))
    return 0 if reply.get('type') == 'acted' else 1


def main(argv, driver):
    try:
        return _main(argv, driver)
    except (ConnectionError, OSError, TimeoutError, RuntimeError) as error:
        # Transport failures may occur after a mouse-down. Do not claim that
        # nothing happened or echo provider messages containing screen data.
        print(json.dumps({'error':'drag_connection_failed','effect':'unknown',
                          'exception':type(error).__name__,'retry':False}))
        return 1


def capture_main(argv, driver):
    """Read-only window capture for choosing drag points without model calls."""
    from pathlib import Path
    from .native_visual_actions import capture_window
    from .flow import Selector
    from .access_log import record_failure
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target')
    parser.add_argument('--helper',action='store_true',required=True)
    parser.add_argument('--window')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        obs=driver.observe(args.target,'tree_full')
        if obs.get('type')!='observation':
            print(json.dumps(obs,ensure_ascii=False));return 1
        indices=(Selector(role='Window',title=args.window).resolve_all(obs['tree']) if args.window else
                 [int(match.group(1)) for line in obs['tree'].splitlines()
                  if (match:=re.match(r'^(\d+) Window(?:\s|$)',line))])
        if len(indices)!=1:
            record_failure('window_missing_or_ambiguous','select_window')
            print(json.dumps({'error':'window_missing_or_ambiguous','detail':'specify --window with an exact observed title'}));return 1
        image=capture_window(driver,obs['obs_id'],args.target,window_index=indices[0])
        # Do not overwrite an existing user's image or follow a destination symlink.
        with args.output.open('xb') as stream:
            import os
            os.fchmod(stream.fileno(),0o600)
            stream.write(image.png)
        print(json.dumps({'type':'capture','path':str(args.output),'pixels':image.viewport,
                          'window_points':{'width':image.window_frame[2],'height':image.window_frame[3]},
                          'pixel_to_point':{'x':image.window_frame[2]/image.viewport['width'],
                                            'y':image.window_frame[3]/image.viewport['height']}},ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        result={'error':'window_capture_failed','effect':'not_started','exception':type(error).__name__}
        if re.fullmatch(r'[a-z_]+(?::[a-z_]+)?',str(error)):result['detail']=str(error)
        record_failure('output_exists' if isinstance(error,FileExistsError) else 'window_capture_failed','capture')
        print(json.dumps(result))
        return 1
