"""Read-only metadata experiment; never a readiness result or input permit.

Compare AXChildren value vs count/range on a bounded sample of actual Finder
nodes. Keep UI values and element handles out of the report. This uses a direct
AX metadata reader, not the signed native-input backend.
"""
import hashlib
import json
import os
from pathlib import Path
import time

ROLES = frozenset({'AXWindow', 'AXScrollArea', 'AXGroup', 'AXSplitGroup',
                   'AXBrowser', 'AXColumn', 'AXRow', 'AXCell', 'AXImage',
                   'AXButton', 'AXStaticText', 'AXToolbar', 'AXTextField'})


def summarize(rows):
    """Only successful, identity-matching arrays qualify for timing comparison."""
    import statistics
    result = {}
    for role in sorted({row['role'] for row in rows}):
        selected = [row for row in rows if row['role'] == role]
        comparable = [row for row in selected if row['same_ordered_children'] is True]
        result[role] = {'rows': len(selected), 'comparable_rows': len(comparable),
            'value_median_us': statistics.median(r['value_us'] for r in comparable) if comparable else None,
            'range_median_us': statistics.median(r['range_us'] for r in comparable) if comparable else None}
    return result


def summarize_attributes(rows):
    import statistics
    result = {}
    for role in sorted({row['role'] for row in rows}):
        selected = [row for row in rows if row['role'] == role]
        comparable = [row for row in selected if row['same_attribute_replies'] is True]
        result[role] = {'rows':len(selected), 'comparable_rows':len(comparable),
            'batch_median_us':statistics.median(r['batch_us'] for r in comparable) if comparable else None,
            'names_batch_median_us':statistics.median(r['names_batch_us'] for r in comparable) if comparable else None}
    return result


