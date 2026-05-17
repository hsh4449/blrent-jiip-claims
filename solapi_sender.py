"""
솔라피(Solapi) SMS/LMS 발송 모듈.

직접 REST 호출 (HMAC-SHA256 인증).
- API: POST https://api.solapi.com/messages/v4/send-many/detail
- 인증: Authorization: HMAC-SHA256 apiKey=..., date=..., salt=..., signature=...

90 byte (EUC-KR 기준 약 45자) 초과 시 자동으로 LMS 로 전환됨 (subject 필요).
"""
import os
import hmac
import hashlib
import uuid
import requests
from datetime import datetime, timezone

SOLAPI_API_KEY = os.environ['SOLAPI_API_KEY']
SOLAPI_API_SECRET = os.environ['SOLAPI_API_SECRET']
SOLAPI_FROM = os.environ['SOLAPI_FROM']  # 발신번호 (사전 등록 필수)

API_URL = 'https://api.solapi.com/messages/v4/send-many/detail'


def _auth_header():
    date = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    salt = uuid.uuid4().hex
    msg = (date + salt).encode()
    sig = hmac.new(SOLAPI_API_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return f'HMAC-SHA256 apiKey={SOLAPI_API_KEY}, date={date}, salt={salt}, signature={sig}'


def _byte_len(s):
    """EUC-KR 환산 길이 (한글 2byte, ASCII 1byte) — 솔라피 SMS/LMS 판단용"""
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 127 else 1
    return n


def send_messages(messages, dry_run=False):
    """
    messages: [{'to': '010-...', 'text': '...', 'subject': '...(LMS 시)'}, ...]
    dry_run: True 면 실제 발송 안 하고 페이로드만 출력
    """
    if not messages:
        print('[SOLAPI] 발송할 메시지 없음')
        return {'sent': 0, 'failed': 0}

    payload_msgs = []
    for m in messages:
        to = (m['to'] or '').replace('-', '').strip()
        if not to:
            print(f"[SOLAPI] SKIP: 수신번호 없음 ({m.get('subject') or m['text'][:30]})")
            continue
        text = m['text']
        msg = {
            'to': to,
            'from': SOLAPI_FROM.replace('-', ''),
            'text': text,
        }
        if _byte_len(text) > 90:
            msg['type'] = 'LMS'
            msg['subject'] = m.get('subject') or '미입금 청구 안내'
        else:
            msg['type'] = 'SMS'
        payload_msgs.append(msg)

    payload = {'messages': payload_msgs}

    if dry_run:
        print('[DRY-RUN] 발송 예정:')
        for pm in payload_msgs:
            print(f"  to={pm['to']} type={pm['type']} bytes={_byte_len(pm['text'])}")
            for line in pm['text'].split('\n'):
                print(f"    {line}")
        return {'sent': 0, 'failed': 0, 'dry_run': True, 'count': len(payload_msgs)}

    headers = {
        'Authorization': _auth_header(),
        'Content-Type': 'application/json',
    }
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    print(f'[SOLAPI] HTTP {resp.status_code}')
    try:
        body = resp.json()
    except Exception:
        body = {'raw': resp.text[:500]}

    if resp.status_code >= 400:
        print(f'[SOLAPI] 실패: {body}')
        return {'sent': 0, 'failed': len(payload_msgs), 'error': body}

    group_info = body.get('groupInfo') or {}
    count = group_info.get('count') or {}
    return {
        'sent': count.get('registeredSuccess', 0),
        'failed': count.get('registeredFailed', 0),
        'group_id': body.get('groupId'),
    }
