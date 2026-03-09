# KIS vs Alpaca 기능 비교 (Parity Check)

기준 브랜치: `development`
작성일: 2026-03-09

## 1) 브로커 인터페이스 기준 비교

`app/brokers/base.py`의 공통 인터페이스 항목 기준.

| 항목 | Alpaca | KIS | Parity |
|---|---|---|---|
| `get_account_info` | 구현 | 구현 | ✅ 동등 (응답 스키마 동일) |
| `get_positions` | 구현 | 구현 | ✅ 동등 (응답 스키마 동일) |
| `submit_order` | 구현 | 구현 | ✅ 동등 (시장가/지정가 입력 가능) |
| `cancel_order` | 구현 | 구현 | ✅ 동등 (KIS 취소 API 연결) |
| `get_market_status` | 구현(실시장 상태) | 구현(현재는 `True` 기반) | ⚠️ 부분 동등 (엄밀한 시장시계는 Alpaca 우위) |
| `get_historicals` | 구현(분/시간/일) | 구현(1m/5m/15m/30m/1h/1d) | ✅ 동등 |
| `get_portfolio_history` | 구현(실제 Equity Curve) | 구현(호환 출력: synthetic curve) | ⚠️ API 스키마 동등 / 데이터 의미 차이 |
| `get_trade_fills` | 구현 | 구현 | ✅ 동등 (필드 매핑 완료) |

---

## 2) 서비스 레벨 비교

| 모듈 | Alpaca 모드 | KIS 모드 | Parity |
|---|---|---|---|
| `DataService.download_historical` | Alpaca 데이터 API | KIS broker 경유 | ✅ 동등 (브로커 모드 분기) |
| `TradingService.run_cycle` | broker 공통 인터페이스 사용 | broker 공통 인터페이스 사용 | ✅ 동등 |
| `ExecutionService.process_signal` | broker 공통 인터페이스 사용 | broker 공통 인터페이스 사용 | ✅ 동등 |
| API `/trading/*` | 정상 | 정상 | ✅ 동등 |

---

## 3) 운영 안정성 항목

| 항목 | Alpaca | KIS | 상태 |
|---|---|---|---|
| 주문 레이트리밋 대응 | 기본 | EGW00201/EGW00133 재시도(backoff) | ✅ 반영 |
| 에러 표준화 | 기본 | HTTP 400/429/503/502 매핑 | ✅ 반영 |
| 장전/장마감 처리 | 브로커 응답 | 브로커 응답 + 메시지 매핑 | ✅ 반영 |

---

## 4) 현재 남아있는 차이 (중요)

아래는 “완전 동일”을 위해 남아있는 항목.

1. **시장 상태 판정 정밀도**
   - Alpaca: `clock.is_open` 기반
   - KIS: 현재 `get_market_status=True` 단순화
   - 영향: 장외 조건에서 사이클이 실행될 수 있고, 주문 단계에서 reject될 수 있음.

2. **포트폴리오 히스토리 의미 차이**
   - Alpaca: 실제 시계열 equity/pnl
   - KIS: 현재 스키마 호환용 synthetic flat series
   - 영향: UI 그래프는 나오지만 성과 곡선 해석 정확도 차이 존재.

3. **브로커 고유 파라미터/거래소 규칙 차이**
   - KIS 미국주식은 거래소 코드/주문구분 코드 제약 존재
   - 현재는 공통 인터페이스로 흡수했지만, 특정 케이스에서 브로커별 튜닝 필요.

---

## 5) 결론

- **자동매매 루프 관점(신호→주문→포지션/체결 반영)에서는 KIS가 Alpaca와 거의 동일 흐름으로 동작**함.
- 다만 “완전 무차이(100%)”라고 하려면 아래 2개를 추가 보완해야 함:
  1) KIS 실시장 시계 기반 `get_market_status`
  2) KIS 실제 portfolio history 시계열 구현

즉, 현재 상태는 **기능 패리티는 높고, 데이터 정확도 패리티는 일부 남음**.
