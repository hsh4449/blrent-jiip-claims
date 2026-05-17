"""
Supabase에서 신동석부장 지입 차량 목록을 가져온다.

- contracts.status='지입' 이고
- vehicles.customer_name 이 OWNER_NAME 과 일치하는 차량을 반환.
"""
import os
from supabase import create_client

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

# 지입주명 (portal/jiip 에서 customer_name 컬럼 값)
OWNER_NAME = os.environ.get('OWNER_NAME', '신동석부장')


def get_jiip_vehicles():
    """[{'car_number': '07호8433', 'model': '...', 'owner': '신동석부장'}, ...]"""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    jiip_rows = sb.table('contracts').select('vehicle_id').eq('status', '지입').execute().data
    jiip_vids = list({r['vehicle_id'] for r in jiip_rows if r.get('vehicle_id')})
    if not jiip_vids:
        return []

    vehicles = (
        sb.table('vehicles')
        .select('id, car_number, model, customer_name, customer_phone')
        .eq('is_deleted', False)
        .eq('customer_name', OWNER_NAME)
        .in_('id', jiip_vids)
        .execute()
        .data
    )

    return [
        {
            'car_number': v['car_number'],
            'model': v.get('model') or '',
            'owner': v.get('customer_name') or '',
            'owner_phone': v.get('customer_phone') or '',
        }
        for v in vehicles
        if v.get('car_number')
    ]


if __name__ == '__main__':
    rows = get_jiip_vehicles()
    print(f'{OWNER_NAME} 지입차량: {len(rows)}대')
    for r in rows:
        print(f"  {r['car_number']:<10} {r['model']}")
