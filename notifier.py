"""
신동석부장 지입차량 미입금 청구건 → 보험사 담당자별로 묶어 SMS 발송.

흐름:
1. Supabase에서 신동석부장 지입차량 목록 조회
2. IMS에서 각 차량 검색 → 청구금액>0 AND 입금일null 인 건만 필터
3. 보험사 담당자(이름+전화)별로 그룹핑
4. 담당자 1명당 1통 묶음 메시지 발송
5. (옵션) 신동석부장 본인에게 요약 발송
"""
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from jiip_vehicles import get_jiip_vehicles, OWNER_NAME
from ims_crawler import collect_unpaid
from solapi_sender import send_messages

DRY_RUN = os.environ.get('DRY_RUN', '').lower() in ('1', 'true', 'yes')
NOTIFY_OWNER = os.environ.get('NOTIFY_OWNER', '').lower() in ('1', 'true', 'yes')

KST = timezone(timedelta(hours=9))


def fmt_won(n):
    return f'{int(n):,}원'


def group_by_manager(unpaid):
    """담당자 전화번호 기준으로 그룹핑. 연락처 없는 건은 별도 버킷."""
    groups = defaultdict(list)
    no_contact = []
    for r in unpaid:
        phone = (r.get('insurance_manager_phone') or '').strip()
        if not phone:
            no_contact.append(r)
            continue
        groups[phone].append(r)
    return groups, no_contact


def build_manager_message(items):
    """담당자 1명에게 보낼 본문 생성. 그 담당자의 모든 미입금건을 한 통에."""
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
        lines.append(
            f'{i}) {r["customer_name"]} ({r["customer_car_number"] or "-"})'
        )
        lines.append(
            f'   대차 {r["rent_car_number"]} {r["car_model"]}'
        )
        lines.append(
            f'   기간 {r["delivered_at"]}~{r["return_date"]} / 청구 {r["billing_date"]} / {fmt_won(r["billing_amount"])}'
        )
        total += r['billing_amount']

    lines.append('')
    lines.append(f'합계 {fmt_won(total)}')
    lines.append('빠른 입금 처리 부탁드립니다. 감사합니다.')
    lines.append('- 비엘렌터카')

    return '\n'.join(lines)


def build_owner_summary(unpaid, no_contact_count):
    """지입주(신동석부장)에게 보낼 요약 메시지"""
    today = datetime.now(KST).strftime('%Y-%m-%d')
    total = sum(r['billing_amount'] for r in unpaid)
    by_insurer = defaultdict(lambda: {'cnt': 0, 'amt': 0})
    for r in unpaid:
        k = r.get('insurer') or '(보험사미상)'
        by_insurer[k]['cnt'] += 1
        by_insurer[k]['amt'] += r['billing_amount']

    lines = [
        f'[{OWNER_NAME}님 지입차량] 사고대차 미입금 현황 ({today})',
        '',
        f'총 {len(unpaid)}건 / 합계 {fmt_won(total)}',
        '',
    ]
    for ins, v in sorted(by_insurer.items(), key=lambda x: -x[1]['amt']):
        lines.append(f'- {ins}: {v["cnt"]}건 / {fmt_won(v["amt"])}')
    if no_contact_count:
        lines.append('')
        lines.append(f'※ 담당자 연락처 없음 {no_contact_count}건은 자동발송 제외 (ERP 확인 필요)')
    return '\n'.join(lines)


def main():
    print(f'=== 시작 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST (DRY_RUN={DRY_RUN}) ===')

    vehicles = get_jiip_vehicles()
    print(f'[1] {OWNER_NAME} 지입차량 {len(vehicles)}대')
    if not vehicles:
        print('대상 차량 없음 — 종료')
        return

    unpaid = collect_unpaid(vehicles)
    print(f'[2] 미입금 건 {len(unpaid)}건')
    if not unpaid:
        print('미입금 건 없음 — 종료')
        return

    groups, no_contact = group_by_manager(unpaid)
    print(f'[3] 담당자별 그룹: {len(groups)}명, 연락처없음 {len(no_contact)}건')

    messages = []
    for phone, items in groups.items():
        text = build_manager_message(items)
        messages.append({
            'to': phone,
            'text': text,
            'subject': '[비엘렌터카] 사고대차 미입금 안내',
        })

    # 지입주 본인에게도 요약 발송 (옵션)
    if NOTIFY_OWNER:
        owner_phone = vehicles[0].get('owner_phone')
        if owner_phone:
            messages.append({
                'to': owner_phone,
                'text': build_owner_summary(unpaid, len(no_contact)),
                'subject': '[비엘렌터카] 지입차량 미입금 요약',
            })

    print(f'[4] 발송 대상 {len(messages)}건')
    result = send_messages(messages, dry_run=DRY_RUN)
    print(f'[5] 발송 결과: {result}')

    if no_contact:
        print('\n[WARN] 담당자 연락처 없는 미입금 건 (수동 처리 필요):')
        for r in no_contact:
            print(f'  - [{r["rent_car_number"]}] {r["customer_name"]} {fmt_won(r["billing_amount"])} (보험사 {r.get("insurer")})')

    print(f'=== 완료 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST ===')


if __name__ == '__main__':
    main()
