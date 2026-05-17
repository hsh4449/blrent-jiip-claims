"""
자동발송 cron 진입점. 매일 KST 09시 호출.

- jiip_settings.auto_send_enabled = false 면 종료.
- 오늘 이미 자동발송했으면 (last_auto_send_date == today) 종료. (중복 방지)
- snapshot + excluded → 전체 발송 → log + last_auto_send_date 갱신.
"""
from datetime import datetime
from db import get_client, kst_today, KST
from send_engine import load_active_snapshots, load_excluded_ids, build_message_plan, send_plan


def main():
    started = datetime.now(KST)
    print(f'=== AUTO_SEND 시작 {started.strftime("%Y-%m-%d %H:%M:%S")} KST ===')

    sb = get_client()
    settings = sb.table('jiip_settings').select('*').eq('id', 1).single().execute().data

    if not settings.get('auto_send_enabled'):
        print('[GATE] auto_send_enabled = false → 종료')
        return

    today = kst_today().isoformat()
    if str(settings.get('last_auto_send_date') or '') == today:
        print(f'[GATE] 오늘({today}) 이미 자동발송함 → 중복 방지로 종료')
        return

    snapshots = load_active_snapshots(sb)
    excluded = load_excluded_ids(sb)
    print(f'[1] active {len(snapshots)}건 / excluded {len(excluded)}건')

    plan = build_message_plan(
        snapshots,
        excluded,
        notify_owner=settings.get('notify_owner_enabled', True),
    )
    print(f'[2] 발송 대상 담당자 {len(plan["messages"])}명 + 지입주 {1 if plan["owner_message"] else 0}통, '
          f'연락처없음 {plan["no_contact_count"]}건')

    result = send_plan(plan, dry_run=False, trigger_type='auto', triggered_by='cron', sb=sb)
    print(f'[3] 결과: {result}')

    if result.get('sent', 0) > 0 or result.get('count', 0) > 0:
        sb.table('jiip_settings').update({
            'last_auto_send_date': today,
            'updated_at': datetime.now(KST).isoformat(),
            'updated_by': 'cron:auto_send',
        }).eq('id', 1).execute()
        print(f'[4] last_auto_send_date = {today} 저장')

    print(f'=== AUTO_SEND 완료 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST ===')


if __name__ == '__main__':
    main()
