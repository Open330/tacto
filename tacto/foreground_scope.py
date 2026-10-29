"""Keep explicit native presentation inside the helper's app/input ownership."""
from contextlib import contextmanager


@contextmanager
def foreground_scope(driver,target):
    if driver is None or 'session.foreground_lease' not in getattr(driver,'capabilities',()):
        yield None
        return
    acquired=driver.foreground_begin(target)
    if acquired.get('type')!='foreground_lease' or acquired.get('acquired') is not True:
        yield acquired
        return
    try:
        yield None
    finally:
        driver.foreground_end(target)


def native_driver(driver,backend):
    if driver is not None or backend is not None:return driver
    import os
    if 'TACTO_HELPER_FD' not in os.environ:return None
    from .helper import InheritedHelperDriver
    return InheritedHelperDriver()
