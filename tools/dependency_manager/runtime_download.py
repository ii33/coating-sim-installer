"""Streaming CPython downloads: verified bytes, idle bounds, and atomic publication."""

from __future__ import annotations

import hashlib
import http.client
import math
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError

from .mirrors import redact

CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 15
MAX_BYTES = 128 * 1024 * 1024
ATTEMPTS_PER_SOURCE = 2


class _SourceFailure(Exception):
    def __init__(self, error, retryable=False):
        self.error = error
        self.retryable = retryable


def _cancel(cancel_event):
    if cancel_event.is_set():
        raise InterruptedError("Runtime download cancelled; no installer was executed")


def _network_error(error, cancel_event):
    _cancel(cancel_event)
    retryable = not isinstance(error, HTTPError) or error.code in (408, 429) or error.code >= 500
    raise _SourceFailure(error, retryable) from error


def _set_idle_timeout(response, timeout):
    # urllib's open timeout also initializes the body socket. Tighten/set it
    # separately once headers arrive; read1 performs at most one buffered read.
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if sock is not None:
        sock.settimeout(timeout)


def _receive(opener, url, artifact, part, cancel_event, emit, proxy,
             connect_timeout, idle_timeout, max_bytes, source, attempt):
    digest, total, started = hashlib.sha256(), 0, time.monotonic()
    last_emitted = started
    try:
        response = opener.open(url, timeout=connect_timeout)
    except (OSError, URLError, http.client.HTTPException) as exc:
        _network_error(exc, cancel_event)
    except ValueError as exc:
        raise _SourceFailure(exc) from exc
    with response:
        _cancel(cancel_event)
        _set_idle_timeout(response, idle_timeout)
        headers = getattr(response, "headers", {})
        length = headers.get("Content-Length")
        if length is not None:
            try:
                length = int(length)
            except (TypeError, ValueError) as exc:
                raise _SourceFailure(ValueError("Invalid runtime Content-Length")) from exc
            if length < 0 or length > max_bytes:
                raise _SourceFailure(ValueError("Runtime download exceeds size bound"))
        status = getattr(response, "status", 200)
        if isinstance(status, int) and status != 200:
            raise _SourceFailure(ValueError("Runtime download requires HTTP 200"))
        with open(part, "wb") as output:
            while True:
                _cancel(cancel_event)
                try:
                    chunk = getattr(response, "read1", response.read)(64 * 1024)
                except (OSError, URLError, http.client.HTTPException) as exc:
                    _network_error(exc, cancel_event)
                _cancel(cancel_event)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise _SourceFailure(ValueError("Runtime download exceeds size bound"))
                digest.update(chunk)
                output.write(chunk)
                now = time.monotonic()
                if now - last_emitted >= 1 or total == len(chunk):
                    emit({"event": "progress", "phase": "runtime_download", "source": source,
                          "attempt": attempt, "download_bytes": total, "total_bytes": length,
                          "elapsed_seconds": round(now - started, 2)})
                    last_emitted = now
        if length is not None and total != length:
            raise _SourceFailure(OSError("Runtime download was truncated"), True)
        if digest.hexdigest() != artifact["sha256"]:
            raise _SourceFailure(ValueError(
                "Runtime SHA-256 mismatch; the downloaded file will not be executed"))
        _cancel(cancel_event)
        emit({"event": "log", "phase": "runtime_download", "source": source,
              "download_bytes": total, "sha256_verified": True,
              "text": f"Python download verified: {total} bytes; pinned official SHA-256 matched."})
        return total


def download(artifact, destination, sources, opener_factory, cancel_event, emit, proxy, *,
             connect_timeout=CONNECT_TIMEOUT, idle_timeout=IDLE_TIMEOUT, max_bytes=MAX_BYTES):
    """No elapsed-total limit: a progressing transfer may take longer than 180s.

    Connection/header waits are bounded by 10s by default; a body socket read
    without data by 15s. Cancellation is checked before/after each bounded I/O
    and during backoff. There are no background downloader threads or resume
    requests: each retry starts fresh and must match the complete pinned hash.
    """
    for value in (connect_timeout, idle_timeout):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 60:
            raise ValueError("Runtime connection/idle timeout must be between 0 and 60 seconds")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError("Runtime size bound must be between 1 and 128 MiB")
    destination = Path(destination)
    if os.path.lexists(destination):
        raise FileExistsError("Runtime download destination already exists; preserved")
    _cancel(cancel_event)
    error = None
    for index, source in enumerate(sources):
        if index:
            emit({"event": "log", "phase": "runtime_download", "source": source["id"],
                  "text": "Python mirror failed verification or download; falling back to the official HTTPS source."})
        for attempt in range(1, ATTEMPTS_PER_SOURCE + 1):
            _cancel(cancel_event)
            fd, part_name = tempfile.mkstemp(prefix=".python-download-", suffix=".part", dir=destination.parent)
            os.close(fd)
            part = Path(part_name)
            try:
                emit({"event": "log", "phase": "runtime_download", "source": source["id"],
                      "attempt": attempt, "url": source["url"], "connect_timeout": connect_timeout,
                      "idle_timeout": idle_timeout,
                      "text": f"Python download {source['id']} attempt {attempt}/{ATTEMPTS_PER_SOURCE}: {source['url']} "
                              f"(connect/header {connect_timeout:g}s; no-data read {idle_timeout:g}s; no total-duration cutoff)."})
                opener, request = opener_factory(source["url"])
                total = _receive(opener, request, artifact, part, cancel_event, emit, proxy,
                                 connect_timeout, idle_timeout, max_bytes, source["id"], attempt)
                _cancel(cancel_event)
                # Windows rename refuses existing destinations, including a race.
                # POSIX hard-link publication gives the same atomic no-clobber rule.
                if os.name == "nt":
                    os.rename(part, destination)
                else:
                    os.link(part, destination)
                return total
            except _SourceFailure as failure:
                error = failure.error
                _cancel(cancel_event)
                retry = failure.retryable and attempt < ATTEMPTS_PER_SOURCE
                emit({"event": "log", "phase": "runtime_download", "source": source["id"],
                      "attempt": attempt, "retry": retry,
                      "text": "Python download failed: " + redact(str(error), proxy)
                              + ("; retrying from byte zero." if retry else "; source exhausted.")})
                if not retry:
                    break
                if cancel_event.wait(0.5):
                    _cancel(cancel_event)
            finally:
                part.unlink(missing_ok=True)
    raise error or OSError("No verified Python download source was available")
