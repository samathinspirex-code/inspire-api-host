"""Bounded per-process protection; use a shared proxy limit for multiple workers."""
import hashlib
import time
from collections import OrderedDict

from app.core.errors import APIError

_windows=OrderedDict()


def check(request,bucket,limit):
    now=int(time.monotonic()//60)
    address=request.client.host if request.client else 'unknown'
    key=hashlib.sha256((bucket+':'+address).encode()).digest()
    window,count=_windows.pop(key,(now,0))
    count=count+1 if window==now else 1
    _windows[key]=(now,count)
    while len(_windows)>10000: _windows.popitem(last=False)
    if count>limit: raise APIError(429,'PAYMENTS_RATE_LIMIT','Too many requests. Please wait a minute and try again')
