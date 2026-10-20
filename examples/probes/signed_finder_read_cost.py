"""Bounded read-only RPC comparison; no Finder input or window creation."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

ROLES={'Window','ScrollArea','Group','Row','Cell'}


def rows_valid(rows, count, *, partial=False):
    if type(rows) is not list or (len(rows)>count*2 if partial else len(rows)!=count*2):
        return False
    for position,row in enumerate(rows):
        required={'ordinal','round','batch_first','batch_ax_us','single_ax_us',
                  'batch_transport','batch_attributes','single_attributes'}
        if type(row) is not dict:
            return False
        if partial:
            if not {'ordinal','round','batch_first'}<=set(row) or not set(row)<=required:return False
        elif set(row)!=required:return False
        if (type(row['ordinal']) is not int or row['ordinal']!=position//2
                or type(row['round']) is not int or row['round']!=position%2
                or type(row['batch_first']) is not bool
                or row['batch_first']!=((position//2+position%2)%2==0)
                or ('batch_transport' in row and row['batch_transport'] not in ('ok','unsupported'))):
            return False
        for key in ('batch_ax_us','single_ax_us'):
            if key not in row:continue
            if type(row[key]) is not int or not 0<=row[key]<=2000000:return False
        for key in ('batch_attributes','single_attributes'):
            if key not in row:continue
            values=row[key]
            if key=='batch_attributes' and row.get('batch_transport')=='unsupported':
                if values is not None:return False
                continue
            if type(values) is not list or len(values)!=3:return False
            for value in values:
                if (type(value) is not dict or set(value) not in ({'ax_error','kind'},{'ax_error','kind','count'})
                        or type(value['ax_error']) is not int or not -65536<=value['ax_error']<=65536
                        or type(value['kind']) is not str or value['kind'] not in ('nil','string','boolean','array','other')):
                    return False
                if value['kind']=='array':
                    if type(value.get('count')) is not int or not 0<=value['count']<=1000000:return False
                elif 'count' in value:return False
    return True


def preflight_summary(reply):
    """Retain only closed counters; an error detail may contain UI text."""
    reasons={'read_complete','invalid_or_stale_preflight','preflight_window_owner_unavailable',
             'preflight_window_role_unavailable','preflight_deadline','preflight_nodes','preflight_unavailable'}
    if (type(reply) is not dict or reply.get('type') not in ('error','readiness_preflight')
            or reply.get('read_only') is not True or reply.get('authorizes_input') is not False
            or type(reply.get('reason')) is not str or reply.get('reason') not in reasons):return None
    result={'type':reply['type'],'reason':reply['reason'],'read_only':True,'authorizes_input':False}
    if 'titlebar_hits' in reply:
        hits=reply['titlebar_hits']
        fields={'status','role','same_owner','is_window','same_window','actions_readable','has_actions','clear_margin'}
        roles={'AXWindow','AXToolbar','AXGroup','AXButton','AXStaticText','AXImage','other'}
        if type(hits) is not list or len(hits) not in (3,15):return None
        for hit in hits:
            if (type(hit) is not dict or set(hit)!=fields or type(hit['role']) is not str
                    or hit['role'] not in roles
                    or type(hit.get('status')) is not str or hit['status'] not in {'read_complete','anchor_unavailable','hit_unavailable',
                        'owner_unavailable','cross_process','ancestry_unavailable','margin_unavailable'}
                    or any(type(hit[key]) is not bool for key in fields-{'role','status'})):return None
        result['titlebar_hits']=[dict(hit) for hit in hits]
    if 'read_strategy' in reply:
        if type(reply['read_strategy']) is not str or reply['read_strategy'] not in ('batch','single'):return None
        result['read_strategy']=reply['read_strategy']
    for key,limit in (('elapsed_ms',600000),('scoped_ax_operations',1000000),
                      ('ax_operation_elapsed_ms',600000),('context_elapsed_ms',600000)):
        value=reply.get(key)
        if type(value) is not int or not 0<=value<=limit:return None
        result[key]=value
    # Reuse the existing closed progress validator without publishing its
    # validation envelope as if this were a window-input preparation result.
    if 'readiness_progress' in reply:
        from tacto.preparation_diagnostic import preparation_diagnostic
        checked=preparation_diagnostic({'phase':'context','reason':'unclassified_refusal',
            'elapsed_ms':0,'readiness_progress':reply['readiness_progress']})
        if checked is None:return None
        result['readiness_progress']=checked['readiness_progress']
    if 'readiness_failure' in reply:
        from tacto.preparation_diagnostic import readiness_failure
        failure=readiness_failure(reply['readiness_failure'])
        if failure is None:return None
        result['readiness_failure']=failure
    if 'readiness_ax_error' in reply:
        error=reply['readiness_ax_error']
        if type(error) is not int or not -65536<=error<=65536 or error==0:return None
        result['readiness_ax_error']=error
    if 'readiness_identity_counts' in reply:
        counts=reply['readiness_identity_counts']
        if (type(counts) is not dict or set(counts)!= {'unique_nodes','repeated_nodes','visits'}
                or any(type(v) is not int or not 0<=v<=20000 for v in counts.values())
                or counts['visits']!=counts['unique_nodes']+counts['repeated_nodes']
                or 'readiness_progress' not in result
                or counts['visits']!=result['readiness_progress']['total_nodes']):
            return None
        result['readiness_identity_counts']=dict(counts)
    if 'readiness_role_costs' in reply:
        rows=reply['readiness_role_costs'];roles=set()
        allowed={'AXWindow','AXScrollArea','AXGroup','AXRow','AXCell','AXSheet','AXStaticText',
                 'AXImage','AXButton','AXTextField','AXOutline','AXSplitGroup','AXToolbar',
                 'AXPopUpButton','AXDisclosureTriangle','other','unreadable'}
        fields={'role','nodes','scoped_ax_operations','ax_operation_us','context_us','wall_us'}
        if type(rows) is not list or len(rows)>len(allowed):return None
        for row in rows:
            if (type(row) is not dict or set(row)!=fields or type(row['role']) is not str
                    or row['role'] not in allowed or row['role'] in roles):return None
            roles.add(row['role'])
            for key in fields-{'role'}:
                limit=1000000 if key in ('nodes','scoped_ax_operations') else 600000000
                if type(row[key]) is not int or not 0<=row[key]<=limit:return None
        if (sum(row['scoped_ax_operations'] for row in rows)>result['scoped_ax_operations']
                or sum(row['ax_operation_us'] for row in rows)>result['ax_operation_elapsed_ms']*1000+999
                or sum(row['context_us'] for row in rows)>result['context_elapsed_ms']*1000+999
                or sum(row['wall_us'] for row in rows)>result['elapsed_ms']*1000+999):return None
        if 'readiness_progress' in result and sum(row['nodes'] for row in rows)!=result['readiness_progress']['total_nodes']:
            return None
        result['readiness_role_costs']=[dict(row) for row in rows]
    if 'readiness_attribute_costs' in reply:
        rows=reply['readiness_attribute_costs'];names=set()
        allowed={'AXRole','AXModal','AXChildren','AXWindows','AXMinimized','AXParent','AXSheets','other'}
        if type(rows) is not list or len(rows)>len(allowed):return None
        for row in rows:
            if (type(row) is not dict or set(row)!={'attribute','scoped_ax_operations','ax_operation_us'}
                    or type(row['attribute']) is not str or row['attribute'] not in allowed
                    or row['attribute'] in names):return None
            names.add(row['attribute'])
            for key,limit in (('scoped_ax_operations',1000000),('ax_operation_us',600000000)):
                if type(row[key]) is not int or not 0<=row[key]<=limit:return None
        if (sum(row['scoped_ax_operations'] for row in rows)>result['scoped_ax_operations']
                or sum(row['ax_operation_us'] for row in rows)>result['ax_operation_elapsed_ms']*1000+999):return None
        result['readiness_attribute_costs']=[dict(row) for row in rows]
    return result


def preflight_for_strategy(reply, strategy):
    summary=preflight_summary(reply)
    if summary is None or summary.get('read_strategy','batch')!=strategy:return None
    return summary


def main(driver, output, *, include_preflight=False, read_strategy='batch', collect_cpu=False, titlebar_probe=False):
    if type(titlebar_probe) is not bool or (titlebar_probe and not include_preflight):raise ValueError('titlebar probe needs preflight')
    if type(include_preflight) is not bool:raise ValueError('invalid preflight mode')
    if type(read_strategy) is not str or read_strategy not in ('batch','single'):raise ValueError('invalid read strategy')
    if read_strategy!='batch' and not include_preflight:raise ValueError('read strategy needs preflight')
    if type(collect_cpu) is not bool or (collect_cpu and not include_preflight):raise ValueError('CPU capture needs preflight')
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'kind':'read-only AX RPC sample, not Finder task completion',
            'model_requests':0,'all_dependencies_frozen':False,'read_strategy':read_strategy}
    interrupted=None
    with os.fdopen(descriptor,'w') as stream:
        try:
            from tacto.helper import InheritedHelperDriver
            if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
                raise RuntimeError('signed_helper_required')
            if 'diagnostic.readiness_read_cost' not in driver.capabilities:
                raise RuntimeError('readiness_cost_capability_unavailable')
            from tacto.input_audit_process import ProcessInputAudit
            root=Path(__file__).resolve().parents[2]
            paths=list((root/'tacto').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
                Path(__file__).resolve(),root/'examples/eval.py',root/'launcher/tacto',
                root/'launcher/tacto.swift',root/'helper/tacto-helper',
                root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',root/'helper/build.sh',
                root/'helper/bundle.sh',root/'signing.sh']
            def manifest():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
            report['source_subset_before']=manifest()
            audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
            try:
                with audit:
                    observation=driver.observe('com.apple.finder','tree_full')
                    if observation.get('type')!='observation':raise RuntimeError('finder_observation_unavailable')
                    by_role={role:[] for role in sorted(ROLES)}
                    for line in observation['tree'].splitlines():
                        parts=line.split()
                        if len(parts)>1 and parts[0].isdigit() and parts[1] in ROLES:
                            by_role[parts[1]].append(int(parts[0]))
                    selected=[]
                    for role,indices in by_role.items():
                        if indices:
                            selected.append((indices[0],role))
                            if indices[-1]!=indices[0]:selected.append((indices[-1],role))
                    report['observation_truncated']=observation.get('truncated') is True
                    report['sampling']='first and last exposed index per role; not the entire Finder tree'
                    if not selected:raise RuntimeError('finder_candidates_unavailable')
                    report['sample_roles']=[role for _,role in selected]
                    reply=driver._send({'type':'readiness_read_cost','target':'com.apple.finder',
                        'obs_id':observation['obs_id'],'element_indices':[index for index,_ in selected]})
                    report['response_type']=reply.get('type') if reply.get('type') in ('error','readiness_read_cost') else 'other'
                    if reply.get('type')!='readiness_read_cost':
                        report['error_code']=reply.get('error') if reply.get('error') in (
                            'readiness_cost_unavailable','invalid_readiness_cost_request','stale_observation',
                            'aborted_by_user','rejected_by_launcher') else 'other'
                        # Partial measurements contain no UI strings; retain privately.
                        if rows_valid(reply.get('rows'),len(selected),partial=True):
                            report['partial_rows']=reply['rows']
                        else:report['partial_rows_unavailable']=True
                        raise RuntimeError('readiness_cost_incomplete')
                    if (not rows_valid(reply.get('rows'),len(selected)) or reply.get('read_only') is not True
                            or reply.get('atomic_snapshot') is not False
                            or type(reply.get('scoped_ax_operations')) is not int
                            or reply['scoped_ax_operations']!=len(selected)*8):
                        raise RuntimeError('readiness_cost_shape_unavailable')
                    report['rows']=reply['rows'];report['scoped_ax_operations']=reply.get('scoped_ax_operations')
                    report['rpc_only_us']={'batch_median':statistics.median(row['batch_ax_us'] for row in reply['rows']),
                        'three_singles_median':statistics.median(row['single_ax_us'] for row in reply['rows'])}
                    report['attribute_shapes_equal']=all(row['batch_attributes']==row['single_attributes'] for row in reply['rows'])
                    if include_preflight:
                        windows=by_role['Window']
                        if not windows or 'diagnostic.readiness_preflight' not in driver.capabilities:
                            raise RuntimeError('readiness_preflight_unavailable')
                        report['preflight_sampling']='first exposed Window after bounded AX sample on same channel; not a cold-task guarantee'
                        cpu_before=None
                        if collect_cpu:
                            from AppKit import NSWorkspace
                            from tacto.process_cpu import sample_process_cpu,cpu_delta_ns,verify_cpu_clock
                            from dataclasses import asdict
                            report['cpu_clock_validation']=verify_cpu_clock()
                            apps=[a for a in NSWorkspace.sharedWorkspace().runningApplications()
                                  if a.bundleIdentifier()=='com.apple.finder' and not a.isTerminated()]
                            if len(apps)!=1:raise RuntimeError('finder_cpu_identity_unavailable')
                            cpu_before=sample_process_cpu(int(apps[0].processIdentifier()))
                        preflight_started=time.monotonic()
                        preflight=preflight_for_strategy(driver._send({'type':'readiness_preflight',
                            'target':'com.apple.finder','obs_id':observation['obs_id'],'element_index':windows[0],
                            'read_strategy':read_strategy, **({'titlebar_probe':True} if titlebar_probe else {})}),read_strategy)
                        preflight_wall=time.monotonic()-preflight_started
                        if cpu_before is not None:
                            cpu_after=sample_process_cpu(cpu_before.pid)
                            report['preflight_process_cpu']={
                                'scope':'whole Finder process during preflight RPC; not AX/thread attribution',
                                'before':asdict(cpu_before),'after':asdict(cpu_after),
                                'cpu_delta_ns':cpu_delta_ns(cpu_before,cpu_after),'wall_seconds':preflight_wall,
                                'conversion':'Mach timebase; separate own-process calibration in this report'}
                        if preflight is None:raise RuntimeError('readiness_preflight_shape_unavailable')
                        report['preflight']=preflight
                        report['preflight_read_complete']=preflight['type']=='readiness_preflight' and preflight['reason']=='read_complete'
                        report['preparation_task_success_verified']=False
                    report['passed']=True
            finally:
                report['input_audit']=audit.report();report['source_subset_after']=manifest()
                report['source_subset_stable']=report['source_subset_after']==report['source_subset_before']
                if not report['source_subset_stable'] or report['input_audit'].get('status')!='UNCHANGED_SAMPLED':report['passed']=False
        except BaseException as error:
            report.update(passed=False,error_class=type(error).__name__);interrupted=error
        stream.write(json.dumps(report,ensure_ascii=False,indent=2));stream.flush();os.fsync(stream.fileno())
    if interrupted is not None:raise interrupted
    return report
