"""
Pin requirements to the exact versions installed in your local venv, so the container
runs the same library versions you tested with (important: the saved calibrator was
pickled with your local scikit-learn version).

    python scripts/pin_requirements.py requirements-api.in requirements-api.txt
"""

import re
import sys
from importlib.metadata import PackageNotFoundError, version

src, dst = sys.argv[1], sys.argv[2]
out, missing = [], []
for line in open(src, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    name = re.split(r"[\[<>=]", line)[0]
    try:
        out.append(f"{line}=={version(name)}")
    except PackageNotFoundError:
        missing.append(name)
if missing:
    sys.exit(f"Not installed in this environment: {', '.join(missing)}")
with open(dst, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print(f"Wrote {len(out)} pinned packages to {dst}")