"""Purpose-limited core credential; never a user identity or public API key."""
import re
import secrets
import stat
from pathlib import Path

from fastapi import HTTPException, Request


def core_service_auth(path: Path | None):
    credential = None
    if path is not None:
        try:
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode) or mode & 0o077:
                raise ValueError()
            credential = path.read_bytes().rstrip(b'\r\n')
            if not re.fullmatch(rb'[0-9a-f]{64}', credential):
                raise ValueError()
        except (OSError, ValueError):
            raise RuntimeError('invalid affinity service credential file') from None

    async def require_service(request: Request) -> None:
        declarations = request.headers.getlist('authorization')
        if credential is None or len(declarations) != 1 or not secrets.compare_digest(
            declarations[0].encode('utf-8'), b'Bearer ' + credential
        ):
            raise HTTPException(404, 'Not Found')

    return require_service


# Kept for callers configuring the first (affinity) read-model slice.
affinity_service_auth = core_service_auth
