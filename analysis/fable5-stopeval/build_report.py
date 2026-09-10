import sys
from pathlib import Path
sys.path.insert(0, "/work/kit")
from kit_build import build
ROOT = Path(__file__).parent
build(src=ROOT / "report_src.html", out=ROOT / "index.html",
      subs={"PAYLOAD_B64": (ROOT / "data/payload.b64").read_text()})
