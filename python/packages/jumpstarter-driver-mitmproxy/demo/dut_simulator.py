#!/usr/bin/env python3
"""Simulated DUT client that polls the backend through the proxy.

Cycles through all four API endpoints every ``--interval`` seconds,
printing colour-coded output so the "source" switch is obvious when
mock scenarios are loaded/cleared during the live demo.

Usage::

    python dut_simulator.py [--proxy http://127.0.0.1:8080]
                            [--backend http://127.0.0.1:9000]
                            [--interval 5]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

# ── ANSI colours ──────────────────────────────────────────────
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_MAGENTA = "\033[95m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _status_colour(code: int) -> str:
    if 200 <= code < 300:
        return _GREEN
    if 400 <= code < 500:
        return _YELLOW
    return _RED


def _source_colour(source: str) -> str:
    if source == "mock":
        return _MAGENTA
    return _CYAN


def _print_response(method: str, path: str, resp: requests.Response):
    ts = time.strftime("%H:%M:%S")
    sc = _status_colour(resp.status_code)

    print(f"  {_DIM}{ts}{_RESET}  {sc}{resp.status_code}{_RESET}  {method:4s} {path}")

    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        print(f"         {_DIM}(non-JSON body){_RESET}")
        return

    source = body.get("source", "?")
    src_c = _source_colour(source)
    # Print a compact one-liner for the key fields
    fields = {k: v for k, v in body.items() if k != "source"}
    summary = ", ".join(f"{k}={v}" for k, v in fields.items())
    print(f"         source={src_c}{_BOLD}{source}{_RESET}  {_DIM}{summary}{_RESET}")


def _print_error(method: str, path: str, err: Exception):
    ts = time.strftime("%H:%M:%S")
    print(f"  {_DIM}{ts}{_RESET}  {_RED}ERR{_RESET}  {method:4s} {path}")
    print(f"         {_RED}{type(err).__name__}: {err}{_RESET}")


def run_cycle(session: requests.Session, backend: str):
    """Execute one full cycle of DUT requests."""
    endpoints = [
        ("GET", f"{backend}/api/v1/status"),
        ("GET", f"{backend}/api/v1/updates/check"),
        ("POST", f"{backend}/api/v1/telemetry"),
        ("GET", f"{backend}/api/v1/config"),
    ]

    for method, url in endpoints:
        path = url.split(backend)[-1]
        try:
            if method == "POST":
                resp = session.post(
                    url,
                    json={"cpu_temp": 42.5, "mem_used_pct": 61},
                    timeout=10,
                )
            else:
                resp = session.get(url, timeout=10)
            _print_response(method, path, resp)
        except (
            requests.exceptions.ProxyError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            _print_error(method, path, exc)


def main():
    parser = argparse.ArgumentParser(description="DUT simulator")
    parser.add_argument(
        "--proxy", default="http://127.0.0.1:8080",
        help="HTTP proxy URL (default: http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--backend", default="http://127.0.0.1:9000",
        help="Backend server URL (default: http://127.0.0.1:9000)",
    )
    parser.add_argument(
        "--interval", type=float, default=5,
        help="Seconds between polling cycles (default: 5)",
    )
    args = parser.parse_args()

    session = requests.Session()
    session.proxies = {"http": args.proxy, "https": args.proxy}

    print(f"{_BOLD}DUT Simulator{_RESET}")
    print(f"  Backend : {args.backend}")
    print(f"  Proxy   : {args.proxy}")
    print(f"  Interval: {args.interval}s")
    print()

    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"{_DIM}── cycle {cycle} ──{_RESET}")
            run_cycle(session, args.backend)
            print()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{_DIM}Stopped after {cycle} cycles.{_RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
