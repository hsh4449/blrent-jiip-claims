"""
IMS 크롤만 수행 → jiip_unpaid_snapshots 테이블 upsert.
실제 SMS 발송은 하지 않음. (auto_send.py 가 따로 담당)

사용:
    ./venv/bin/python crawl_only.py
"""
from datetime import datetime
from db import get_client, KST
from jiip_vehicles import get_jiip_vehicles
from ims_crawler import collect_unpaid


def main():
    started = datetime.now(KST)
    print(f'=== CRAWL 시작 {started.strftime("%Y-%m-%d %H:%M:%S")} KST ===')

    sb = get_client()
    vehicles = get_jiip_vehicles()
    print(f'[1] 지입차량 {len(vehicles)}대')
    if not vehicles:
        return

    unpaid = collect_unpaid(vehicles)
    print(f'[2] 미입금 raw {len(unpaid)}건')

    if not unpaid:
        # 기존 active 모두 비활성화 (전부 입금 완료된 케이스)
        sb.table('jiip_unpaid_snapshots').update({'is_active': False}).eq('is_active', True).execute()
        print('[3] 미입금 0건 — 기존 active 모두 비활성화')
        return

    now_iso = datetime.now(KST).isoformat()
    rows = []
    for r in unpaid:
        rows.append({
            'claim_id': r['claim_id'],
            'registration_id': r.get('registration_id'),
            'rent_car_number': r['rent_car_number'],
            'car_model': r.get('car_model'),
            'customer_name': r.get('customer_name'),
            'customer_car_number': r.get('customer_car_number'),
            'insurer': r.get('insurer'),
            'insurance_manager_name': r.get('insurance_manager_name'),
            'insurance_manager_phone': r.get('insurance_manager_phone'),
            'billing_amount': r.get('billing_amount', 0),
            'billing_date': r.get('billing_date') or None,
            'delivered_at': r.get('delivered_at') or None,
            'return_date': r.get('return_date') or None,
            'claim_state': r.get('claim_state'),
            'last_crawled_at': now_iso,
            'is_active': True,
        })

    sb.table('jiip_unpaid_snapshots').upsert(rows, on_conflict='claim_id').execute()
    print(f'[3] upsert {len(rows)}건')

    # 이번 크롤에 없는 active 행 = 입금완료/취소 등 사라진 청구 → 비활성화
    current_ids = [r['claim_id'] for r in rows]
    active_rows = sb.table('jiip_unpaid_snapshots').select('claim_id').eq('is_active', True).execute().data
    stale_ids = [r['claim_id'] for r in active_rows if r['claim_id'] not in current_ids]
    if stale_ids:
        sb.table('jiip_unpaid_snapshots').update({'is_active': False}).in_('claim_id', stale_ids).execute()
        print(f'[4] 사라진 청구 {len(stale_ids)}건 비활성화')

    ended = datetime.now(KST)
    print(f'=== CRAWL 완료 {ended.strftime("%Y-%m-%d %H:%M:%S")} KST ({(ended - started).seconds}s) ===')


if __name__ == '__main__':
    main()
