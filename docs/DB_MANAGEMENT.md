# Database Management Scripts

AutoBuySell 프로젝트의 데이터베이스 관리 스크립트 모음입니다.

## 📁 디렉토리 구조

```
api/scripts/
├── init_db.py             # DB 초기화 (Python)
├── backup_db.py           # DB 백업 (Python - 로컬 전용)
├── restore_db.py          # DB 복원 (Python - 로컬 전용)
└── manage_strategies.py   # 전략 관리 (추가/수정/조회/삭제)

ROOT/
├── backup_db.sh        # DB 백업 (Shell - Docker용)
└── restore_db.sh       # DB 복원 (Shell - Docker용)

api/backups/            # 백업 파일 저장 디렉토리
└── .gitignore          # 백업 파일은 git에서 제외
```

---

## 🚀 사용법

### 1. 데이터베이스 초기화

모든 테이블을 생성하고 초기 데이터를 입력합니다.

#### Docker 환경:
```bash
docker exec autobuysell_api python scripts/init_db.py
```

#### 로컬 환경:
```bash
cd api
python scripts/init_db.py
```

#### 전체 초기화 (기존 데이터 삭제):
```bash
docker exec autobuysell_api python scripts/init_db.py --reset
```

**주의**: `--reset` 옵션은 모든 데이터를 삭제합니다!

---

### 2. 데이터베이스 백업

#### Docker 환경 (권장):
```bash
./backup_db.sh
```

백업 파일은 `api/backups/autobuysell_YYYY-MM-DD_HH-MM-SS.sql.gz` 형식으로 저장됩니다.

#### 로컬 환경 (pg_dump 필요):
```bash
cd api
python scripts/backup_db.py
```

**참고**: Docker 환경에서는 Shell 스크립트 사용을 권장합니다.

---

### 3. 데이터베이스 복원

#### Docker 환경 (권장):
```bash
# 확인 프롬프트 포함
./restore_db.sh api/backups/autobuysell_2026-01-03_18-00-00.sql.gz

# 확인 없이 복원
./restore_db.sh api/backups/autobuysell_2026-01-03_18-00-00.sql.gz --force
```

#### 로컬 환경 (psql 필요):
```bash
cd api
python scripts/restore_db.py backups/autobuysell_2026-01-03_18-00-00.sql.gz
python scripts/restore_db.py --force backups/autobuysell_2026-01-03_18-00-00.sql.gz
```

**⚠️  주의**: 복원은 기존 데이터에 추가되므로, 완전한 복원을 위해서는 먼저 `--reset`으로 초기화 후 복원해야 합니다.

#### 완전한 복원 예제:
```bash
# 1. DB 초기화 (모든 데이터 삭제)
docker exec -it autobuysell_api python scripts/init_db.py --reset

# 2. 백업에서 복원
./restore_db.sh api/backups/autobuysell_2026-01-03_18-00-00.sql.gz --force
```

---

### 4. 전략 관리

전략을 등록, 수정, 조회, 삭제할 수 있는 통합 관리 스크립트입니다.

#### 전략 목록 조회
```bash
docker exec autobuysell_api python scripts/manage_strategies.py list
```

#### 새로운 전략 추가
```bash
docker exec autobuysell_api python scripts/manage_strategies.py add \
  "SMA_Strategy" \
  "app.strategies.sma.SMAStrategy" \
  --description "Simple Moving Average Strategy"
```

#### 전략 파라미터 업데이트
```bash
# 기본 파라미터 추가 (모든 심볼에 적용)
docker exec autobuysell_api python scripts/manage_strategies.py update \
  "MeanReversion_v1" \
  '{"timeframe":"30Min","duration":24,"thr_buy":0.05,"thr_sell":0.05}' \
  --active

# 특정 심볼용 파라미터 추가
docker exec autobuysell_api python scripts/manage_strategies.py update \
  "MeanReversion_v1" \
  '{"timeframe":"15Min","duration":48,"thr_buy":0.03}' \
  --symbol AAPL \
  --active
```

#### 전략 삭제
```bash
docker exec autobuysell_api python scripts/manage_strategies.py delete "SMA_Strategy"
docker exec autobuysell_api python scripts/manage_strategies.py delete "SMA_Strategy" --force
```

---

## 📊 백업 파일 관리

### 자동 생성되는 파일명 형식
```
autobuysell_2026-01-03_18-00-00.sql.gz
          └─ 타임스탬프: YYYY-MM-DD_HH-MM-SS
```

### 저장 위치
- Docker: `./api/backups/`
- 로컬: `api/backups/`

### Git 관리
백업 파일은 `.gitignore`에 의해 자동으로 제외되므로 저장소에 커밋되지 않습니다.

---

## ⚙️ 기술 세부사항

### Python 스크립트 (`api/scripts/`)
- **init_db.py**: SQLAlchemy를 사용하여 테이블 생성 및 기본 데이터 시딩
- **backup_db.py**: `pg_dump` 명령어 필요 (Docker 환경에서는 작동하지 않음)
- **restore_db.py**: `psql` 명령어 필요 (Docker 환경에서는 작동하지 않음)
- **manage_strategies.py**: 전략 등록/수정/조회/삭제 (버전 관리 포함)

### Shell 스크립트 (루트 디렉토리)
- **backup_db.sh**: Docker 컨테이너에서 `pg_dump` 실행
- **restore_db.sh**: Docker 컨테이너에서 `psql` 실행

### 데이터베이스 구성
- **DBMS**: PostgreSQL 16
- **컨테이너**: `autobuysell_db`
- **사용자**: `autosys`
- **데이터베이스**: `autosys`

---

## 🔧 트러블슈팅

### "pg_dump: command not found" 오류
Docker 환경에서는 Shell 스크립트(`backup_db.sh`)를 사용하세요.

### "psql: command not found" 오류
Docker 환경에서는 Shell 스크립트(`restore_db.sh`)를 사용하세요.

### "relation already exists" 오류 (복원 시)
정상적인 메시지입니다. 기존 데이터가 있는 상태에서 복원하면 발생합니다.
완전한 복원을 원하면 먼저 `init_db.py --reset`으로 초기화하세요.

### 백업 파일이 너무 큰 경우
`.gz` 압축이 자동으로 적용되므로 용량이 크게 줄어듭니다.
추가 압축이 필요한 경우 백업 파일을 수동으로 압축할 수 있습니다.

---

## 📝 Best Practices

1. **정기 백업**: 중요한 작업 전에는 항상 백업을 생성하세요
2. **백업 보관**: 백업 파일을 안전한 외부 저장소에도 보관하세요
3. **복원 테스트**: 백업이 제대로 작동하는지 주기적으로 테스트하세요
4. **버전 관리**: 여러 시점의 백업을 유지하세요
