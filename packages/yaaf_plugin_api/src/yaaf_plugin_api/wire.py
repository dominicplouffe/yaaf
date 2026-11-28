"""How the API's values cross the boundary between core and a plugin host.

Both sides speak this: core encodes a snapshot, the host decodes it, the hook
answers, and the host encodes the answer back. The decoder is the part that
matters for safety. Core decodes what an untrusted process sent it, so it
rebuilds only the dataclasses in `yaaf_plugin_api.types` and a handful of
plain types, never a class a message names. A message naming anything else
is a WireError, and core treats that as a host failure (fail closed).

Frames are a 4-byte big-endian length, then that many bytes of UTF-8 JSON.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import struct
from datetime import date
from typing import Any, BinaryIO

from yaaf_plugin_api import types as T

#: One frame's ceiling. A snapshot carries metadata, never bodies (those come
#: by callback), so 64 MB is room for a rendered deck and far past a mistake.
MAX_FRAME = 64 * 1024 * 1024
MAX_DEPTH = 64

#: The only classes a decoder will build, by name.
DATACLASSES: dict[str, type] = {
    cls.__name__: cls for cls in vars(T).values()
    if isinstance(cls, type) and dataclasses.is_dataclass(cls)
}


class WireError(ValueError):
    pass


def encode(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise WireError("value nested too deeply")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise WireError("a float that is not finite cannot cross the wire")
        return value
    if isinstance(value, bytes):
        return {"$t": "bytes", "v": base64.b64encode(value).decode("ascii")}
    if isinstance(value, date):
        return {"$t": "date", "v": value.isoformat()}
    if isinstance(value, tuple):
        return {"$t": "tuple", "v": [encode(v, depth + 1) for v in value]}
    if isinstance(value, frozenset):
        return {"$t": "frozenset", "v": [encode(v, depth + 1) for v in sorted(value, key=repr)]}
    if isinstance(value, list):
        return [encode(v, depth + 1) for v in value]
    if isinstance(value, dict) or (hasattr(value, "items") and hasattr(value, "keys")
                                   and not dataclasses.is_dataclass(value)):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise WireError("only string keys cross the wire")
            out[k] = encode(v, depth + 1)
        if "$t" in out:
            raise WireError('"$t" is reserved on the wire')
        return out
    if dataclasses.is_dataclass(value) and type(value).__name__ in DATACLASSES \
            and DATACLASSES[type(value).__name__] is type(value):
        fields = {f.name: encode(getattr(value, f.name), depth + 1)
                  for f in dataclasses.fields(value) if not f.name.startswith("_")}
        return {"$t": type(value).__name__, "v": fields}
    raise WireError(f"a {type(value).__name__} cannot cross the wire")


def decode(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise WireError("value nested too deeply")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [decode(v, depth + 1) for v in value]
    if not isinstance(value, dict):
        raise WireError(f"unexpected {type(value).__name__} on the wire")
    tag = value.get("$t")
    if tag is None:
        return {k: decode(v, depth + 1) for k, v in value.items()}
    v = value.get("v")
    try:
        if tag == "bytes":
            return base64.b64decode(v, validate=True)
        if tag == "date":
            return date.fromisoformat(v)
        if tag == "tuple":
            return tuple(decode(x, depth + 1) for x in v)
        if tag == "frozenset":
            return frozenset(decode(x, depth + 1) for x in v)
    except (TypeError, ValueError) as exc:
        raise WireError(f"a {tag} that is not one: {exc}") from None
    cls = DATACLASSES.get(tag)
    if cls is None or not isinstance(v, dict):
        raise WireError(f"{tag!r} is not a type the API defines")
    allowed = {f.name for f in dataclasses.fields(cls) if not f.name.startswith("_")}
    if set(v) - allowed:
        raise WireError(f"{tag} has no field {sorted(set(v) - allowed)[0]!r}")
    try:
        return cls(**{k: decode(x, depth + 1) for k, x in v.items()})
    except TypeError as exc:
        raise WireError(f"{tag} could not be built: {exc}") from None


def write_frame(stream: BinaryIO, message: dict) -> None:
    body = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise WireError(f"a frame of {len(body)} bytes is over {MAX_FRAME}")
    stream.write(struct.pack(">I", len(body)) + body)
    stream.flush()


def read_frame(stream: BinaryIO) -> dict | None:
    """The next message, or None at a clean end of stream."""
    head = _exactly(stream, 4)
    if head is None:
        return None
    (size,) = struct.unpack(">I", head)
    if size > MAX_FRAME:
        raise WireError(f"a frame of {size} bytes is over {MAX_FRAME}")
    body = _exactly(stream, size)
    if body is None:
        raise WireError("the stream ended inside a frame")
    try:
        message = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise WireError(f"a frame that is not JSON: {exc}") from None
    if not isinstance(message, dict):
        raise WireError("a frame must be one JSON object")
    return message


def _exactly(stream: BinaryIO, n: int) -> bytes | None:
    chunks, got = [], 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            if got == 0:
                return None
            raise WireError("the stream ended inside a frame")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)
