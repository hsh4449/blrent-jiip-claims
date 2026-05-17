"""
queue_worker 데몬 모드. 2초마다 큐 폴링 + 처리.
systemd 가 관리. 한 번 시작되면 무한 실행.
"""
import time
import traceback
from queue_worker import main as process_one

INTERVAL = 2  # seconds


def loop():
    while True:
        try:
            process_one()
        except Exception:
            print('[DAEMON] error during processing:')
            traceback.print_exc()
        time.sleep(INTERVAL)


if __name__ == '__main__':
    print(f'[DAEMON] queue daemon start, polling every {INTERVAL}s')
    loop()
