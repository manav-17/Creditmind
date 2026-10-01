"""
Pin requirements to the exact versions installed in your local venv, so the deployed app
runs the same library versions you tested with (important: the saved calibrator was
pickled with your local scikit-learn version).

    python scripts/pin_requirements.py requirements.in requirements.txt
"""

import re
import sys
from importlib.metadata import PackageNotFoundError, version

SPACY_MODEL_URL = ("en_core_web_sm @ https://github.com/explosion/spacy-models/releases/"
                   "download/en_core_web_sm-{v}/en_core_web_sm-{v}-py3-none-any.whl")

src, dst = sys.argv[1], sys.argv[2]
out, missing = [], []
for line in open(src, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    name = re.split(r"[\[<>=]", line)[0]
    try:
        v = version(name)
    except PackageNotFoundError:
        missing.append(name)
        continue
    # spaCy models are not on PyPI: install the matching wheel from GitHub
    out.append(SPACY_MODEL_URL.format(v=v) if name == "en_core_web_sm" else f"{line}=={v}")
if missing:
    sys.exit(f"Not installed in this environment: {', '.join(missing)}")
with open(dst, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print(f"Wrote {len(out)} pinned packages to {dst}")