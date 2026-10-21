"""Fresh-context reuse arms, repetitions and their summary."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from examples import repeat_benchmark
from examples.repeat_benchmark import parse_arm, sum_usage
from examples.summarize_reuse_ratios import summarize_groups


def usage(total=10):
    return {'status':'reported','total_input_tokens':total,'new_input_tokens':2,
            'cache_read_tokens':total-2,'cache_creation_tokens':None,'output_tokens':1}


def batch(percent=0,context='fresh',rep=1,passed=True,seconds=2,total=10,provider='codex'):
    rows=[{'seconds':seconds,'exit':0,'mode':'agent','context':context,'job':{'job':f'T{i:02d}'},
           'trace_directory':f'/t/{i}','usage':usage(total),
           'oracle':{'passed':True,'single_commit':True,'active':False}} for i in range(1,11)]
    return {'arm':f'{provider}-tacto-{context}{percent}','kind':'mixed-reuse','reuse_percent':percent,'context':context,'rep':rep,'passed':passed,
            'rows':rows,'agent_runs':rows,'usage':sum_usage(rows),'setup':None,'provider':provider}


class FakeFixture:
    def __init__(self,*_):pass
    def setup(self,*_ ,**__):pass
    def oracle(self,_):return {'passed':True,'single_commit':True,'active':False}
    def close(self):pass


class ParseArmTests(unittest.TestCase):
    def test_fresh_and_ratio_arms(self):
        self.assertEqual(('codex','tacto','mixed',30,True),parse_arm('codex-tacto-fresh30'))
        self.assertEqual(('claude','tacto','mixed',70,False),parse_arm('claude-tacto-ratio70'))
        self.assertEqual(('codex','sky','workflow',None,False),parse_arm('codex-sky-workflow'))

    def test_bad_arms_are_refused(self):
        for arm in ('codex-tacto-fresh50','codex-sky-fresh30','gpt-tacto-ratio0','codex-tacto-ratio030',
                    'codex-tacto-freshx','codex-tacto','codex-tacto-other','codex-tacto-fresh-30'):
            with self.subTest(arm=arm),self.assertRaises(ValueError):parse_arm(arm)


class FreshContextTests(unittest.TestCase):
    def run_zero(self,fresh):
        calls=[]
        def agent(provider,text,trace,**kwargs):
            calls.append(kwargs)
            return {'exit':0,'seconds':1,'session_id':'s1','usage':usage(),'trace_directory':str(trace)}
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(repeat_benchmark,'Fixture',FakeFixture), \
             mock.patch.object(repeat_benchmark,'run_agent',agent), \
             mock.patch.object(repeat_benchmark,'codex_turn_delta',lambda now,_:now):
            result=repeat_benchmark.reused_flow(Path(folder),None,'t','codex',0,fresh=fresh)
        return result,calls

    def test_fresh_never_resumes_or_shares_a_workspace(self):
        result,calls=self.run_zero(True)
        self.assertEqual(10,len(calls))
        self.assertTrue(all(c=={} for c in calls))
        self.assertEqual('fresh',result['context'])
        self.assertTrue(all(r['context']=='fresh' for r in result['rows']))
        self.assertTrue(result['passed'])

    def test_resumed_carries_the_session(self):
        result,calls=self.run_zero(False)
        self.assertIsNone(calls[0]['resume'])
        self.assertTrue(all(c['resume']=='s1' for c in calls[1:]))
        self.assertEqual('resumed',result['context'])


class GroupSummaryTests(unittest.TestCase):
    def test_failure_rate_and_medians_over_repetitions(self):
        runs=[batch(rep=1,seconds=2),batch(rep=2,seconds=4),batch(rep=3,passed=False)]
        group,=summarize_groups(runs)
        self.assertEqual(3,group['runs']);self.assertEqual(2,group['passed_runs'])
        self.assertAlmostEqual(0.3333,group['failure_rate'])
        self.assertEqual(30,group['median']['seconds'])
        self.assertEqual(100,group['median']['total_input_tokens'])
        self.assertEqual(3,group['failures'][0]['rep'])

    def test_unknown_usage_keeps_the_median_unknown(self):
        good,unknown=batch(rep=1),batch(rep=2)
        unknown['rows'][0]['usage']={'status':'missing_or_ambiguous'}
        unknown['usage']=sum_usage(unknown['rows'])
        group,=summarize_groups([good,unknown])
        self.assertIsNone(group['median']['total_input_tokens'])
        self.assertEqual(20,group['median']['seconds'])

    def test_a_resumed_job_in_a_fresh_arm_is_a_failure(self):
        run=batch();run['rows'][3]['context']='resumed'
        group,=summarize_groups([run])
        self.assertEqual(1.0,group['failure_rate'])

    def test_contexts_are_compared_only_against_their_own_zero(self):
        groups=summarize_groups([batch(0,'fresh',total=10),batch(0,'resumed',total=40)])
        self.assertEqual({('fresh',0),('resumed',0)},{(g['context'],g['reuse_percent']) for g in groups})
        self.assertTrue(all('median_change_vs_zero' not in g for g in groups))

    def test_providers_are_never_pooled_or_compared_across(self):
        from examples.summarize_reuse_ratios import complete,legacy_savings
        runs=[batch(0,'resumed',provider='codex',total=10),batch(0,'resumed',provider='claude',total=40)]
        groups=summarize_groups(runs,keep_batches=True)
        self.assertEqual({('codex',100),('claude',400)},
                         {(g['provider'],g['median']['total_input_tokens']) for g in groups})
        self.assertTrue(all('median_change_vs_zero' not in g for g in groups))
        self.assertIsNone(legacy_savings(groups))
        self.assertEqual({},legacy_savings(groups[:1]))
        self.assertFalse(complete(groups))

    def test_an_empty_or_failed_only_report_is_not_complete(self):
        from examples.summarize_reuse_ratios import complete
        self.assertEqual([],summarize_groups([]));self.assertFalse(complete([]))
        failed=[{'kind':'mixed-reuse','reuse_percent':p,'context':'fresh','provider':'codex','passed':False,'error':'X'}
                for p in (0,30,70,100)]
        groups=summarize_groups(failed)
        self.assertTrue(all(g['failure_rate']==1.0 for g in groups));self.assertFalse(complete(groups))

    def test_the_summary_cli_writes_an_incomplete_report_for_no_results(self):
        import json,subprocess,sys
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'report.json';report.write_text(json.dumps({'results':[]}))
            out=Path(folder)/'summary.json'
            done=subprocess.run([sys.executable,'-m','examples.summarize_reuse_ratios',str(report),'--output',str(out)],
                                capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(0,done.returncode,done.stderr)
            summary=json.loads(out.read_text())
            self.assertFalse(summary['complete']);self.assertEqual([],summary['groups'])
            self.assertIsNone(summary['savings_against_zero'])


    def test_the_summary_cli_keeps_a_passed_run_with_unknown_usage(self):
        """The original failure: strict summarizing aborted the whole CLI."""
        import json,subprocess,sys
        run=batch(0,'resumed');run['rows'][0]['usage']={'status':'missing_or_ambiguous'}
        run['usage']=sum_usage(run['rows'])
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'report.json';report.write_text(json.dumps({'results':[run]}))
            out=Path(folder)/'summary.json'
            done=subprocess.run([sys.executable,'-m','examples.summarize_reuse_ratios',str(report),'--output',str(out)],
                                capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(0,done.returncode,done.stderr)
            group,=json.loads(out.read_text())['groups']
            self.assertEqual(1,group['passed_runs'])
            self.assertIsNone(group['median']['total_input_tokens'])
            self.assertEqual(20,group['median']['seconds'])

class CommandLineTests(unittest.TestCase):
    def test_a_missing_agent_cli_is_refused_before_any_build(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(Path,'home',lambda:Path(home)), \
             mock.patch.object(repeat_benchmark,'build',side_effect=AssertionError('built')), \
             mock.patch('sys.argv',['repeat_benchmark','--output',str(Path(folder)/'out'),'--arms','claude-tacto-fresh0']), \
             mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit) as stop:repeat_benchmark.main()
            self.assertEqual(2,stop.exception.code)
            self.assertFalse((Path(folder)/'out').exists())

    def test_an_unknown_arm_is_a_usage_error(self):
        with mock.patch('sys.argv',['repeat_benchmark','--output','/nonexistent/x','--arms','codex-tacto-fresh5']), \
             mock.patch('sys.stderr'):
            with self.assertRaises(SystemExit) as stop:repeat_benchmark.main()
            self.assertEqual(2,stop.exception.code)


class MatchedRepetitionTests(unittest.TestCase):
    def test_failure_rate_and_medians_use_passed_runs(self):
        from examples.benchmark_matched_conditions import aggregate
        def row(arm,seconds,passed=True,total=10):
            return {'arm':arm,'exit':0,'seconds':seconds,'oracle':{'passed':passed},
                    'usage':{'total_input_tokens':total,'new_input_tokens':2,'cache_read_tokens':total-2,'output_tokens':1}}
        rows=[row('tacto',4),row('tacto',8),row('tacto',99,passed=False),row('sky',5),row('sky',7)]
        tacto=aggregate(rows)['tacto']
        self.assertAlmostEqual(0.3333,tacto['failure_rate']);self.assertEqual(6,tacto['median_seconds_passed'])
        self.assertEqual(10,tacto['median_total_input_tokens_passed'])
        rows[3]['usage']={}
        sky=aggregate(rows)['sky']
        self.assertEqual(0.0,sky['failure_rate']);self.assertIsNone(sky['median_total_input_tokens_passed'])
    def test_an_arm_with_no_rows_is_unknown_not_an_error(self):
        from examples.benchmark_matched_conditions import aggregate
        sky=aggregate([])['sky']
        self.assertEqual(0,sky['runs']);self.assertIsNone(sky['failure_rate'])
        self.assertIsNone(sky['mean_seconds']);self.assertIsNone(sky['total_input_tokens'])


def paired(agent_seconds=20, flow_seconds=2, repair=True, agent_fail=None):
    rows=[]
    ok={'passed':True,'single_commit':True,'active':False}
    for n in range(1,11):
        agent={'seconds':agent_seconds,'exit':0,'mode':'agent','job':{'job':f'T{n:02d}'},'trace_directory':f'/a/{n}',
               'usage':usage(10),'oracle':dict(ok,passed=n!=agent_fail)}
        flow={'seconds':flow_seconds,'exit':0,'mode':'flow','job':{'job':f'T{n:02d}'},'oracle':dict(ok)}
        if n==7 and repair:flow['recovery']={'seconds':5,'exit':0}
        rows.append({'agent':agent,'flow':flow})
    setup={'seconds':60,'exit':0,'trace_directory':'/seed','usage':usage(100)}
    fix={'seconds':25,'exit':0,'trace_directory':'/repair','usage':usage(40)}
    agents=[p['agent'] for p in rows]+[setup]+([fix] if repair else [])
    return {'arm':'codex-tacto-paired','kind':'paired-jobs','context':'fresh','provider':'codex','rep':1,
            'passed':True,'pairs':rows,'setup':setup,'agent_runs':agents}


class PairedJobsTests(unittest.TestCase):
    def test_parse_arm(self):
        self.assertEqual(('codex','tacto','paired',None,True),parse_arm('codex-tacto-paired'))
        with self.assertRaises(ValueError):parse_arm('codex-sky-paired')

    def test_every_ratio_is_composed_from_one_run_per_mode(self):
        from examples.summarize_reuse_ratios import compose_paired
        r=paired()
        zero,thirty,seventy,hundred=(compose_paired(r,p) for p in (0,30,70,100))
        self.assertEqual((200,10,0),(zero['seconds'],zero['agent_jobs'],zero['flow_jobs']))
        self.assertEqual(100,zero['total_input_tokens'])
        # 70%: three agents, seven replays (job 7 with its recovery), seed and repair once.
        self.assertEqual(3*20+7*2+5+60+25,seventy['seconds'])
        self.assertEqual(3*10+100+40,seventy['total_input_tokens'])
        self.assertEqual(10*2+5+60+25,hundred['seconds'])
        # 30%: jobs 3,6,9 replay; 9 is the first after the change and takes job 7's attempt.
        self.assertEqual(7*20+2+2+(2+5)+60+25,thirty['seconds'])
        self.assertTrue(thirty['approximated_first_changed_replay'])
        self.assertFalse(seventy['approximated_first_changed_replay'])

    def test_a_failed_row_fails_only_the_ratios_that_use_it(self):
        from examples.summarize_reuse_ratios import summarize_groups
        groups={g['reuse_percent']:g for g in summarize_groups([paired(agent_fail=2)])}
        self.assertEqual(1.0,groups[0]['failure_rate'])     # job 2 runs as agent at 0%
        self.assertEqual(1.0,groups[30]['failure_rate'])    # and at 30% (2 is not replayed)
        self.assertEqual(0.0,groups[70]['failure_rate'])    # 2 is replayed at 70%
        self.assertEqual(0.0,groups[100]['failure_rate'])
        self.assertEqual('paired',groups[70]['context'])
        self.assertIn('median_change_vs_zero',groups[70])

    def test_a_missing_repair_or_seed_is_not_free(self):
        from examples.summarize_reuse_ratios import compose_paired
        with self.assertRaises(ValueError):compose_paired(paired(repair=False),70)
        r=paired();r['setup']=None
        with self.assertRaises(ValueError):compose_paired(r,30)
        self.assertEqual(200,compose_paired(r,0)['seconds'])   # 0% needs no seed

    def test_paired_run_measures_each_job_both_ways_with_one_seed(self):
        calls=[]
        def agent(provider,text,trace,**kwargs):
            calls.append(('agent',str(trace).rsplit('/',1)[-1],kwargs))
            return {'exit':0,'seconds':1,'session_id':'s','usage':usage(),'trace_directory':str(trace)}
        def flow_job(base,fixture,job,n,recipe,template,provider,agents):
            calls.append(('flow',n,{}))
            return {'seconds':1,'exit':0,'mode':'flow','job':job,'oracle':{'passed':True}},recipe
        def author(base,fixture,target,provider,template,agents):
            calls.append(('seed',None,{}));return {'seconds':1,'exit':0},object()
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(repeat_benchmark,'Fixture',FakeFixture), \
             mock.patch.object(repeat_benchmark,'run_agent',agent), \
             mock.patch.object(repeat_benchmark,'_flow_job',flow_job), \
             mock.patch.object(repeat_benchmark,'_author_seed',author), mock.patch('builtins.print'):
            result=repeat_benchmark.paired_jobs(Path(folder),None,'t','codex')
        self.assertEqual(1,sum(c[0]=='seed' for c in calls))
        self.assertEqual(10,sum(c[0]=='agent' for c in calls));self.assertEqual(10,sum(c[0]=='flow' for c in calls))
        self.assertTrue(all(c[2]=={} for c in calls if c[0]=='agent'))   # fresh: never resumed
        order=[c[0] for c in calls if c[0]!='seed']
        self.assertEqual(['agent','flow','flow','agent'],order[:4])     # alternates per job
        self.assertEqual(('paired-jobs',10),(result['kind'],len(result['pairs'])))


class StatefulFixture(repeat_benchmark.Fixture):
    """The real oracle over a commit list that lives as long as the run."""
    def __init__(self,base,app):
        self.seq=0;self.current={'sequence':0,'job':'','phase':'entry','commits':[],'active':False}
        StatefulFixture.last=self
    def state(self):
        return json.loads(json.dumps(self.current))
    def setup(self,job,changed=False):
        self.seq+=1;self.current.update(sequence=self.seq,job=job['job'],phase='entry')
        return self.state()
    def commit(self,job):
        self.current['commits'].append({'job':self.current['job'],'amount':job['amount'],'factor':job['factor'],
                                        'bonus':job['bonus'],'result':int(job['result'])})
        self.current['phase']='done'
    def close(self):pass


class PairedStatefulTests(unittest.TestCase):
    def test_both_modes_of_a_job_each_hold_exactly_one_commit(self):
        import json as _json
        def agent(provider,text,trace,**kwargs):
            fixture=StatefulFixture.last
            job=next(j for j in repeat_benchmark.jobs() if j['job']==fixture.current['job'].split('-')[0])
            fixture.commit(job)
            return {'exit':0,'seconds':1,'session_id':'s','usage':usage(),'trace_directory':str(trace)}
        def flow_job(base,fixture,job,n,recipe,template,provider,agents):
            fixture.commit(job)
            return {'seconds':1,'exit':0,'mode':'flow','job':job,'oracle':fixture.oracle(job)},recipe
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(repeat_benchmark,'Fixture',StatefulFixture), \
             mock.patch.object(repeat_benchmark,'run_agent',agent), \
             mock.patch.object(repeat_benchmark,'_flow_job',flow_job), \
             mock.patch.object(repeat_benchmark,'_author_seed',lambda *a:({'seconds':1,'exit':0},object())), \
             mock.patch('builtins.print'):
            result=repeat_benchmark.paired_jobs(Path(folder),None,'t','codex')
        rows=[r for p in result['pairs'] for r in p.values()]
        self.assertTrue(all(r['oracle']['single_commit'] and r['oracle']['passed'] for r in rows))
        self.assertEqual((20,True),(result['verified_rows'],result['passed']))
        self.assertEqual({'T01'},{r['logical_job'] for r in result['pairs'][0].values()})
        self.assertEqual({'T01-A','T01-F'},{r['job']['job'] for r in result['pairs'][0].values()})
        self.assertEqual(20,len(StatefulFixture.last.current['commits']))   # evidence never cleared

    def test_the_selected_rows_own_failure_is_not_hidden_by_borrowed_timing(self):
        from examples.summarize_reuse_ratios import compose_paired
        for change in ({'passed':False},{'single_commit':False}):
            with self.subTest(change=change):
                r=paired();r['pairs'][8]['flow']['oracle'].update(change)
                with self.assertRaises(ValueError):compose_paired(r,30)
        r=paired();r['pairs'][8]['flow']['exit']=1
        with self.assertRaises(ValueError):compose_paired(r,30)

    def test_one_passing_paired_repetition_summarizes_from_the_command_line(self):
        import json as _json,subprocess,sys
        with tempfile.TemporaryDirectory() as folder:
            report=Path(folder)/'report.json';report.write_text(_json.dumps({'results':[paired()]}))
            out=Path(folder)/'summary.json'
            done=subprocess.run([sys.executable,'-m','examples.summarize_reuse_ratios',str(report),'--output',str(out)],
                                capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(0,done.returncode,done.stderr)
            summary=_json.loads(out.read_text())
        self.assertIsNone(summary['savings_against_zero'])        # composed rows are not separate runs
        self.assertTrue(summary['complete'])
        self.assertTrue(any('correlated' in limit for limit in summary['limits']))
        seventy=next(g for g in summary['groups'] if g['reuse_percent']==70)
        self.assertEqual('paired',seventy['context'])
        self.assertIsNotNone(seventy['median']['total_input_tokens'])


if __name__=='__main__':unittest.main()
