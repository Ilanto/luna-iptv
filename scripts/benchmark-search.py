#!/usr/bin/env python3
"""Synthetic filter timings, excluding network and playlist import; not a CI threshold."""

import argparse
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from luna_iptv.library import ChannelFilter, ChannelModel
from luna_iptv.models import Channel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="work/qa/search-after.json")
    args = parser.parse_args()
    app = QCoreApplication([])
    result = {"kind": "synthetic-filter-only", "rows": []}
    for size in (10000, 50000, 100000):
        channels = [
            Channel(str(i), f"İstanbul Yayını {i}", "", group="Gündem") for i in range(size)
        ]
        model, proxy = ChannelModel(), ChannelFilter()
        start = time.perf_counter()
        model.reset(channels, set())
        proxy.setSourceModel(model)
        row = {
            "channels": size,
            "load_ms": round((time.perf_counter() - start) * 1000, 2),
            "samples": [],
        }
        for query in ("istanbul", "yayin 49", "bulunmayan"):
            start = time.perf_counter()
            proxy.query = query
            proxy.refresh()
            count = proxy.rowCount()
            row["samples"].append(
                {
                    "query": query,
                    "ms": round((time.perf_counter() - start) * 1000, 2),
                    "count": count,
                }
            )
        result["rows"].append(row)
        app.processEvents()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
