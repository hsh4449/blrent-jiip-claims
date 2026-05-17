"""
발송 공통 로직.

snapshot 읽고 → excluded 제외 → 담당자별 묶어 → 솔라피 발송 → log 기록.

dry_run=True 면 실제 발송 없이 페이로드만 반환. (사고 방지)
target_phones / target_claim_ids 로 부분 발송 가능.
"""
from collections import defaultdict
from datetime import datetime

from db import get_client, KST
from jiip_vehicles import get_jiip_vehicles, OWNER_NAME
from solapi_sender import _auth_header, _byte_len, API_URL, SOLAPI_FROM
import requests

# ─────────────────────────────────────────────────────────────────────────
# 🚨 MASTER KILL SWITCH 🚨
#
# True 인 동안 모든 발송 경로(자동/수동/테스트)가 dry_run 으로 강제 전환됩니다.
# 사용자(황성현)가 명시적으로 "보내라" 또는 "킬스위치 풀어줘" 라고 요청한 경우에만
# False 로 바꿔서 git push 할 것. 절대 자동으로 풀지 마세요.
# 풀린 후에도 jiip_settings.send_armed 게이트 + 사용자 클릭 확인이 추가로 필요합니다.
#
# 설정 이유: 2026-05-17 사고 후 사용자 요청
# "내가 프로그램 완성되서 보내라고 하기 전까진 절대 테스트문자도 보내선 안됨"
# ─────────────────────────────────────────────────────────────────────────
MASTER_KILL_SWITCH = True


def fmt_won(n):
    return f'{int(n):,}원'


def load_active_snapshots(sb):
    return (
        sb.table('jiip_unpaid_snapshots')
        .select('*')
        .eq('is_active', True)
        .execute()
        .data
    )


def load_excluded_ids(sb):
    rows = sb.table('jiip_excluded_claims').select('claim_id').execute().data
    return {r['claim_id'] for r in rows}


def build_manager_message(items):
    today = datetime.now(KST).strftime('%Y-%m-%d')
    manager_name = items[0].get('insurance_manager_name') or '담당자'
    insurer = items[0].get('insurer') or ''
    lines = [
        f'[{insurer} {manager_name}님께] 사고대차 미입금 안내 ({today})',
        '',
        f'아래 {len(items)}건의 사고대차 청구건이 입금 확인되지 않아 안내드립니다.',
        '',
    ]
    total = 0
    for i, r in enumerate(items, 1):
        lines.append(f'{i}) {r.get("customer_name", "")} ({r.get("customer_car_number") or "-"})')
        lines.append(f'   대차 {r.get("rent_car_number", "")} {r.get("car_model") or ""}')
        lines.append(
            f'   기간 {r.get("delivered_at") or "?"}~{r.get("return_date") or "?"} / '
            f'청구 {r.get("billing_date") or "?"} / {fmt_won(r.get("billing_amount", 0))}'
        )
        total += int(r.get('billing_amount') or 0)
    lines += ['', f'합계 {fmt_won(total)}', '빠른 입금 처리 부탁드립니다. 감사합니다.', '- 비엘렌터카']
    return '\n'.join(lines), total


def build_owner_summary(active_unpaid, no_contact_count):
    today = datetime.now(KST).strftime('%Y-%m-%d')
    total = sum(int(r.get('billing_amount') or 0) for r in active_unpaid)
    by_ins = defaultdict(lambda: {'cnt': 0, 'amt': 0})
    for r in active_unpaid:
        k = r.get('insurer') or '(보험사미상)'
        by_ins[k]['cnt'] += 1
        by_ins[k]['amt'] += int(r.get('billing_amount') or 0)
    lines = [
        f'[{OWNER_NAME}님 지입차량] 사고대차 미입금 현황 ({today})',
        '',
        f'총 {len(active_unpaid)}건 / 합계 {fmt_won(total)}',
        '',
    ]
    for ins, v in sorted(by_ins.items(), key=lambda x: -x[1]['amt']):
        lines.append(f'- {ins}: {v["cnt"]}건 / {fmt_won(v["amt"])}')
    if no_contact_count:
        lines += ['', f'※ 담당자 연락처 없음 {no_contact_count}건은 자동발송 제외']
    return '\n'.join(lines), total


def build_message_plan(snapshots, excluded_ids, notify_owner, target_phones=None, target_claim_ids=None):
    """발송 계획 생성. 실제 발송 안 함.
    Returns: {messages: [{phone, name, type, insurer, claim_ids, total, text}, ...], owner_message: {...} or None, no_contact_count: int}
    """
    # 제외 적용
    pool = [s for s in snapshots if s['claim_id'] not in excluded_ids]
    if target_claim_ids is not None:
        pool = [s for s in pool if s['claim_id'] in set(target_claim_ids)]

    # 담당자별 그룹핑
    groups = defaultdict(list)
    no_contact = []
    for r in pool:
        phone = (r.get('insurance_manager_phone') or '').replace('-', '').strip()
        if not phone:
            no_contact.append(r)
            continue
        groups[phone].append(r)

    if target_phones is not None:
        wanted = {p.replace('-', '').strip() for p in target_phones}
        groups = {k: v for k, v in groups.items() if k in wanted}

    messages = []
    for phone, items in groups.items():
        text, total = build_manager_message(items)
        messages.append({
            'phone': phone,
            'recipient_name': items[0].get('insurance_manager_name'),
            'recipient_type': 'insurance_manager',
            'insurer': items[0].get('insurer'),
            'claim_ids': [it['claim_id'] for it in items],
            'total_amount': total,
            'text': text,
            'msg_type': 'LMS' if _byte_len(text) > 90 else 'SMS',
        })

    owner_msg = None
    if notify_owner and target_phones is None and target_claim_ids is None:
        # 전체 발송일 때만 지입주 요약. 타겟 발송은 요약 생략.
        owner_vehicles = get_jiip_vehicles()
        owner_phone = (owner_vehicles[0].get('owner_phone') or '').replace('-', '').strip() if owner_vehicles else ''
        if owner_phone:
            text, total = build_owner_summary(pool, len(no_contact))
            owner_msg = {
                'phone': owner_phone,
                'recipient_name': OWNER_NAME,
                'recipient_type': 'owner',
                'insurer': None,
                'claim_ids': [r['claim_id'] for r in pool],
                'total_amount': total,
                'text': text,
                'msg_type': 'LMS' if _byte_len(text) > 90 else 'SMS',
            }

    return {'messages': messages, 'owner_message': owner_msg, 'no_contact_count': len(no_contact), 'no_contact_items': no_contact}


