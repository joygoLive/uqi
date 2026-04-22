"""
Quandela Cloud 에 pending 상태로 남아있는 특정 job을 취소한다.

사용 이유: UQI 웹앱에서 submit 중 timeout(300s) 으로 예외가 발생해서
save_job() 이 호출되지 않았고, 결과적으로 로컬 DB에 기록이 없어 UI로
취소 불가능한 상태. Perceval RPCHandler.cancel_job() 을 직접 호출한다.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 취소할 job
JOB_ID   = "6eadf96a-9f2d-4788-91de-07e6eaee4888"
PLATFORM = "qpu:belenos"


def main() -> int:
    load_dotenv(Path(__file__).parent / ".env")
    token = os.getenv("QUANDELA_TOKEN")
    if not token:
        print("[ERR] QUANDELA_TOKEN 미설정")
        return 2

    import perceval as pcvl
    session = pcvl.QuandelaSession(platform_name=PLATFORM, token=token)
    session.start()
    try:
        p = session.build_remote_processor()
        rpc = p.get_rpc_handler()

        # 1) 현재 상태 조회
        print(f"[STEP 1] job status 조회: {JOB_ID}")
        try:
            status = rpc.get_job_status(JOB_ID)
            print(f"  status={status}")
        except Exception as e:
            print(f"  조회 실패: {type(e).__name__}: {e}")
            return 3

        # 2) 취소 요청
        print(f"[STEP 2] cancel_job 호출")
        try:
            rpc.cancel_job(JOB_ID)
            print("  cancel 요청 완료")
        except Exception as e:
            print(f"  cancel 실패: {type(e).__name__}: {e}")
            return 4

        # 3) 재조회로 확인
        print(f"[STEP 3] 재조회")
        try:
            status = rpc.get_job_status(JOB_ID)
            print(f"  post-cancel status={status}")
        except Exception as e:
            print(f"  재조회 실패: {type(e).__name__}: {e}")
            return 5

    finally:
        session.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
