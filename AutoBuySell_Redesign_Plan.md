
# AutoBuySell 재설계 계획서 (개인용)

_Last updated: 2026-01-01_

이 문서는 **개인 개발·운영**을 전제로 `AutoBuySell` 프로젝트를 재설계하기 위한 가이드다.  
목표는 **혼자 이해·유지보수 가능한 단순한 구조**를 유지하면서도, **알고리즘 확장·브로커 확장·백테스트·UI 개선**이 가능한 형태로 정리하는 것이다.

---

## 1. 재설계 목표

1. **아키텍처 단순화 & 현대화**
   - 서비스 간 역할을 명확히 하되, 개인이 관리 가능한 수준으로 구성
   - 불필요한 복잡도 최소화 (과도한 마이크로서비스 지양)

2. **스택 및 라이브러리 최신화**
   - Frontend: Next.js 기반 유지, TypeScript 도입, 구조 간결화
   - Backend: FastAPI + Python 3.12, 의존성 정리

3. **Docker 기반 실행**
   - `docker-compose` 하나로 전체 시스템 실행
   - `frontend`, `api`, `db` 3개 컨테이너를 기본 단위로 구성

4. **UI 최신화 & 사용자 편의 개선**
   - 상태/로그/포지션/전략 설정을 한 눈에 볼 수 있는 단순 대시보드
   - 백테스트 실행을 UI에서 바로 제어

5. **브로커(증권사) 확장 용이성**
   - 주식(Equity)만 지원
   - **증권사 SDK의 object를 내부 공통 인터페이스로 감싸는 어댑터 구조**

6. **알고리즘 변경/추가 & 백테스팅 확장성**
   - 전략(Strategy)을 플러그인처럼 교체/추가 가능
   - UI에서 전략/파라미터/기간을 선택해 백테스트 수행

7. **로깅 및 에러 핸들링 고도화 (단순 but 효율적)**
   - Postgres 하나를 중심으로, 로그까지 포함한 데이터 일원화
   - UI에서 필터링/검색 가능한 형태로 노출

8. **WebSocket 기반 실시간 UI 개선**
   - 포지션/PNL/로그의 간단한 실시간 스트리밍

---

## 2. 현재 구조 요약 (참고)

- **Frontend (`AT_V0`)**
  - Next.js 13, JS, Recoil, Styled-components, Plotly
  - 페이지: Home, Analysis, Log

- **Backend_server**
  - FastAPI, Python 3.10+, Pandas/NumPy
  - 알고리즘 실행 (`judge_and_order`), Alpaca 연동, 설정 관리

- **Data_server**
  - FastAPI, Python 3.10+, Pandas/NumPy
  - 통계 계산, 데이터 아카이빙, 로그 조회

- **Data 디렉토리**
  - `log_data/`, `market_data/`, `setting_data/`, `market_long_data/`, `target_company.csv` 등
  - CSV/파일 기반으로 데이터 공유

---

## 3. 목표 아키텍처 (단순 버전)

### 3.1 컴포넌트 구조

```text
AutoBuySell/
  frontend/      # Next.js (TS) 대시보드
  api/           # FastAPI 메인 서버 (트레이딩 + 데이터 + 백테스트)
  docker-compose.yml
```

- 기존 `Backend_server`와 `Data_server`는 **하나의 FastAPI 앱** 안에서
  - `trading`, `data`, `backtest`, `log` 라우터로 논리 분리
- 데이터는 모두 **단일 PostgreSQL**에 보관

```text
services:
  frontend  (port 3000)
  api       (port 8000)
  db        (PostgreSQL, 단일 DB)
```

### 3.2 데이터 흐름

1. 사용자가 UI에서
   - 대상 종목 선택, 전략/파라미터 설정, 실행/일시정지, 백테스트 요청
2. `frontend` → `api` REST 호출
3. `api`는
   - 설정/전략/백테스트 요청 → Postgres 저장
   - 실거래 알고리즘/백테스트 엔진 실행
   - 브로커 어댑터를 통해 주문/데이터 요청
4. 실행 결과/로그/포지션/PNL → Postgres 저장
5. UI는 REST + WebSocket으로 데이터 반영

---

## 4. 기술 스택

### 4.1 Frontend

- **Next.js 14 (App Router)** + **TypeScript**
- 상태 관리: **React Query** (+ 필요한 경우 가벼운 전역 상태 라이브러리, 예: Zustand)
- UI:
  - CSS: Tailwind 또는 간단한 UI 라이브러리(shadcn/ui, MUI 중 하나 택1)
  - 차트: Recharts 또는 단일 Chart 라이브러리 (복잡한 Plotly 구성 최소화)
