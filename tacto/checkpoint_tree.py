"""Compare AX meaning while ignoring only bare, unaddressable Group wrappers."""
import re
from collections import Counter

_NODE=re.compile(r'^(\s*)(\d+)\s+(\S.*)$')


def snapshot(tree):
    rows=[];parents=[];index_to_row={};row_to_index={};wrappers=[]
    for line in tree.splitlines():
        match=_NODE.match(line)
        if match is None:
            rows.append(('text',line));continue
        indent=len(match[1]);index=int(match[2]);body=match[3]
        if index in index_to_row:raise ValueError('duplicate AX index')
        while parents and parents[-1][0]>=indent:parents.pop()
        path=parents[-1][1] if parents else ()
        if body=='Group':
            # No identity, value, description, state, flag or subrole is present.
            # Named descendants retain the same effective ancestor depth.
            wrappers.append((len(rows),path))
            parents.append((indent,path));index_to_row[index]=None;continue
        ordinal=len(rows);rows.append(('node',path,body))
        index_to_row[index]=ordinal;row_to_index[ordinal]=index
        parents.append((indent,path+(body,)))
    return tuple(rows),index_to_row,row_to_index,tuple(wrappers)


def equivalent(before,after):
    a=snapshot(before);b=snapshot(after)
    delta=sum((Counter(a[3])-Counter(b[3])).values())+sum((Counter(b[3])-Counter(a[3])).values())
    return a[0]==b[0] and delta<=2


def corresponding_index(before,after,index):
    old,positions,_,_=snapshot(before);new,_,indices,_=snapshot(after)
    if not equivalent(before,after) or positions.get(index) is None:raise ValueError('node identity unavailable')
    return indices[positions[index]]
