"""Exact private display contract; public routes keep their legacy projection."""
from fastapi import HTTPException, Request

DISPLAY_HEADER = 'X-Core-Display-Text'
DISPLAY_CONTRACT = 'raw-fields-v1'


def raw_display_fields(request: Request) -> bool:
    values = request.headers.getlist(DISPLAY_HEADER)
    if not values:
        return False
    if values != [DISPLAY_CONTRACT]:
        raise HTTPException(409, 'unsupported display text contract')
    return True