- 구조 (간단 버전):

```text
frontend/
  app/
    page.tsx            # 대시보드 (요약/상태)
    analysis/page.tsx   # 분석/백테스트 결과
    log/page.tsx        # 로그 조회
  components/
    layout/
    charts/
    forms/
  lib/
    apiClient.ts        # axios or fetch wrapper
```

> 목표: **파일 수를 최소화**하고, 한 사람이 전체 구조를 머릿속에 그릴 수 있을 정도의 복잡도로 유지한다.

### 4.2 Backend (FastAPI)

- Python 3.12
- FastAPI + Uvicorn
- ORM: SQLAlchemy + async 지원 or Tortoise ORM
- Background jobs: FastAPI BackgroundTasks 또는 간단한 스케줄러 (APScheduler)

구조 예:

```text
api/
  app/main.py          # FastAPI 생성, 라우터 등록
  app/api/             # 라우터 모음
    trading.py         # 실시간/실거래 관련
    backtest.py        # 백테스트 실행/조회
    data.py            # 시세/지표/통계 조회
    settings.py        # 전략/파라미터 관리
    logs.py            # 로그 조회 API
  app/core/
    config.py
    db.py
    logging.py
  app/domain/
    models.py          # 도메인 모델 (Position, Order, Candle, Strategy 등)
    services/          # 비즈니스 로직
      trading_service.py
      backtest_service.py
      stats_service.py
  app/brokers/
    base.py            # 공통 인터페이스 (BrokerAdapter)
    alpaca.py
    other_broker_x.py  # 추후 추가
```

---

## 5. 브로커(증권사) 어댑터 구조

### 5.1 공통 인터페이스

증권사 SDK의 object를 **직접 전파하지 않고**, 내부 공통 구조로 감싸는 형태:

```python
# app/brokers/base.py
class BrokerAdapter(Protocol):
    def get_name(self) -> str: ...
    def get_account_info(self) -> AccountInfo: ...
    def get_positions(self) -> list[Position]: ...
    def get_recent_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]: ...
    def submit_order(self, order: OrderRequest) -> OrderResult: ...
```

- `AccountInfo`, `Position`, `Candle`, `OrderRequest`, `OrderResult`는 **프로젝트 내부 공통 데이터 클래스**로 정의
- 각 증권사 SDK에서 받은 object는 어댑터에서 이 공통 타입으로 변환

```python
# app/brokers/alpaca.py
class AlpacaBroker(BrokerAdapter):
    def __init__(self, client: AlpacaClient): ...
    def get_positions(self) -> list[Position]:
        raw = self.client.get_positions()
        return [self._map_position(p) for p in raw]
```

> 이 구조를 통해 **알고리즘/백테스트/프론트엔드**는 특정 증권사 SDK를 알 필요가 없다.

---

## 6. 데이터 모델 (PostgreSQL, 단일 DB)

PostgreSQL 하나에 모든 데이터 저장:

- `symbols` – 종목 정보  
- `strategies` – 사용 가능한 알고리즘 목록  
- `strategy_params` – 각 전략별 파라미터 (thr_buy, thr_sell, duration 등)  
- `accounts` – 계좌/브로커 설정 (API 키 등, 암호화·마스킹 전제)  
- `positions` – 현재 포지션(snapshot)  
- `orders` – 주문 내역  
- `candles` – 시세(OHLCV) 데이터  
- `backtest_runs` – 백테스트 실행 메타 정보 (전략, 기간, 파라미터)  
- `backtest_results` – 백테스트 결과 (결과 지표, equity curve 등)  
- `logs` – 애플리케이션/트레이딩 로그  

> 마켓 데이터까지도 Postgres에 넣되, 용량이 크게 문제되지 않는 선에서 운영(개인용 기준).  
> 추후 필요 시, 오래된 데이터는 별도 아카이빙(파티셔닝 or 삭제) 전략을 둔다.

---

## 7. 기능별 설계 (요약)

### 7.1 실거래 알고리즘 실행

- 주기적 실행 방식(예: 스케줄러):
  - 일정 주기로 `trading_service.run_cycle()` 호출
  - 현재 설정/전략/포지션/시세 → 판단 → 주문
- 알고리즘 구조 (예시):

```python
class Strategy(Protocol):
    def generate_signal(self, candles: list[Candle], positions: list[Position], params: StrategyParams) -> list[Signal]:
        ...
```

- 하나의 전략 파일은 `Strategy` 구현체만 제공하면 됨
- 향후 전략 추가 시, 새로운 파일 추가 후 등록만 하면 되도록 설계

