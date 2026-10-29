"""Host-selected live window identity; never persisted inside a Flow."""
def take_window_arg(args):
    args=list(args)
    if '--window-id' not in args:return args,None
    at=args.index('--window-id')
    if (args.count('--window-id')!=1 or at+1>=len(args) or not args[at+1].isascii()
            or not args[at+1].isdigit() or not 0<int(args[at+1])<=0xffffffff
            or '--document' in args):
        raise ValueError('--window-id needs one current positive window ID; choose it from observe --window-identity --json')
    value=int(args[at+1]);del args[at:at+2]
    return args,value


def bind_window(driver,target,window_id):
    bind=getattr(driver,'scope_window',None)
    if bind is None:return {'type':'error','error':'window_scope_unavailable','effect':'not_started'}
    result=bind(target,window_id)
    return None if result.get('type')=='window_scope' else result
