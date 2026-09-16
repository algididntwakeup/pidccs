#!/usr/bin/env python3
"""
P&ID Studio Web Platform - Production Deployment Smoke Test
Verifies operational readiness across API, Database, Storage, and Frontend.
"""

import sys
import time
import urllib.request
import urllib.error
import json

API_BASE = "http://localhost:8000"
FRONTEND_BASE = "http://localhost:3000"


def check_endpoint(url: str, desc: str, timeout: int = 5) -> bool:
    print(f"[*] Checking {desc} ({url})... ", end="", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PIDStudioSmokeTest/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            if status == 200:
                print(f"[OK] (HTTP {status})")
                return True
            else:
                print(f"[WARN] (HTTP {status})")
                return False
    except urllib.error.HTTPError as e:
        print(f"[FAIL] (HTTP {e.code}: {e.reason})")
        return False
    except urllib.error.URLError as e:
        print(f"[FAIL] (Connection Refused: {e.reason})")
        return False
    except Exception as e:
        print(f"[ERROR] ({e})")
        return False


def main():
    print("==================================================================")
    print("  P&ID Studio Web Platform - Deployment Smoke Test")
    print("==================================================================")

    results = []

    # 1. API Liveness Probe
    results.append(("API Liveness (/healthz)", check_endpoint(f"{API_BASE}/healthz", "Backend Liveness Probe")))

    # 2. API Readiness Probe
    results.append(("API Readiness (/readyz)", check_endpoint(f"{API_BASE}/readyz", "Backend Readiness Probe")))

    # 3. OpenAPI Documentation
    results.append(("OpenAPI Docs (/docs)", check_endpoint(f"{API_BASE}/docs", "Swagger UI Docs")))

    # 4. Project Service & Database Connection
    results.append(("Database & Project Service", check_endpoint(f"{API_BASE}/api/v1/projects", "REST API Projects Endpoint")))

    # 5. Frontend Web Client
    results.append(("Frontend UI (Port 3000)", check_endpoint(FRONTEND_BASE, "Next.js Web Interface")))

    print("\n==================================================================")
    print("  Smoke Test Summary Results:")
    print("==================================================================")
    all_passed = True
    for name, status in results:
        indicator = "[PASS]" if status else "[FAIL]"
        print(f"  {indicator} {name}")
        if not status:
            all_passed = False

    if all_passed:
        print("\n>>> ALL SERVICES OPERATIONAL AND READY FOR PRODUCTION! <<<")
        sys.exit(0)
    else:
        print("\n>>> WARNING: One or more services are not reachable. Please check docker logs. <<<")
        sys.exit(1)


if __name__ == "__main__":
    main()
