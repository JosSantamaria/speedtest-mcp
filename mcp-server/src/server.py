import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastmcp import FastMCP

SPEEDTEST_BASE_URL = os.getenv("SPEEDTEST_BASE_URL", "http://speedtest:80")
SPEEDTEST_API_KEY = os.getenv("SPEEDTEST_API_KEY", "")

mcp = FastMCP("speedtest-mcp")


def _get_headers() -> dict:
    if SPEEDTEST_API_KEY:
        return {"Authorization": f"Bearer {SPEEDTEST_API_KEY}"}
    return {}


def _parse_result(r: dict) -> dict:
    return {
        "download_mbps": round(r.get("download_bits", 0) / 1_000_000, 2),
        "upload_mbps": round(r.get("upload_bits", 0) / 1_000_000, 2),
        "ping_ms": r.get("ping_latency", 0),
        "jitter_ms": r.get("ping_jitter", 0),
        "timestamp": r.get("created_at", ""),
        "server_name": r.get("server_name", ""),
    }


def _fetch_latest() -> dict:
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{SPEEDTEST_BASE_URL}/api/v1/results/latest",
            headers=_get_headers(),
        )
        resp.raise_for_status()
        return _parse_result(resp.json().get("data", {}))


def _fetch_history(days: int = 7) -> list:
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{SPEEDTEST_BASE_URL}/api/v1/results",
            params={"per_page": 100},
            headers=_get_headers(),
        )
        resp.raise_for_status()
        results = resp.json().get("data", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = []
    for r in results:
        try:
            ts_str = r.get("created_at", "")
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                filtered.append(_parse_result(r))
        except (ValueError, AttributeError):
            continue
    return filtered


@mcp.tool()
def get_latest_result() -> dict:
    """Get the latest speedtest result with download_mbps, upload_mbps, ping_ms, jitter_ms, timestamp and server_name."""
    return _fetch_latest()


@mcp.tool()
def get_history(days: int = 7) -> list:
    """Get speedtest history for the last N days. Returns a list of results."""
    return _fetch_history(days)


@mcp.tool()
def run_speedtest() -> dict:
    """Trigger a manual speedtest and return the result when complete (may take 30-60s)."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{SPEEDTEST_BASE_URL}/api/v1/speedtests/run",
            headers={**_get_headers(), "Accept": "application/json"},
        )
        resp.raise_for_status()
    for _ in range(12):
        time.sleep(10)
        try:
            result = _fetch_latest()
            if result.get("timestamp"):
                result["triggered"] = True
                return result
        except Exception:
            continue
    return {"error": "Speedtest did not complete in 120s"}


@mcp.tool()
def compare_vs_plan(
    expected_down: float = 100.0,
    expected_up: float = 40.0,
) -> dict:
    """Compare last 7 days average speedtest results vs ISP plan. Returns averages, percentages and a summary note."""
    history = _fetch_history(days=7)
    if not history:
        return {"error": "No data available"}
    avg_down = sum(r["download_mbps"] for r in history) / len(history)
    avg_up = sum(r["upload_mbps"] for r in history) / len(history)
    pct_down = (avg_down / expected_down * 100) if expected_down > 0 else 0.0
    pct_up = (avg_up / expected_up * 100) if expected_up > 0 else 0.0
    return {
        "samples": len(history),
        "avg_download_mbps": round(avg_down, 2),
        "avg_upload_mbps": round(avg_up, 2),
        "expected_download_mbps": expected_down,
        "expected_upload_mbps": expected_up,
        "download_pct": round(pct_down, 1),
        "upload_pct": round(pct_up, 1),
        "note": f"ISP delivering {pct_down:.0f}% down / {pct_up:.0f}% up of contracted plan",
    }


if __name__ == "__main__":
    mcp.run(
        transport="sse",
        host="0.0.0.0",
        port=int(os.getenv("MCP_PORT", "3100")),
    )