### 7.2 백테스트 (UI에서 실행)

- UI에서 입력:
  - 전략 선택, 종목, 기간, 타임프레임, 파라미터
- API 처리 흐름:
  1. 요청 저장 (`backtest_runs`)
  2. 시세 데이터 조회 또는 필요한 경우 다운로드 후 DB 적재
  3. 동일 전략 로직으로 시뮬레이션 실행
  4. 결과 지표 계산 후 `backtest_results`에 저장
- UI에서 제공:
  - Equity curve, MDD, 승률, 평균 수익/손실, 트레이드 수
  - 간단한 테이블 + 라인 차트

### 7.3 로그 및 에러 핸들링

- 로깅 목표: **단순하지만 효율적**
- 구조:
  - `logs` 테이블에는  
    - `timestamp`, `level`, `source` (trading/backtest/api), `message`, `context (JSON)`  
  - 콘솔에도 동시에 출력 (개발 시 편의를 위해)
- 에러 핸들링:
  - FastAPI 전역 예외 핸들러에서 에러 로깅 + 사용자에게 간단한 메시지 반환
  - 트레이딩/백테스트 실패 시:
    - 에러 메시지를 로그에 남기고, UI에서 해당 실행의 상태를 확인 가능하도록

### 7.4 WebSocket 기반 실시간 UI

- 단일 WebSocket 엔드포인트 예:
  - `/ws/stream` – 포지션/PNL/로그 일부를 실시간 푸시
- 서버 측:
  - 주요 이벤트 발생 시(주문 체결, 포지션 변경, 에러 로그 등) WebSocket 브로드캐스트
- 클라이언트:
  - 대시보드 상단에 간단한 실시간 상태/로그 패널

---

## 8. Docker & 실행 구조

### 8.1 docker-compose 개요

```yaml
version: "3.9"

services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: autosys
      POSTGRES_PASSWORD: autosys
      POSTGRES_DB: autosys
    volumes:
      - db_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  api:
    build: ./api
    env_file: .env
    depends_on:
      - db
    ports:
      - "8000:8000"

  frontend:
    build: ./frontend
    depends_on:
      - api
    ports:
      - "3000:3000"

volumes:
  db_data:
```

- 목표: **로컬에서 `docker-compose up`만으로 전체 환경 실행**
- 개발 단계에서는
  - DB만 Docker로 돌리고, API/Frontend는 로컬에서 실행하는 모드도 허용

---

## 9. 단계별 작업 계획 (개발 순서 가이드)

1. **기본 인프라**
   - 새 리포 구조 정리 (`frontend/`, `api/`, `docker-compose.yml`)
   - PostgreSQL 연결 및 기본 테이블 스키마 설계/생성

2. **Backend 1차**
   - `BrokerAdapter` 인터페이스 + Alpaca 어댑터 구현
   - 최소한의 API (계정 정보, 포지션 조회, 시세 조회, 기본 설정)

3. **Frontend 1차**
   - Next.js + TS 초기 세팅
   - 대시보드 페이지에서 계정/포지션/기본 로그 조회

4. **실거래 알고리즘 포팅**
   - 기존 `judge_and_order` 로직을 `Strategy` 구조로 옮기기
   - 설정 저장/불러오기 API + UI

5. **백테스트 기능 추가**
   - 백테스트용 서비스/테이블/엔드포인트
   - UI에서 기간/전략/파라미터 설정 → 결과 시각화

6. **로그 & WebSocket**
   - `logs` 테이블 기반 API
   - WebSocket을 통한 간단 실시간 스트림

7. **리팩터링 및 마무리**
   - 폴더 구조/이름 정리
   - README, 간단한 API 설명 문서 보완 (별도 md)

---

## 10. 에이전트에게 줄 지시 예시

> 이 문서는 AutoBuySell 프로젝트 재설계 계획서다.  
> 개인 개발/운영을 전제로, 이 문서에 정의된 아키텍처/데이터 모델/브로커 어댑터 구조/단계별 계획을 따르도록 한다.  
>  
> 우선 순서는 다음과 같다.  
> 1) PostgreSQL 기반 데이터 모델 정리  
> 2) FastAPI 백엔드에서 브로커 어댑터 + 기본 조회 API 구현  
> 3) Next.js 프론트엔드 초기화 및 대시보드 최소 기능 구현  
>  
> 각 단계는 별도 커밋/PR 단위로 작게 쪼개고, 기존 알고리즘 로직의 동작을 깨지 않도록 한다.
> 본 시스템의 python 동작은 conda 환경에서 실행되며, 환경의 이름은 autobuysell_new이다.


