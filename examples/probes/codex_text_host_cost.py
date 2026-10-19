"""Pilot the text-only CLI host flag without changing production or user config."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def run(output):
    import tacto.codex_result as provider
    from tacto.provider_observation import summarize_provider_calls
    sources=[ROOT/'tacto/codex_result.py',ROOT/'tacto/provider_io.py',Path(__file__).resolve()]
    def manifest():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'scope':'two CLI-only tiny-text pilot calls; not UI, Sky or statistical performance',
            'prompt_sha256':hashlib.sha256(b'Return exactly this JSON: {"answer":"ok"}').hexdigest(),
            'rows':[], 'host_ui_actions_executed':0,'provider_side_effects_verified':False}
    before=None
    try:
        before=manifest()
        base=provider._TEXT_ONLY_DISABLED
        for name,features in [('baseline',base),('disable_code_host',(*base,'code_mode_host'))]:
            with patch.object(provider,'_TEXT_ONLY_DISABLED',features):
                result=provider.codex_result('Return exactly this JSON: {"answer":"ok"}',timeout=60)
            try:exact=json.loads(result.text)=={'answer':'ok'}
            except (TypeError,ValueError):exact=False
            metadata=result.to_dict();metadata.pop('text',None)
            report['rows'].append({'stage':name,'exact_answer':exact,'metadata':metadata,
                'usage':summarize_provider_calls([metadata])})
    except Exception as error:report['error_class']=type(error).__name__
    finally:
        try:after=manifest()
        except Exception:after=None
        report.update(source_before=before,source_after=after,
                      source_subset_stable=before is not None and before==after)
        with os.fdopen(fd,'w') as stream:json.dump(report,stream,indent=2)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=run(args.output)
    print(json.dumps({'source_subset_stable':report['source_subset_stable'],'rows':[
        {'stage':r['stage'],'exact_answer':r['exact_answer'],
         'status':r['metadata']['status'],'elapsed_seconds':r['metadata']['elapsed_seconds'],
         'total_input_tokens':r['metadata']['total_input_tokens'],
         'output_tokens':r['metadata']['output_tokens']} for r in report['rows']]}))
