"""
Quandela specs 구조 전체 덤프 — 실제 operational status 필드 탐색.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

PLATFORMS = ["qpu:ascella", "qpu:belenos", "sim:ascella", "sim:belenos"]


def _safe(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return f"<{type(v).__name__}>"


def dump_platform(platform: str, token: str) -> None:
    import perceval as pcvl
    print("=" * 72)
    print(f"[{platform}]")
    try:
        session = pcvl.QuandelaSession(platform_name=platform, token=token)
        session.start()
        p = session.build_remote_processor()

        # specs 전체
        specs = p.specs
        print("-- specs keys:", list(specs.keys()) if isinstance(specs, dict) else type(specs))
        print("-- specs (pretty):")
        print(json.dumps({k: _safe(v) for k, v in (specs or {}).items()},
                         indent=2, ensure_ascii=False, default=str))

        # performance
        try:
            perf = p.performance
            print("-- performance:", perf)
        except Exception as e:
            print(f"-- performance error: {e}")

        # RemoteProcessor dir (공개 속성/메서드)
        pub = [a for a in dir(p) if not a.startswith("_")]
        print("-- RemoteProcessor public attrs:", pub)

        # session dir
        pubs = [a for a in dir(session) if not a.startswith("_")]
        print("-- Session public attrs:", pubs)

        # 혹시 session에 get_status / list_platforms 같은 메서드가 있는지
        for name in ["get_platform_status", "platform_status", "status",
                     "list_platforms", "get_platforms", "platforms",
                     "get_platform", "available_platforms"]:
            if hasattr(session, name):
                print(f"-- session.{name} exists")
                try:
                    val = getattr(session, name)
                    if callable(val):
                        print(f"   callable: {name}()")
                    else:
                        print(f"   value: {val}")
                except Exception as e:
                    print(f"   err: {e}")

        session.stop()
    except Exception as e:
        print(f"[ERR] {type(e).__name__}: {e}")


def probe_top_level():
    import perceval as pcvl
    print("=" * 72)
    print("[perceval top-level]")
    top = [a for a in dir(pcvl) if not a.startswith("_") and "uandela" in a.lower()]
    print("-- pcvl.*Quandela*:", top)
    # 자주 쓰는 RemoteProcessor 클래스 메서드
    rp_pub = [a for a in dir(pcvl.RemoteProcessor) if not a.startswith("_")]
    print("-- RemoteProcessor class attrs:", rp_pub)


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    token = os.getenv("QUANDELA_TOKEN")
    if not token:
        print("[ERR] QUANDELA_TOKEN 미설정")
        return 2
    probe_top_level()
    for p in PLATFORMS:
        dump_platform(p, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
