"""The Windows session reader: one bounded line, without select() on a pipe."""
import os
import threading
import time
import unittest

from tacto.flow_session import MAX_FLOW_BYTES, read_flow_line


class WindowsSessionInputTests(unittest.TestCase):
    def pipe(self, *chunks, close=False, delay=0.0):
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        def feed():
            for chunk in chunks:
                time.sleep(delay)
                os.write(write, chunk)
            if close:
                os.close(write)
        thread = threading.Thread(target=feed, daemon=True)
        thread.start()
        if not close:
            self.addCleanup(os.close, write)
        return read

    def test_reads_one_line_split_across_writes_with_crlf(self):
        fd = self.pipe(b'{"name":', b'"x"}\r\n', delay=0.01)
        self.assertEqual('{"name":"x"}\r', read_flow_line(fd, 2, platform="win32"))

    def test_idle_closed_oversized_and_second_flow_are_refused(self):
        with self.assertRaisesRegex(TimeoutError, "session_idle_timeout"):
            read_flow_line(self.pipe(), 0.2, platform="win32")
        with self.assertRaisesRegex(ValueError, "session_input_closed"):
            read_flow_line(self.pipe(b"partial", close=True), 2, platform="win32")
        with self.assertRaisesRegex(ValueError, "session_flow_over_budget"):
            read_flow_line(self.pipe(b"x" * (MAX_FLOW_BYTES + 10)), 5, platform="win32")
        with self.assertRaisesRegex(ValueError, "session_accepts_one_flow"):
            read_flow_line(self.pipe(b"{}\n{}\n"), 2, platform="win32")


class BoundedReaderTests(unittest.TestCase):
    def test_the_reader_does_not_run_ahead_of_its_consumer(self):
        from tacto.flow_session import _windows_reader
        read_fd, write_fd = os.pipe()
        total = 32 * 65536
        written = []

        def produce():
            try:
                for _ in range(32):
                    os.write(write_fd, b"x" * 65536); written.append(1)
            finally:
                os.close(write_fd)   # EOF once everything is written
        writer = threading.Thread(target=produce, daemon=True); writer.start()
        read = _windows_reader(read_fd)
        time.sleep(0.3)
        # At most two chunks wait; the rest of the 2 MiB stays unread.
        self.assertLessEqual(read.queue.qsize(), 2)
        self.assertLess(len(written), 32)
        received = 0
        while True:   # drain to the real EOF and count every byte
            chunk = read(5)
            if not chunk:
                break
            received += len(chunk)
        writer.join(5)
        self.assertEqual(total, received)
        os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
