"""
IMS 크롤만 수행 → jiip_unpaid_snapshots 테이블 upsert.
실제 SMS 발송은 하지 않음. (auto_send.py 가 따로 담당)

사용:
    ./venv/bin/python crawl_only.py
"""
from datetime import datetime
from db import get_client, KST
from jiip_vehicles import get_jiip_vehicles
from ims_crawler import collect_all


SNAPSHOT_COLS = (
    'claim_id', 'registration_id', 'rent_car_number', 'car_model', 'customer_name',
    'customer_car_number', 'insurer', 'insurance_manager_name', 'insurance_manager_phone',
    'billing_amount', 'billing_date', 'delivered_at', 'return_date', 'claim_state',
)


def main():
    started = datetime.now(KST)
    print(f'=== CRAWL 시작 {started.strftime("%Y-%m-%d %H:%M:%S")} KST ===')

    sb = get_client()
    vehicles = get_jiip_vehicles()
    print(f'[1] 지입차량 {len(vehicles)}대')
    if not vehicles:
        return

    all_records = collect_all(vehicles)
    print(f'[2] 전체 청구 {len(all_records)}건 (cutoff 적용 전)')

    # cutoff 적용: settings.cutoff_billing_date 이전 또는 billing_date null 인 건은 저장 안 함
    settings = sb.table('jiip_settings').select('cutoff_billing_date').eq('id', 1).single().execute().data
    cutoff = settings.get('cutoff_billing_date') if settings else None
    if cutoff:
        before = len(all_records)
        all_records = [r for r in all_records if r.get('billing_date') and r['billing_date'] >= cutoff]
        print(f'[2-cutoff] cutoff={cutoff} 적용 → {before} → {len(all_records)}건 (이전 건 {before-len(all_records)} 제외)')

    now_iso = datetime.now(KST).isoformat()

    # jiip_all_claims (통계용): 모든 청구, paid_at 포함
    all_rows = []
    for r in all_records:
        all_rows.append({
            **{k: r.get(k) for k in SNAPSHOT_COLS},
            'paid_at': r.get('paid_at'),
            'last_crawled_at': now_iso,
        })
    if all_rows:
        sb.table('jiip_all_claims').upsert(all_rows, on_conflict='claim_id').execute()
        print(f'[3] jiip_all_claims upsert {len(all_rows)}건')

    # jiip_unpaid_snapshots (발송 캐시): 미입금만
    unpaid = [r for r in all_records if not r.get('paid_at')]
    if unpaid:
        unpaid_rows = [{**{k: r.get(k) for k in SNAPSHOT_COLS}, 'last_crawled_at': now_iso, 'is_active': True} for r in unpaid]
        sb.table('jiip_unpaid_snapshots').upsert(unpaid_rows, on_conflict='claim_id').execute()
        print(f'[4] jiip_unpaid_snapshots upsert {len(unpaid_rows)}건')

    # 이번 크롤에서 미입금 아닌 active 행 = 입금완료/취소 → 비활성화
    unpaid_ids = {r['claim_id'] for r in unpaid}
    active_rows = sb.table('jiip_unpaid_snapshots').select('claim_id').eq('is_active', True).execute().data
    stale_ids = [r['claim_id'] for r in active_rows if r['claim_id'] not in unpaid_ids]
    if stale_ids:
        sb.table('jiip_unpaid_snapshots').update({'is_active': False}).in_('claim_id', stale_ids).execute()
        print(f'[5] 사라진/입금완료 {len(stale_ids)}건 → is_active=false')

    ended = datetime.now(KST)
    print(f'=== CRAWL 완료 {ended.strftime("%Y-%m-%d %H:%M:%S")} KST ({(ended - started).seconds}s) ===')


if __name__ == '__main__':
    main()