def main(output):
    # Reserve a new private report before accessing the application.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {'read_only': True, 'authorizes_input': False, 'readiness_verified': False,
              'backend': 'direct_ax_metadata', 'rows': [], 'passed': False}
    sources = [Path(__file__), Path(__file__).resolve().parents[2] / 'tacto/process_cpu.py']
    hashes = lambda: {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    report['source_before'] = hashes()
    started = time.monotonic()
    try:
        import ApplicationServices as AX
        from AppKit import NSWorkspace
        from CoreFoundation import CFEqual, CFGetTypeID, CFArrayGetTypeID
        from tacto.process_cpu import sample_process_cpu, cpu_delta_ns, verify_cpu_clock
        report['cpu_calibration'] = verify_cpu_clock()
        apps = [a for a in NSWorkspace.sharedWorkspace().runningApplications()
                if a.bundleIdentifier() == 'com.apple.finder']
        if len(apps) != 1:
            raise RuntimeError('finder_identity_unavailable')
        pid = apps[0].processIdentifier()
        initial = sample_process_cpu(pid)
        deadline = time.monotonic() + 15
        def ready(node):
            current = sample_process_cpu(pid)
            if current.start_raw != initial.start_raw:
                raise RuntimeError('finder_identity_changed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('metadata_deadline')
            status, owner = AX.AXUIElementGetPid(node, None)
            if status != 0 or owner != pid:
                raise RuntimeError('element_owner_changed')
            if AX.AXUIElementSetMessagingTimeout(node, min(1., remaining)) != 0:
                raise RuntimeError('metadata_timeout_unavailable')
        def value(node, name):
            ready(node)
            answer = AX.AXUIElementCopyAttributeValue(node, name, None)
            ready(node)
            return answer
        def array(value):
            return value is not None and CFGetTypeID(value) == CFArrayGetTypeID()
        app = AX.AXUIElementCreateApplication(pid)
        status, windows = value(app, 'AXWindows')
        if status != 0 or not array(windows):
            raise RuntimeError('windows_unavailable')
        report['windows'] = len(windows)
        # This is bounded sample selection, explicitly NOT full modal checking.
        samples, counts, stack, visited = [], {}, [(w, 0) for w in reversed(windows)], 0
        while stack and visited < 512 and len(samples) < 24:
            node, depth = stack.pop(); visited += 1
            if depth > 64:
                continue
            status, role = value(node, 'AXRole')
            if status == 0 and role in ROLES and counts.get(role, 0) < 2:
                samples.append((node, role)); counts[role] = counts.get(role, 0) + 1
            status, children = value(node, 'AXChildren')
            if status == 0 and array(children):
                # A sampling ceiling must never be mistaken for readiness prune.
                stack.extend((c, depth + 1) for c in reversed(list(children)[:512]))
                stack = stack[-1024:]
        report['sample_selection'] = {'visited': visited, 'sampled': len(samples),
            'full_tree_verified': False, 'pending': len(stack)}
        cpu_before = sample_process_cpu(pid)
        measured = time.monotonic()
        for ordinal, (node, role) in enumerate(samples):
            for round_number in range(2):
                row = {'ordinal': ordinal, 'round': round_number, 'role': role,
                       'value_first': (ordinal + round_number) % 2 == 0}
                report['rows'].append(row)
                def full():
                    begin = time.monotonic_ns()
                    error, children = value(node, 'AXChildren')
                    row.update(value_us=(time.monotonic_ns()-begin)//1000,
                               value_error=int(error), value_count=len(children) if array(children) else None)
                    return children if error == 0 and array(children) else None
                def ranged():
                    begin = time.monotonic_ns(); ready(node)
                    error, count = AX.AXUIElementGetAttributeValueCount(node, 'AXChildren', None)
                    ready(node); row.update(count_error=int(error), range_count=None)
                    children = None
                    if error == 0 and type(count) is int and 0 <= count <= 4096:
                        children = []
                        row['range_errors'] = []
                        for offset in range(0, count, 64):
                            ready(node)
                            status, part = AX.AXUIElementCopyAttributeValues(node, 'AXChildren', offset, min(64,count-offset), None)
                            ready(node); row['range_errors'].append(int(status))
                            if status != 0 or not array(part) or len(part) != min(64,count-offset):
                                children = None; break
                            children.extend(part)
                        ready(node)
                        last_error, last_count = AX.AXUIElementGetAttributeValueCount(node, 'AXChildren', None)
                        ready(node);row.update(recheck_error=int(last_error), count_stable=last_error == 0 and last_count == count)
                        if not row['count_stable']:children = None
                    row['range_us'] = (time.monotonic_ns()-begin)//1000
                    row['range_count'] = len(children) if children is not None else None
                    return children
                if row['value_first']: left, right = full(), ranged()
                else: right, left = ranged(), full()
                row['same_ordered_children'] = (left is not None and right is not None
                    and len(left) == len(right) and all(CFEqual(a,b) for a,b in zip(left,right)))
                names = ['AXRole', 'AXModal', 'AXChildren']
                def packet(keys):
                    ready(node)
                    status, values = AX.AXUIElementCopyMultipleAttributeValues(node, keys, 0, None)
                    ready(node)
                    if status != 0 or not array(values) or len(values) != len(keys):
                        return int(status), None
                    decoded = {}
                    for key, item in zip(keys, values):
                        if CFGetTypeID(item) == AX.AXValueGetTypeID():
                            kind = AX.AXValueGetType(item)
                            if kind != AX.kAXValueAXErrorType:
                                return -1, None
                            ok, error = AX.AXValueGetValue(item, kind, None)
                            if not ok or error == 0:
                                return -1, None
                            decoded[key] = (int(error), None)
                        else:
                            decoded[key] = (0, item)
                    return 0, decoded
                def batched():
                    begin = time.monotonic_ns()
                    status, reply = packet(names)
                    row.update(batch_us=(time.monotonic_ns()-begin)//1000, batch_error=status)
                    return reply
                def named():
                    begin = time.monotonic_ns(); ready(node)
                    status, attributes = AX.AXUIElementCopyAttributeNames(node, None)
                    ready(node); row['names_error'] = int(status)
                    reply = None
                    if status == 0 and array(attributes) and all(isinstance(n, str) for n in attributes):
                        keys = [key for key in names if key in attributes]
                        row['supported'] = {key:key in keys for key in names}
                        if 'AXRole' in keys:
                            status, reply = packet(keys)
                            row['names_batch_error'] = status
                            if reply is not None:
                                reply.update({key:(-25205,None) for key in names if key not in keys})
                    row['names_batch_us'] = (time.monotonic_ns()-begin)//1000
                    return reply
                if row['value_first']: original, filtered = batched(), named()
                else: filtered, original = named(), batched()
                row['same_attribute_replies'] = (original is not None and filtered is not None
                    and all(original[key][0] == filtered[key][0] and (
                        original[key][1] is None and filtered[key][1] is None or
                        original[key][1] is not None and filtered[key][1] is not None
                        and CFEqual(original[key][1],filtered[key][1])) for key in names))
        report['measurement_wall_seconds'] = time.monotonic()-measured
        report['whole_finder_cpu_ns'] = cpu_delta_ns(cpu_before, sample_process_cpu(pid))
        report['summary'] = summarize(report['rows'])
        report['attributes_summary'] = summarize_attributes(report['rows'])
        report['passed'] = bool(samples)
    except Exception as error:
        # No raw exception may publish UI strings or handles.
        report['error_class'] = type(error).__name__
    finally:
        report['elapsed_seconds'] = time.monotonic()-started
        report['source_after'] = hashes()
        report['source_subset_stable'] = report['source_before'] == report['source_after']
        with os.fdopen(fd, 'w') as out:
            json.dump(report, out, indent=2)
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    result = main(parser.parse_args().output)
    print(json.dumps({k: result[k] for k in ('passed','elapsed_seconds','source_subset_stable')}))
