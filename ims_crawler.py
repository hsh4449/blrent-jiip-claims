"""
IMS Form 사고대차 크롤러 - 지입차량용

- 주어진 차량번호 리스트로 imsform.com 검색
- __NEXT_DATA__ JSON 에서 청구건 추출
- '청구금액 > 0 AND 입금일 null' 인 미입금 건만 반환

기존 blrent-accidenterp/crawler.py 의 로그인/검색 로직을 재사용.
"""
import os
import re
import json
import hashlib
import requests
from urllib.parse import quote

IMS_ID = os.environ['IMS_ID']
IMS_PW = os.environ['IMS_PW']


def login():
    """IMS 로그인 → JWT 쿠키 설정된 세션 반환"""
    pw_hash = hashlib.sha256(IMS_PW.encode('utf-8')).hexdigest()
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    resp = session.post(
        'https://api.rencar.co.kr/auth',
        json={'username': IMS_ID, 'password': pw_hash},
        headers={'Content-Type': 'application/json', 'Origin': 'https://imsform.com'},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'[LOGIN] HTTP {resp.status_code}: {resp.text[:200]}')
    token = resp.json().get('access_token')
    if not token:
        raise RuntimeError('[LOGIN] access_token 없음')
    session.cookies.set('production-imsform-jwt', token, domain='imsform.com')
    return session


def parse_phone(p):
    if not p:
        return ''
    d = re.sub(r'[^\d]', '', p)
    if len(d) == 11:
        return f'{d[:3]}-{d[3:7]}-{d[7:]}'
    return p


def search_vehicle(session, car_number):
    """차량번호로 IMS 청구 리스트 페이지네이션 조회. raw claim dict 리스트 반환."""
    claims = []
    page = 1
    while True:
        # IMS UI 와 동일하게 차량번호 전체(한글 포함)를 URL 인코딩해서 쿼리
        url = (
            f'https://imsform.com/contract/list/all'
            f'?page={page}&option=rent_car_number&value={quote(car_number)}&is_corporation=all'
        )
        resp = session.get(url, timeout=30)
        if resp.status_code != 200:
            print(f'  [WARN] {car_number} page {page}: HTTP {resp.status_code}')
            break

        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text)
        if not m:
            print(f'  [WARN] {car_number} page {page}: __NEXT_DATA__ 없음')
            break

        data = json.loads(m.group(1))
        api_result = data.get('props', {}).get('pageProps', {}).get('apiResult', {}) or {}
        page_claims = api_result.get('claimList', []) or []
        claims.extend(page_claims)

        total_pages = api_result.get('totalPage', 1) or 1
        if page >= total_pages:
            break
        page += 1
    return claims


def to_record(claim, vehicle_meta):
    """청구 raw → 통일된 dict (paid_at 포함). 청구금액 0 이면 None."""
    try:
        billing = int(claim.get('claim_total_cost') or 0)
    except (ValueError, TypeError):
        billing = 0
    if billing <= 0:
        return None

    rent_car = claim.get('rent_car_number') or ''
    return {
        'claim_id': str(claim.get('id') or ''),
        'registration_id': claim.get('registration_id') or '',
        'rent_car_number': rent_car,
        'car_model': vehicle_meta.get('model') or claim.get('car_model') or '',
        'customer_name': claim.get('customer_name') or '',
        'customer_car_number': claim.get('customer_car_number') or '',
        'insurer': claim.get('insurance_company') or '',
        'insurance_manager_name': claim.get('claim_insurance_manager') or '',
        'insurance_manager_phone': parse_phone(claim.get('claim_insurance_contact')),
        'billing_amount': billing,
        'billing_date': (claim.get('claim_at') or '')[:10] or None,
        'delivered_at': (claim.get('delivered_at') or '')[:10] or None,
        'return_date': (claim.get('return_date') or '')[:10] or None,
        'claim_state': claim.get('claim_state') or '',
        'paid_at': (claim.get('claim_done_at') or '')[:10] or None,
    }


def collect_all(vehicles):
    """전체 차량 리스트 → 모든 청구 (입금 완료 포함). 중복 제거. paid_at 포함."""
    session = login()
    print(f'[LOGIN] 성공')

    records = {}
    for v in vehicles:
        car_number = v['car_number']
        suffix = re.sub(r'[^\d]', '', car_number)[-4:]
        if not suffix:
            continue
        print(f'[SEARCH] {car_number} (suffix={suffix})')
        claims = search_vehicle(session, suffix)
        print(f'  → {len(claims)}건 raw')

        for c in claims:
            our_match = False
            rc = c.get('rent_car_number') or ''
            if rc.endswith(suffix):
                our_match = True
            else:
                for d in (c.get('details') or []):
                    if not d:
                        continue
                    if (d.get('rent_car_number') or '').endswith(suffix):
                        our_match = True
                        break
            if not our_match:
                continue
            rec = to_record(c, v)
            if rec is None:
                continue
            records[rec['claim_id']] = rec

    result = list(records.values())
    paid = sum(1 for r in result if r.get('paid_at'))
    print(f'[TOTAL] 전체 {len(result)}건 (입금완료 {paid}건 / 미입금 {len(result)-paid}건)')
    return result


def collect_unpaid(vehicles):
    """입금 미완료 건만 반환 (하위 호환). collect_all 결과에서 paid_at null 인 것만."""
    return [r for r in collect_all(vehicles) if not r.get('paid_at')]


if __name__ == '__main__':
    from jiip_vehicles import get_jiip_vehicles
    vs = get_jiip_vehicles()
    print(f'\n대상 차량 {len(vs)}대\n')
    rows = collect_all(vs)
    print()
    for r in rows[:20]:
        paid_mark = '✓' if r.get('paid_at') else ' '
        print(
            f"  {paid_mark} [{r['rent_car_number']}] {r.get('customer_name','')[:6]:<8} "
            f"청구 {r.get('billing_date') or '-'} / 입금 {r.get('paid_at') or '-'} / "
            f"{r['billing_amount']:>10,}"
        )
