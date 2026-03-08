# AutoBuySell

자동 매매 시스템 (FastAPI + Next.js + PostgreSQL, Docker Compose 기반)

## 빠른 시작

### 1) 환경 변수 준비

```bash
cp .env.example .env
# .env 파일에서 Alpaca 키 수정
```

필수/주요 변수:
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`

### 2) 실행

```bash
docker compose up -d --build
```

### 3) 접속

- Frontend: http://localhost:3000
- API: http://localhost:8000
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## 트레이딩 상태 확인

```bash
curl http://127.0.0.1:8000/api/v1/trading/status
```

## 개발 참고 문서

- 운영 가이드: `docs/SYSTEM_OPERATION.md`
- 거래 내부 구조: `docs/TRADING_INTERNALS.md`
- 검증 문서: `docs/verification/*`

## 보안 주의

- `.env`는 커밋하지 마세요.
- 실계좌 키 대신 Paper Trading 키를 기본 권장합니다.
- 배포 전 리스크/주문 제한 정책을 점검하세요.
