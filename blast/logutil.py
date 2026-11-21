"""Structured logs on stderr. stdout is reserved for answer lines."""

from __future__ import annotations

import json
import sys
from typing import Any


def log(event: str, **fields: Any) -> None:
    record = {"event": event, **fields}
    print(json.dumps(record, sort_keys=True), file=sys.stderr)
