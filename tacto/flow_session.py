"""One signed connection: observe, accept one bounded flow, replay, exit."""
import json
import os
import select
import sys
import time
from contextlib import contextmanager

from .flow import Flow, every_step


MAX_FLOW_BYTES = 1024 * 1024


@contextmanager
def flow_input_mode(fd):
    original = None
    # Windows consoles have no termios; their line mode already delivers
    # one whole line, which is all a session reads.
    if os.isatty(fd) and sys.platform != "win32":
        import termios
        original = termios.tcgetattr(fd)
        settings = list(original)
        settings[6] = list(original[6])
        settings[3] &= ~(termios.ICANON | termios.ECHO)
        settings[6][termios.VMIN] = 1
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, settings)
    try:
        yield
    finally:
        if original is not None:
            termios.tcsetattr(fd, termios.TCSANOW, original)


def _windows_reader(fd, *, depth=2):
    """select() takes only sockets on Windows, so one thread reads the pipe.

    The queue holds at most `depth` chunks: while a long batch job runs, the
    thread waits instead of reading the rest of stdin into memory.
    """
    import queue
    import threading
    chunks = queue.Queue(maxsize=depth)

    def pump():
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b''
            chunks.put(chunk)
            if not chunk:
                return
    threading.Thread(target=pump, daemon=True).start()

    def read(remaining):
        try:
            return chunks.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError('session_idle_timeout') from None
    read.queue = chunks   # for tests: bounded backlog
    return read


def read_flow_line(fd, timeout, *, platform=None):
    deadline = time.monotonic() + timeout
    data = bytearray()
    windows = (platform or sys.platform) == "win32"
    reader = _windows_reader(fd) if windows else None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('session_idle_timeout')
        if windows:
            chunk = reader(remaining)
            if len(data) + len(chunk) > MAX_FLOW_BYTES + 1:
                chunk = chunk[:MAX_FLOW_BYTES + 1 - len(data)]
        elif not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError('session_idle_timeout')
        else:
            chunk = os.read(fd, min(65536, MAX_FLOW_BYTES + 1 - len(data)))
        if not chunk:
            raise ValueError('session_input_closed')
        data.extend(chunk)
        if len(data) > MAX_FLOW_BYTES:
            raise ValueError('session_flow_over_budget')
        if b'\n' in data:
            line, tail = data.split(b'\n', 1)
            if tail.strip():
                raise ValueError('session_accepts_one_flow')
            return line.decode('utf-8')


def run_session(args, *, make_driver, run_flow, stdin, emit):
    args = list(args)
    # Windows has no signed helper: the session drives UI Automation in this
    # process, so --helper is refused there and required on macOS.
    windows = sys.platform == "win32"
    helper = [] if windows else ['--helper']
    usage = ('usage: session <app.exe | pid:N> [--guide] [--timings] [--idle-timeout 1..600]' if windows
             else 'usage: session <bundle-id> --helper [--guide] [--timings] [--idle-timeout 1..600]')
    timeout = 90
    save_path = None
    if '--save' in args:
        index = args.index('--save')
        if args.count('--save') != 1 or index+1 >= len(args) or args[index+1].startswith('-'):
            emit('session_save: --save needs one absolute new path');return 2
        save_path = args[index+1];del args[index:index+2]
        if not os.path.isabs(save_path) or os.path.lexists(save_path) or not os.path.isdir(os.path.dirname(save_path)):
            emit('session_save: use an absolute new path in an existing directory');return 2
    if '--idle-timeout' in args:
        index = args.index('--idle-timeout')
        if args.count('--idle-timeout') != 1 or index + 1 >= len(args):
            emit(usage)
            return 2
        value = args[index + 1]
        if not value.isdigit() or not 1 <= int(value) <= 600:
            emit('session_invalid_idle_timeout')
            return 2
        timeout = int(value)
        del args[index:index + 2]
    targets = [value for value in args if value not in ('--helper', '--timings', '--guide', '--reuse-guide')]
    if (len(targets) != 1 or targets[0].startswith('-') or args.count('--helper') != len(helper)
            or args.count('--timings') > 1 or args.count('--guide') > 1 or args.count('--reuse-guide') > 1):
        emit(usage)
        return 2
    target = targets[0]
    driver = None
    started = time.monotonic()
    timing = {}
    try:
        driver = make_driver(list(helper))
        observation = driver.observe(target, 'tree_full')
        timing['observation_ready_seconds'] = time.monotonic() - started
        if observation.get('type') != 'observation':
            emit(json.dumps(observation, ensure_ascii=False))
            return 1
        try:
            # Avoid terminal canonical-line limits and echoing document text.
            # Restore this invocation's terminal flags even on cancellation.
            with flow_input_mode(stdin.fileno()):
                if '--guide' in args or '--reuse-guide' in args:
                    from .command_help import session_guide
                    emit(session_guide(target, reusable='--reuse-guide' in args))
                emit(json.dumps(observation, ensure_ascii=False))
                emit('TACTO_SESSION_READY send one compact Flow JSON line, then newline; target=' + target)
                timing['input_ready_seconds'] = time.monotonic() - started
                flow = Flow.from_json(read_flow_line(stdin.fileno(), timeout), strict_checks=True)
                timing['flow_received_seconds'] = time.monotonic() - started
        except (ValueError, TypeError, KeyError, TimeoutError, RecursionError) as exc:
            # A flow may contain personal text. Do not echo malformed input or
            # decoder excerpts; invalid input ends this session before acting.
            from .flow_errors import feedback
            emit('session_invalid_flow: ' + feedback(exc))
            return 2
        if flow.target != target:
            emit('session_target_mismatch')
            return 2
        if any(step.action in ('decide', 'visual') for step in every_step(flow.steps)):
            emit('session_requires_deterministic_flow; use run for model judgment')
            return 2
        template = flow.to_json(compact=True)
        if flow.params or flow.tables or flow.derived:
            try:
                flow = flow.bind()
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                emit('session_invalid_parameters: provide params defaults for every {{name}}; session accepts no -p flags')
                return 2
        if flow.target != target:
            emit('session_target_mismatch')
            return 2
        # A session is one operation, not a daemon or a source of reusable
        # observation IDs. replay reobserves on this same authenticated channel.
        flags = [*helper, '--think', '0']
        if '--timings' in args:
            flags.append('--timings')
        result = run_flow(flow, flags, _driver=driver, _confirm=lambda description: False)
        timing['replay_done_seconds'] = time.monotonic() - started
        if result == 0 and save_path is not None:
            from .storage import write_private
            try:
                write_private(save_path, template, overwrite=False)
            except OSError:
                emit('session_save_failed: UI task completed; file not saved. Do not replay the task.');return 1
            emit('TACTO_FLOW_SAVED ' + save_path)
        return result
    finally:
        if driver is not None:
            driver.close()
        if '--timings' in args:
            timing['session_done_seconds'] = time.monotonic() - started
            emit('TACTO_SESSION_TIMINGS ' + json.dumps(timing, sort_keys=True))
