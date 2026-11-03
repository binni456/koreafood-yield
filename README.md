# 코리아식품 수율 일지

(주)코리아식품 현장에서 원료육 출고부터 포장까지 무게를 직접 입력하면 공정별 수율을 계산하고, 작업지시서·작업일지·생산일지를 자동으로 만드는 웹 프로그램입니다. 영남이공대 수율 자동계산 과제의 결과물입니다.

## 주요 기능
- 현장 입력: 원료 입고 → ① 원료육 출고 → ② 세척 중량 → ③ 가열(가열 제품만) → ④ 포장 수량·잔량·폐기
- 수율 자동 계산과 입력 오류 경고 (세척 수율 = 세척 후 ÷ 해동 후, 포장 수율 = (포장+잔량+폐기) ÷ 세척 등)
- 자동 생성 문서: 작업지시서, 작업일지, 생산일지, 제품 1차 수량 확인표, 원료 수불 대장 (인쇄·엑셀 저장)
- 관리자 화면: 제품별 수율, 손실 금액, 원료·작업자별 비교, 진행 현황, 주간·월간 보고서, 수기 기록 대조, 변경 기록
- 아이디·비밀번호 로그인(현장/관리자 역할), 사업장 구분(코리아식품·하비랑), 태블릿·휴대폰 화면 대응, 연결이 끊겨도 기기에 보관 후 자동 저장

## 폴더 구성
| 경로 | 내용 |
|---|---|
| `server/` | 사내 서버판. 회사 PC 한 대에서 실행하면 같은 와이파이의 태블릿이 접속 |
| `server/server.py` | 서버 프로그램 (파이썬 3.8+ 표준 라이브러리만 사용, SQLite 저장, 실시간 반영, 자동 백업) |
| `server/public/` | 화면 파일 (`index.html`, 서버 연결부 `shim.js`, 엑셀 라이브러리) |
| `server/INSTALL-GUIDE.html` | 회사용 설치 안내 |
| `src/index.html` | 화면 원본 (claude.ai 아티팩트 버전과 같은 코드) |

## 실행
```bash
cd server
python server.py        # 윈도우는 server.py 를 두 번 클릭
```
브라우저에서 `http://localhost:8080` 을 열고 처음 설정에서 관리자 아이디와 비밀번호를 정합니다. 같은 와이파이의 태블릿은 서버 창에 나오는 주소(예: `http://192.168.0.15:8080`)로 접속합니다. 자세한 설치 방법은 `server/INSTALL-GUIDE.html` 을 보세요.

| 명령 | 하는 일 |
|---|---|
| `python server.py --autostart` | 윈도우에서 PC를 켤 때 서버 자동 실행 등록 |
| `python server.py --reset-password 아이디 새비밀번호` | 비밀번호 재설정 (관리자 비밀번호 분실 시) |

## 화면 수정 후 다시 만들기
화면은 `src/index.html` 한 파일에서 고친 뒤 서버용으로 변환합니다.
```bash
python server/build.py src/index.html                          # server/public/index.html
python server/build.py src/index.html --inline --out 경로.html  # 스크립트를 한 파일에 넣은 메일 전송용
```

## 데이터
입력 데이터(`server/data/`)와 백업(`server/backup/`)은 저장소에 올리지 않습니다 (`.gitignore`).

## 사용한 라이브러리
- [SheetJS (xlsx) 0.18.5](https://sheetjs.com/) — Apache License 2.0 (`server/public/vendor/xlsx.full.min.js`)
