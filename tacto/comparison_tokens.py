"""Observed cold-run token accounting, without a price or billing claim."""

def reported_turn_tokens(events, *, stream_complete):
    result = {'scope':'single reported turn.completed usage', 'status':'unavailable',
              'input_tokens':None,'cached_input_tokens':None,'uncached_input_tokens':None,
              'output_tokens':None,'total_tokens':None,'event_count':len(events),
              'stream_complete':bool(stream_complete),'cost_usd':None,'billing_verified':False}
    if len(events) != 1:
        result['status'] = 'missing' if not events else 'ambiguous_multiple_turns'
        return result
    usage = events[0]
    if type(usage) is not dict:
        result['status'] = 'invalid'
        return result
    for key in ('input_tokens','output_tokens'):
        if type(usage.get(key)) is not int or usage[key] < 0:
            result['status'] = 'invalid'
            return result
    cached = usage.get('cached_input_tokens')
    if cached is not None and (type(cached) is not int or not 0 <= cached <= usage['input_tokens']):
        result['status'] = 'invalid'
        return result
    result.update(status='reported' if stream_complete else 'partial_reported',
                  input_tokens=usage['input_tokens'],output_tokens=usage['output_tokens'],
                  cached_input_tokens=cached,uncached_input_tokens=None if cached is None else usage['input_tokens']-cached,
                  total_tokens=usage['input_tokens']+usage['output_tokens'])
    return result
