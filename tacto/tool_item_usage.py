"""Count observed CLI tool items once; these are not attested native RPCs."""
from collections import Counter


class ToolItemUsage:
    def __init__(self, max_items=10000):
        self.max_items=max_items
        self.items={}
        self.unidentified_events=0
        self.limit_reached=False
        self.unparsed_records=0

    def note_unparsed(self):
        self.unparsed_records+=1

    def record(self,event,key):
        if event.get('type') not in ('item.started','item.updated','item.completed'):
            return
        item=event.get('item')
        if not isinstance(item,dict):return
        identity=item.get('id')
        if (not isinstance(identity,str) or not identity.strip() or len(identity)>256
                or not isinstance(key,str) or not key or len(key)>256):
            self.unidentified_events+=1
            return
        if identity not in self.items:
            if len(self.items)>=self.max_items:
                self.limit_reached=True
                return
            self.items[identity]={'key':key,'completed':False,'ambiguous':False}
        held=self.items[identity]
        if held['key']!=key:held['ambiguous']=True
        if event['type']=='item.completed':held['completed']=True

    def snapshot(self,*,stream_complete):
        observed=Counter();completed=Counter();incomplete=Counter();ambiguous=0
        for held in self.items.values():
            if held['ambiguous']:
                ambiguous+=1
                continue
            observed[held['key']]+=1
            (completed if held['completed'] else incomplete)[held['key']]+=1
        return {'schema':'codex-tool-item-lifecycle-v1','observed_unique_items':dict(observed),
                'completed_unique_items':dict(completed),'incomplete_unique_items':dict(incomplete),
                'ambiguous_ids':ambiguous,'unidentified_events':self.unidentified_events,
                'item_limit_reached':self.limit_reached,'unparsed_records':self.unparsed_records,'stream_complete':bool(stream_complete),
                'complete_unique_counts':bool(stream_complete) and not (ambiguous or self.unidentified_events or self.limit_reached or self.unparsed_records or incomplete),
                'scope':'observed_cli_items_not_attested_rpc_or_model_usage'}
