#!/usr/bin/env python3
"""Replace only the randomized demo home path in an asciicast."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEMO_ROOT = re.compile(r"/tmp/claude-openrouter-demo\.[A-Za-z0-9]+")


def main() -> int:
    if len(sys.argv) not in {2, 4} or (len(sys.argv) == 4 and sys.argv[2] != "--secret-file"):
        raise SystemExit("usage: sanitize-demo-cast.py CAST [--secret-file FILE]")
    path = Path(sys.argv[1])
    secret = Path(sys.argv[3]).read_text(encoding="utf-8").strip() if len(sys.argv) == 4 else ""
    output: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, list) and len(value) == 3 and isinstance(value[2], str):
            if secret and secret in value[2]:
                raise SystemExit("refusing to publish a cast containing the demo credential")
            value[2] = DEMO_ROOT.sub("~", value[2])
        output.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
