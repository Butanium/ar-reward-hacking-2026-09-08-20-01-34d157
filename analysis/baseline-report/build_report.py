import sys
from pathlib import Path

KIT = Path("/work/kit")
sys.path.insert(0, str(KIT))
from kit_build import build  # noqa: E402

ROOT = Path(__file__).parent
build(
    src=ROOT / "report_src.html",
    out=ROOT / "index.html",
    subs={"PAYLOAD": (ROOT / "data/payload.json").read_text()},
)
