import sys
from pathlib import Path
sys.path.insert(0, "/work/kit")
from kit_build import build
ROOT = Path(__file__).parent
build(src=ROOT / "report_src_v3.html", out=ROOT / "index_v3.html",
      subs={"PAYLOAD_B64": (ROOT / "data/payload_v3.b64").read_text()})
