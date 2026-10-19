"""Read an already-created owned RTFD witness; never open or manipulate an app."""
import hashlib
import json
from pathlib import Path

from examples.probes.attachment_payloads import owned_body
from examples.probes.signed_popup_flow_replay import reserve_report
from tacto.document_packages import capture_rtfd_package
from tacto.document_attachments import compare_attachment_payloads


def main(source, output):
    root = Path(__file__).resolve().parents[2]
    names = ['tacto/document_packages.py', 'tacto/document_attachments.py',
             'examples/probes/rtfd_package_payloads.py',
             'examples/probes/attachment_payloads.py', 'tests/test_document_packages.py']
    hashes = lambda: {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    with reserve_report(output) as stream:
        report = {'passed':False, 'scope':'captured private persisted RTFD payloads',
                  'gui_input_requests':0, 'model_requests':0,
                  'ordinary_app_export_verified':False, 'gui_save_causality_verified':False,
                  'comparison_eligible':False, 'whole_goal_complete':False,
                  'native_allocation_hard_bound_guaranteed':False, 'soft_deadline_only':True}
        try:
            report['source_before'] = hashes()
            captured = capture_rtfd_package(source)
            report.update(source_package=str(source), entry_count=captured.entry_count, captured_bytes=captured.captured_bytes,
                          package_sampled_stable=captured.sampled_stable,
                          proves_no_intermediate_change=captured.proves_no_intermediate_change)
            report['attachment_payload_comparison'] = compare_attachment_payloads(owned_body(), captured.body)
            report['passed'] = report['attachment_payload_comparison']['captured_attachment_payloads_equal']
        except Exception as error:
            report['error_class'] = type(error).__name__
        finally:
            try:
                report['source_after'] = hashes()
                report['source_stable'] = report['source_before'] == report['source_after']
            except Exception as error:
                report['source_stable'] = False
                report['source_hash_error_class'] = type(error).__name__
            if not report['source_stable']:
                report['passed'] = False
            json.dump(report, stream, indent=2)
            stream.write('\n')
        return report
