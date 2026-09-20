"""Snapshot-bound, lossless text slices measured with the entire prompt."""
from dataclasses import dataclass
import hashlib
import json
from typing import Callable
from .candidate_paging import utf8_token_upper_bound


@dataclass(frozen=True)
class TextPage:
    ref: str
    start: int
    end: int
    total: int
    text: str
    prompt: str
    token_count: int
    overflow: bool


def text_ref(text: str, context: str = '') -> str:
    if not isinstance(text,str) or not isinstance(context,str):
        raise TypeError('text and context must be strings')
    return 'ctx_'+hashlib.sha256(json.dumps([context,text],ensure_ascii=False).encode('utf-8')).hexdigest()


def page_text(text: str, *, ref: str, context: str = '', offset: int = 0,
              budget: int, length: int | None = None, render: Callable[[str,int,int,int],str], token_counter=None) -> TextPage:
    """Return an intact code-point slice, with no implicit source truncation.

    Offsets allow seeking as well as sequential reading. The reference must
    match this complete text and snapshot context. The counter need not be
    additive: only a fully measured fitting prompt is ever marked nonoverflow.
    Pages need not be maximal with a nonmonotonic custom counter.
    """
    if ref != text_ref(text,context):raise ValueError('stale text reference')
    if type(offset) is not int or not 0 <= offset <= len(text):raise ValueError('invalid text offset')
    if type(budget) is not int or budget<=0:raise ValueError('invalid text budget')
    if length is not None and (type(length) is not int or length <= 0):raise ValueError("invalid text length")
    end_limit = len(text) if length is None else min(len(text), offset + length)
    counter=token_counter or utf8_token_upper_bound
    def measure(end):
        prompt=render(text[offset:end],offset,end,len(text))
        if not isinstance(prompt,str):raise TypeError('renderer must return text')
        tokens=counter(prompt)
        if type(tokens) is not int or tokens<0:raise ValueError('invalid token count')
        return prompt,tokens
    first=min(offset+1,end_limit)
    prompt,tokens=measure(first)
    if tokens>budget:return TextPage(ref,offset,first,len(text),text[offset:first],prompt,tokens,True)
    best=first
    low,high=first+1,end_limit
    while low<=high:
        end=(low+high)//2
        trial,count=measure(end)
        if count<=budget:
            best,prompt,tokens=end,trial,count;low=end+1
        else:high=end-1
    return TextPage(ref,offset,best,len(text),text[offset:best],prompt,tokens,False)
