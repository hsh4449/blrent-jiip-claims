"""
명령 큐 워커. 매분 cron 호출.
대시보드에서 jiip_command_queue 에 INSERT 한 명령을 처리.

지원 명령:
- send_now: 수동 발송. args = {dry_run, target_phones?, target_claim_ids?, notify_owner?}
- crawl_now: 즉시 IMS 크롤
"""
from datetime import datetime
from db import get_client, KST
from send_engine import load_active_snapshots, load_excluded_ids, build_message_plan, send_plan


def process_send_now(sb, args):
    snapshots = load_active_snapshots(sb)
    excluded = load_excluded_ids(sb)
    settings = sb.table('jiip_settings').select('*').eq('id', 1).single().execute().data

    notify_owner = args.get('notify_owner')
    if notify_owner is None:
        notify_owner = settings.get('notify_owner_enabled', True)

    plan = build_message_plan(
        snapshots,
        excluded,
        notify_owner=notify_owner,
        target_phones=args.get('target_phones'),
        target_claim_ids=args.get('target_claim_ids'),
    )
    result = send_plan(
        plan,
        dry_run=bool(args.get('dry_run', False)),
        trigger_type='manual',
        triggered_by=f'queue:{args.get("requested_by", "?")}',
        sb=sb,
    )
    return result


def process_crawl_now(sb, args):
    from crawl_only import main as run_crawl
    run_crawl()
    return {'status': 'done'}


HANDLERS = {
    'send_now': process_send_now,
    'crawl_now': process_crawl_now,
}


def main():
    sb = get_client()
    # 가장 오래된 pending 1건만 처리 (다음 cron tick 에서 나머지)
    rows = (
        sb.table('jiip_command_queue')
        .select('*')
        .eq('status', 'pending')
        .order('enqueued_at')
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return

    cmd = rows[0]
    print(f'[QUEUE] {cmd["id"]} {cmd["command_type"]} args={cmd.get("args")}')

    # processing 으로 잠금
    sb.table('jiip_command_queue').update({
        'status': 'processing',
        'started_at': datetime.now(KST).isoformat(),
    }).eq('id', cmd['id']).eq('status', 'pending').execute()

    try:
        handler = HANDLERS.get(cmd['command_type'])
        if not handler:
            raise ValueError(f'unknown command_type: {cmd["command_type"]}')
        result = handler(sb, cmd.get('args') or {})
        sb.table('jiip_command_queue').update({
            'status': 'done',
            'completed_at': datetime.now(KST).isoformat(),
            'result': result,
        }).eq('id', cmd['id']).execute()
        print(f'[QUEUE] {cmd["id"]} done: {result}')
    except Exception as e:
        sb.table('jiip_command_queue').update({
            'status': 'failed',
            'completed_at': datetime.now(KST).isoformat(),
            'result': {'error': str(e)},
        }).eq('id', cmd['id']).execute()
        print(f'[QUEUE] {cmd["id"]} FAILED: {e}')
        raise


if __name__ == '__main__':
    main()
