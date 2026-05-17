# blrent-jiip-claims

신동석부장 지입차량의 사고대차 미입금 청구건을 매일 오전 9시(KST)에
보험사 담당자별로 1통씩 묶어서 SMS 발송하는 자동화.

운영 cron 은 **Vultr 서버 (158.247.239.114)** crontab 에서 실행.
GitHub Actions 는 비상용 수동 트리거만 유지 (중복 발송 방지).

## 흐름
1. Supabase `vehicles` 테이블에서 `customer_name='신동석부장'` + `contracts.status='지입'` 인 차량 조회
2. 각 차량번호로 imsform.com 검색 (회사 IMS 계정 사용)
3. 청구 데이터 중 `claim_total_cost > 0 AND claim_done_at IS NULL` 인 미입금 건만 필터
4. `claim_insurance_contact` 기준으로 담당자별 그룹핑
5. 솔라피로 담당자당 1통 발송 (90byte 초과 시 자동 LMS 전환)

## 파일
- `jiip_vehicles.py` — Supabase에서 대상 차량 17대 조회
- `ims_crawler.py` — IMS 로그인 + 검색 + 미입금 필터
- `solapi_sender.py` — 솔라피 REST 발송 (HMAC-SHA256 인증)
- `notifier.py` — 메인 진입점
- `.github/workflows/daily.yml` — KST 09시 cron

## 로컬 실행
```powershell
pip install -r requirements.txt
Copy-Item .env.example .env       # 값 채우기
$env:DRY_RUN='true'               # 실제 발송 전 페이로드만 확인
python notifier.py
```

## GitHub Actions Secrets (필수)
| Secret | 설명 |
|---|---|
| `SUPABASE_URL` | blrent-car-system 과 동일 |
| `SUPABASE_KEY` | service_role key |
| `IMS_ID` | 회사 IMS 계정 ID |
| `IMS_PW` | 회사 IMS 계정 PW |
| `SOLAPI_API_KEY` | 솔라피 API Key |
| `SOLAPI_API_SECRET` | 솔라피 API Secret |
| `SOLAPI_FROM` | 사전등록 발신번호 (예: 0212345678) |

GitHub Actions Variables (선택):
- `OWNER_NAME` — 기본 `신동석부장`
- `NOTIFY_OWNER` — `true` 면 지입주 본인에게도 요약 SMS

## 수동 실행 / DRY RUN
GitHub → Actions → "Daily Unpaid Notifier" → Run workflow → `dry_run=true` 선택하면
실제 발송 없이 페이로드만 로그로 확인 가능.

## 문자 본문 수정
- 보험사 담당자에게 보내는 본문: `notifier.py` 의 `build_manager_message()` 함수
- 지입주 요약 본문: `notifier.py` 의 `build_owner_summary()` 함수

## 안전장치
- 보험사 담당자 연락처가 비어있으면 자동발송에서 제외하고 경고 로그만 출력
- 같은 청구건이 IMS 페이지네이션에서 중복 등장해도 `claim_id` 기준 dedup
- DRY_RUN=true 모드 지원 (스팸 방지)

## 알려진 한계
- 매일 발송이라 미입금 장기화 시 같은 담당자에게 반복 발송됨 (사용자 의도)
- IMS API 비공개라 `claim_state` 의미가 바뀌면 깨질 수 있음
- IMS 응답의 `claim_insurance_contact` 가 비어있는 계약이 많을 수 있음 (수동 대응)
