#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import urllib.request


def probe(url: str) -> str:
    request = urllib.request.Request(url, headers={"Connection": "close"})
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.headers.get("X-Upstream-Addr", "unknown")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:9000/health")
    parser.add_argument("--replicas", type=int, default=8)
    parser.add_argument("--requests", type=int, default=64)
    args = parser.parse_args()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.requests) as pool:
        upstreams = list(pool.map(lambda _: probe(args.url), range(args.requests)))

    observed = sorted({value for value in upstreams if value != "unknown"})
    payload = {
        "expected_replicas": args.replicas,
        "observed_replicas": len(observed),
        "upstreams": observed,
    }
    print(json.dumps(payload, indent=2))
    if len(observed) < args.replicas:
        raise SystemExit(
            f"WebSearch distribution check failed: observed {len(observed)} of {args.replicas} replicas."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
