# Weekly Live Validation Checklist (old vs new)

## 매 체크 시점(예: 하루 2~4회)

1. **서비스 상태**
   - new: `GET /api/v1/trading/status`
   - old: backend/data 프로세스 alive 확인

2. **신호/주문 발생 비교**
   - 동일 심볼 세트에서 최근 N건 비교
   - new: `/api/v1/logs/signals`, `/api/v1/logs/trades`
   - old: 기존 로그 소스(action_logs 등)

3. **DB 적재 검증 (new)**
   - `log_signals` 최신 생성 시각
   - `orders` 최신 생성 시각
   - `trades` 최신 생성 시각 (fill sync 포함)
   - `log_system` 오류 급증 여부

4. **계정 상태 비교(서로 다른 paper 계정 기준)**
   - buying power, equity, 포지션 수량
   - 과도한 divergence 여부

5. **이상 징후 알람 조건**
   - judge mismatch 발생
   - order qty mismatch 발생
   - 신호는 있는데 orders 미적재
   - 시스템 에러(log_system ERROR) 연속 발생

## 주간 마감 점검

- 심볼별 총 signal/order/trade 건수 비교
- 체결 손익/보유 포지션 괴리 비교
- backtest 결과 vs live 결과 방향성/규모 비교