---
# 📌 부록: 개선 제안 사항 (추가 고려 요소)

아래 내용은 AutoBuySell 재설계 문서의 방향성을 유지하면서, 장기 운영 및 전략 확장 시 발생 가능한 문제를 예방하기 위한 **구체적인 보완 제안 사항**이다.

---

## 1) 마켓 데이터 보관 정책 & 저장 스키마

단일 PostgreSQL 사용은 단순성과 유지보수 측면에서 적합하나, **대량 데이터 저장 시 I/O 비용 및 디스크 증가 문제가 발생할 수 있음**.  
데이터 보관 기간(TTL) 또는 rollup 전략을 명시하는 것을 권장.

**권장 테이블 구조 및 TTL 예시**

| 테이블 | 내용 | TTL 제안 |
|--------|------|----------|
| `symbol_daily` | OHLCV 일봉 | 유지 |
| `symbol_intraday` | 1분봉/틱 | 30~90일 후 rollup |
| `market_snapshot` | 실시간 스냅샷 | 7일 후 삭제 |

> 이 정책은 **DB 공간 확보, 조회 비용 감소, 백테스트 데이터 품질 유지**에 기여한다.

---

## 2) 전략 파라미터 버전 관리

자동매매의 핵심은 **전략 변경이 성과에 어떤 영향을 주었는지 추적하는 것**이다.  
백테스트 결과와 전략 파라미터 버전을 연결할 수 있도록 버전 관리 구조를 제안한다.

**제안 스키마**

```text
strategy_meta(id, name, description)
strategy_params(id, strategy_id, version, params_jsonb, created_at)
```

> 나중에 "이 백테스트 결과가 어떤 설정이었더라?" 같은 질문에 즉시 대응 가능.

---

## 3) 리스크 관리 계층 추가

현재 문서는 **전략 확장성**을 고려하고 있으나,  
실거래에서는 **리스크 모델**이 전략 모델만큼 중요하다.

**추가 고려 요소**

- 계좌 전체 포지션 한도 (`max_total_exposure`)
- 종목별 포지션 한도 (`max_symbol_exposure`)
- 일일 손절 한도 (`daily_stop_loss`)
- 전략 단위 포트폴리오 리스크 (`strategy_risk_limit`)

**목적**  
> 돌발적 변동성, 시스템 오류, 전략 실패 등으로부터 계좌를 보호

---

## 4) 주문 실행 경로 명확화 (execution layer)

전략이 직접 브로커 API를 호출하는 구조는  
**추후 브로커 교체/리스크 모듈 삽입 시 유지보수 부담**이 커진다.

**권장 호출 흐름**

```text
strategy → execution_service → broker_adapter → broker API
```

**효과**
- 전략 코드는 "signal 생성"에 집중
- 주문 검증/리스크 체크/로그 분리 용이

> 문서 상단 아키텍처에 간단히 이 흐름을 명시하면, 설계 의도가 훨씬 명확해진다.

---

## 5) 로그 분리 정책 정의

자동매매 특성상 **signal 기록**과 **실제 체결 기록**은 반드시 구분해야 한다.  
그렇지 않으면 백테스트와 실거래 비교에 문제가 생긴다.

**로그 테이블 권장**

| 로그 | 목적 |
|------|-----|
| `signal_log` | 전략이 생성한 매수/매도 신호 |
| `trade_log` | 실제 체결 정보 |
| `broker_log` | 주문 요청 및 응답 |
| `error_log` | 예외 기록 |
| `backtest_log` | 백테스트 메타 정보 |

---

## 6) 문서 한 문단 보완 제안 (붙여넣기용)

> **전략은 직접 주문하지 않으며, 모든 주문은 `execution_service`를 통해 리스크 검증 후 브로커로 위임된다.**  
> **전략 파라미터는 `versioned jsonb` 방식으로 저장되며, 백테스트 결과는 해당 버전과 연결된다.**  
> **signal / trade / broker / error 로그는 분리 보관하여 실거래 검증 및 회귀 분석이 가능하도록 한다.**

---

## 최종 요약

| 개선 요소 | 기대 효과 |
|-----------|-----------|
| 데이터 TTL + rollup | 디스크 절약, 장기 운영성 |
| 파라미터 버전 관리 | 백테스트 재현성 확보 |
| 리스크 계층 | 실거래 안정성 |
| execution layer 추가 | 브로커/전략 독립성 |
| 로그 정책 세분화 | 분석 및 검증 용이 |

---

_이 부록은 기존 문서를 확장하는 형태로 사용 가능하며,  
추후 실제 구현 시 **스키마/디렉토리 구조/예시 코드**로 이어질 수 있다._
