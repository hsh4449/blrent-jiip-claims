"""
1회용 사과 SMS 발송 스크립트.
2026-05-17 사고로 실수 발송된 보험사 담당자 75명에게 사과 메시지.
"""
from db import get_client
from send_engine import send_plan, _byte_len  # noqa
from solapi_sender import _byte_len as byte_len

APOLOGY = '[비엘렌터카] 안녕하세요, 프로그램 테스트 중이었습니다.\n혼란 드려 죄송합니다.'


def main():
    print(f'본문 byte: {byte_len(APOLOGY)} (SMS=90byte 이내)')
    print(f'본문:\n---\n{APOLOGY}\n---')

    with open('/tmp/apology_recipients.txt') as f:
        phones = [line.strip() for line in f if line.strip()]
    print(f'수신자: {len(phones)}명')

    plan = {
        'messages': [
            {
                'phone': p,
                'recipient_name': '보험사담당자',
                'recipient_type': 'insurance_manager',
                'insurer': None,
                'claim_ids': [],
                'total_amount': 0,
                'text': APOLOGY,
                'msg_type': 'SMS' if byte_len(APOLOGY) <= 90 else 'LMS',
            }
            for p in phones
        ],
        'owner_message': None,
        'no_contact_count': 0,
        'no_contact_items': [],
    }

    sb = get_client()
    # 무장 (사용자 승인 1회)
    sb.table('jiip_settings').update({
        'send_armed': True,
        'armed_by': 'apology-2026-05-18',
    }).eq('id', 1).execute()
    print('[ARM] send_armed=True')

    result = send_plan(plan, dry_run=False, trigger_type='manual', triggered_by='apology-2026-05-18', sb=sb)
    print(f'[RESULT] {result}')


if __name__ == '__main__':
    main()
