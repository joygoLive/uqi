"""
Quandela QPU 온라인/오프라인 상태 확인 스크립트.

src/uqi_calibration.py 의 수정된 로직(p.status 기반)을 그대로 재현하여
Quandela Hub UI 와 일치하는지 검증한다.
"""
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# src/uqi_calibration.py 와 동일 목록
QUANDELA_PLATFORMS = ["sim:ascella", "sim:belenos", "qpu:ascella", "qpu:belenos"]


def check_platform(platform: str, token: str) -> dict:
    import perceval as pcvl
    t0 = time.time()
    try:
        session = pcvl.QuandelaSession(platform_name=platform, token=token)
        session.start()
        p = session.build_remote_processor()
        status_str = str(getattr(p, "status", "")).lower()
        is_available = (status_str == "available")
        specs = p.specs or {}
        constraints = specs.get("constraints", {}) or {}
        max_modes = constraints.get("max_mode_count", 0) or 0
        max_photons = constraints.get("max_photon_count", 0) or 0
        session.stop()
        return {
            "platform":    platform,
            "available":   is_available,
            "status":      status_str or "unknown",
            "max_modes":   max_modes,
            "max_photons": max_photons,
            "elapsed_s":   round(time.time() - t0, 2),
            "error":       None,
        }
    except Exception as e:
        return {
            "platform":    platform,
            "available":   False,
            "status":      "offline or unreachable",
            "max_modes":   None,
            "max_photons": None,
            "elapsed_s":   round(time.time() - t0, 2),
            "error":       f"{type(e).__name__}: {e}",
        }


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    token = os.getenv("QUANDELA_TOKEN")
    if not token:
        print("[ERR] QUANDELA_TOKEN 미설정 (.env 확인)")
        return 2

    print(f"[INFO] Quandela 플랫폼 {len(QUANDELA_PLATFORMS)}개 상태 확인")
    print(f"[INFO] token=...{token[-6:]}")
    print("-" * 78)

    results = []
    for platform in QUANDELA_PLATFORMS:
        r = check_platform(platform, token)
        results.append(r)
        tag = "ONLINE " if r["available"] else "OFFLINE"
        print(f"  [{tag}] {platform:14s} status={r['status']:14s} "
              f"modes={r['max_modes']} photons={r['max_photons']} "
              f"({r['elapsed_s']}s)")
        if r["error"]:
            print(f"           error: {r['error']}")

    print("-" * 78)
    online = [r["platform"] for r in results if r["available"]]
    offline = [r["platform"] for r in results if not r["available"]]
    print(f"[SUMMARY] online({len(online)})={online}")
    print(f"[SUMMARY] offline({len(offline)})={offline}")
    return 0 if online else 1


if __name__ == "__main__":
    sys.exit(main())
