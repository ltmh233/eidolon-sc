"""Explicit scope for legacy SC gameplay, without changing bodyless request protocols."""
from fastapi import HTTPException, Request


def require_sc(work_id: object) -> None:
    """Only the exact SC identifier is valid; omission is handled by the caller."""
    if work_id != "sc":
        raise HTTPException(409, "legacy API supports sc only")


def sc_request(request: Request) -> None:
    """Check every declaration, including duplicate headers and query parameters.

    Bodyless routes remain bodyless. JSON routes check their already parsed model,
    so no middleware reads or buffers streaming bodies.
    """
    for work in request.headers.getlist("x-work-id") + request.query_params.getlist("work_id"):
        require_sc(work)
