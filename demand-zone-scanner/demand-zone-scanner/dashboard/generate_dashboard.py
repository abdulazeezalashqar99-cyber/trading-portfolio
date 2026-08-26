"""
Generates the static dashboard site: copies template.html to
docs/index.html (unchanged - it's static, just fetches data.json at
load time) and writes docs/data.json from the current store state.

docs/ is the conventional folder GitHub Pages serves from when you
choose "Deploy from a branch" > main > /docs in repo Settings > Pages
(a one-time manual toggle - GitHub doesn't allow enabling Pages purely
from a workflow without extra permissions, so this is the one setup
step that has to happen in the GitHub UI).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from db.store import ZoneStore

from .data_builder import ScanStats, build_dashboard_data

TEMPLATE_PATH = Path(__file__).parent / "template.html"


def generate_dashboard(store: ZoneStore, output_dir: str, scan_stats: Optional[ScanStats] = None) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    data = build_dashboard_data(store, scan_stats)
    (out / "data.json").write_text(json.dumps(data, indent=2))

    shutil.copy(TEMPLATE_PATH, out / "index.html")