def _send_one_batch(payload_msgs):
    """솔라피로 한 번에 발송. 그룹별 SMS/LMS 자동 처리."""
    headers = {
        'Authorization': _auth_header(),
        'Content-Type': 'application/json',
    }
    resp = requests.post(API_URL, json={'messages': payload_msgs}, headers=headers, timeout=30)
    body = {}
    try:
        body = resp.json()
    except Exception:
        body = {'raw': resp.text[:1000]}
    return resp.status_code, body


def send_plan(plan, *, dry_run, trigger_type, triggered_by, sb=None):
    """plan 을 실제 발송 + 로그 기록.

    안전 게이트: jiip_settings.send_armed=false 면 dry_run 으로 강제 전환.
    1회 발송 성공 후 send_armed 는 자동 false 로 복귀 (1회용 무장).
    """
    if sb is None:
        sb = get_client()

    all_msgs = list(plan['messages'])
    if plan['owner_message']:
        all_msgs.append(plan['owner_message'])

    if not all_msgs:
        return {'sent': 0, 'failed': 0, 'dry_run': dry_run, 'count': 0}

    # 🚨 MASTER KILL SWITCH — 최상위 게이트
    if MASTER_KILL_SWITCH and not dry_run:
        print(f'[KILL_SWITCH] MASTER_KILL_SWITCH=True → 강제 dry_run (triggered_by={triggered_by})')
        dry_run = True

    # 발송 잠금 체크 (dry_run 이 아닌 경우만)
    armed = False
    if not dry_run:
        settings = sb.table('jiip_settings').select('send_armed,armed_by').eq('id', 1).single().execute().data
        armed = bool(settings.get('send_armed'))
        if not armed:
            print(f'[LOCK] send_armed=false → 강제 dry_run 으로 전환 (triggered_by={triggered_by})')
            dry_run = True

    if dry_run:
        return {
            'sent': 0, 'failed': 0, 'dry_run': True,
            'count': len(all_msgs), 'messages': all_msgs,
            'blocked_by_kill_switch': MASTER_KILL_SWITCH,
            'blocked_by_lock': (not armed and trigger_type != 'preview' and not MASTER_KILL_SWITCH),
        }

    # 실제 발송
    payload_msgs = []
    for m in all_msgs:
        payload_msgs.append({
            'to': m['phone'],
            'from': SOLAPI_FROM.replace('-', ''),
            'text': m['text'],
            'type': m['msg_type'],
            'subject': '[비엘렌터카] 사고대차 미입금 안내' if m['msg_type'] == 'LMS' else None,
        })
    # subject None 제거
    for pm in payload_msgs:
        if pm.get('subject') is None:
            pm.pop('subject', None)

    status_code, body = _send_one_batch(payload_msgs)
    print(f'[SOLAPI] HTTP {status_code}')

    sent = 0
    failed = 0
    if status_code < 400:
        gi = body.get('groupInfo') or {}
        cnt = gi.get('count') or {}
        sent = cnt.get('registeredSuccess', 0)
        failed = cnt.get('registeredFailed', 0)
    else:
        failed = len(all_msgs)

    # 로그 기록 (한 통씩)
    group_id = body.get('groupId')
    log_rows = []
    for m in all_msgs:
        log_rows.append({
            'trigger_type': trigger_type,
            'recipient_phone': m['phone'],
            'recipient_name': m['recipient_name'],
            'recipient_type': m['recipient_type'],
            'insurer': m['insurer'],
            'claim_ids': m['claim_ids'],
            'total_amount': m['total_amount'],
            'message_type': m['msg_type'],
            'message_text': m['text'],
            'solapi_message_id': group_id,
            'solapi_status_code': status_code,
            'solapi_response': body if status_code >= 400 else None,
            'triggered_by': triggered_by,
        })
    if log_rows:
        sb.table('jiip_sms_logs').insert(log_rows).execute()

    # 1회용 무장 자동 해제 (실제 발송 시도된 직후, 성공/실패 무관)
    sb.table('jiip_settings').update({
        'send_armed': False,
        'armed_at': None,
        'armed_by': None,
        'updated_at': datetime.now(KST).isoformat(),
        'updated_by': f'auto-disarm:{triggered_by}',
    }).eq('id', 1).execute()
    print('[LOCK] 발송 후 send_armed 자동 false 복귀')

    return {'sent': sent, 'failed': failed, 'count': len(all_msgs), 'group_id': group_id, 'status_code': status_code}
