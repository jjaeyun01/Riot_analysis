# Riot Wards API

Riot ACCOUNT-V1 / MATCH-V5로 지정한 유저의 와드 설치·제거 타임라인과 경기 종료 통계를 CSV 두 개로 저장합니다.

처음 실행하기 전에 `.env.example`을 `.env`로 복사하세요:

```bash
cp .env.example .env
```



`riot_wards_csv.py`는 리플레이 파일 대신 ACCOUNT-V1 / MATCH-V5 API를 사용합니다.
Python 3.10 이상에서 추가 패키지 없이 실행할 수 있습니다.

가장 간단한 실행: 프로젝트의 `.env`를 열어 API 키, 대상 계정 이름과 태그,
지역을 입력하고 저장하세요. 태그에는 `#`를 붙이지 않습니다. `.env`는 Git 제외 대상입니다.

```dotenv
RIOT_API_KEY=여기에_실제_API_키
RIOT_GAME_NAME=Example Player
RIOT_TAG_LINE=TAG
RIOT_REGION=americas
RIOT_MATCH_SOURCE=replays
```

```bash
python3 riot_wards_csv.py
```

위 설정은 `Example Player#TAG`, 북미의 `/replays` 목록에서 최대 20경기를 조회합니다.
리플레이가 5개 반환되면 5경기만 추출합니다. `matchFileURLs`의 파일명에서
`NA1_…` 경기 ID를 추출하며 서명된 URL이나 리플레이 파일을 저장·다운로드하지 않습니다.
더 많은 경기 기록이 필요하면 `RIOT_MATCH_SOURCE=ids`로 바꾸세요.
다른 사람을 추출하려면 `.env`의 `RIOT_GAME_NAME`, `RIOT_TAG_LINE`을 바꾸고 같은 명령을 실행하세요.
한국/일본 계정은 `RIOT_REGION=asia`로 설정합니다.
기본 출력 폴더는 프로젝트의 `output/riot_wards_실행시각`이며 실행마다 새로 생성합니다.
`.env`는 현재 터미널 위치와 관계없이 스크립트 옆에서 찾습니다.
설정 우선순위는 명령행 옵션 → 환경변수 → `.env` → 기본값입니다.
대상 계정 이름과 태그는 필수입니다. 지역 설정이 없으면 `americas`를 사용합니다.

환경변수 직접 입력과 옵션 지정도 가능합니다:

```bash
# zsh: 입력한 키를 화면이나 명령 기록에 남기지 않고 환경변수로 설정
read -s 'RIOT_API_KEY?Riot API key: '; echo
export RIOT_API_KEY
python3 riot_wards_csv.py --game-name 'Example Player' --tag-line TAG \
  --region americas --count 20 --output output/barking_dog
```

PUUID가 이미 있으면 계정 조회를 생략할 수 있습니다. 아래 PUUID 자리에는 실제 값을 넣으세요.

```bash
python3 riot_wards_csv.py --puuid 'YOUR_PUUID' --user-id 'Example Player#TAG' \
  --region americas --count 20 --output output/barking_dog_puuid
```

- `--region`: 북미 `americas`, 한국/일본 `asia`, 유럽 `europe`, 동남아 `sea`.
  선택한 지역의 경기만 조회합니다. 기본값은 북미입니다.
- `--count`: 최근 경기 수(기본 20), `--start`: 건너뛸 경기 수(기본 0).
  `/ids` 모드에서 100경기 초과 시 자동으로 페이지를 나눕니다.
  `--match-source ids --queue 420`으로 솔로 랭크를 필터링할 수 있습니다.
- API 키는 `RIOT_API_KEY` 환경변수 또는 프로젝트의 `.env`에서 읽습니다. 코드/CSV에 저장하지 않습니다.
- `--puuid`를 쓰면 `userId`는 기본적으로 PUUID이고, `--user-id`로 바꿀 수 있습니다.
  Riot ID 조회 모드에서는 `gameName#tagLine`이 기본값입니다.
  `userId`는 분석용 계정 라벨이며 Riot API가 반환하는 별도 ID 필드가 아닙니다.
  이 CLI는 지정한 계정을 조회하며 로그인 세션을 자동으로 인식하지 않습니다.

실행할 때마다 타임라인과 경기 종료 종합 통계를 **CSV 두 개로 나누어** 생성합니다.
Excel에서 한글을 읽을 수 있는 UTF-8 BOM CSV입니다.

| 파일 | 행 단위 | 내용 |
|---|---|---|
| `ward_timeline.csv` | 대상 유저의 와드 이벤트 1건 | 기존 A~N열의 14개 컬럼. 계정·경기·챔피언·라인과 `creatorId,killerId,timestamp,type,wardType` |
| `ward_end_to_end.csv` | 대상 유저 × 경기당 1행 | 계정·경기·참가자 식별 정보 9개 + 기존 O~V열의 최종 와드 통계 8개 |

`puuid`·`userId`로 유저를, `matchId`·`participantId`로 경기 안의 참가자를 구분합니다.
두 파일을 연결하려면 `puuid`·`userId`·`matchId`·`participantId`를 함께 사용하세요.
와드 이벤트가 없는 경기는 타임라인에 가짜 이벤트 행을 만들지 않고 종합 통계에만 한 행을 저장합니다.
기존에 저장된 CSV와 JSON은 그대로 보존합니다.

`participants` 컬럼에는 `[{"participantId":2,"championName":"Lux"},…]` 형태의
JSON 배열을 넣습니다. CSV에서 한 셀로 읽은 뒤 `json.loads()`로 복원할 수 있습니다.
Riot 응답의 `metadata.participants`는 PUUID 목록이며, 여기의 컬럼은 요청한
챔피언 정보를 연결하기 위해 `info.participants`에서 ID·챔피언만 추린 목록입니다.
최종 와드 통계는 종합 CSV에만 경기당 한 번 저장합니다. 타임라인에는 종합 통계를 반복하지 않습니다.
중복 기재한 `controlWardsPlaced`는 하나의 컬럼으로 저장합니다.

경기마다 `info.participants[].puuid`로 대상 유저를 찾아 `participantId`를 결정합니다.
`WARD_PLACED`는 `creatorId`, `WARD_KILL`은 `killerId`가 이 ID와 일치할 때만 저장합니다.
각 이벤트에 없는 ID는 빈칸입니다. **와드를 제거한 이벤트의 creatorId를 추정하거나
다른 설치 이벤트와 연결하지 않습니다.** `timestamp`는 게임 시작 이후 밀리초입니다.
와드 이벤트가 없는 경기도 통계 행은 저장합니다.

통계 원본 경로:

- `info.participants[]`: `detectorWardsPlaced`, `wardsKilled`, `wardsPlaced`, `visionWardsBoughtInGame`.
- `info.participants[].challenges`: `controlWardsPlaced`, `stealthWardsPlaced`,
  `wardTakedownsBefore20M`, `controlWardTimeCoverageInRiverOrEnemyHalf`.
  해당 challenge가 없으면 같은 이름의 participant 필드를 확인합니다.
- 필드가 없으면 빈칸으로 기록하고, 실제 `0`은 그대로 보존합니다.
  `detectorWardsPlaced`와 `controlWardsPlaced`는 각각의 원본 값을 유지합니다.

HTTP 429는 `Retry-After`만큼 대기하고 재시도합니다. 서버/연결 오류도 최대 3회 재시도하며,
인증 오류는 즉시 종료합니다. 중간 실패하면 이전에 완료한 경기의 CSV가 남을 수 있고
종료 코드는 1입니다.
재실행은 새로운 출력 폴더를 지정하세요. 기존 결과는 덮어쓰지 않습니다.

403 오류는 실패한 API 단계와 Riot의 JSON 오류 메시지를 함께 표시합니다.
Cloudflare 1010 차단도 별도로 표시하며, 403을 자동 재시도하지 않습니다.
`python3 riot_wards_csv.py --diagnose`로 실제 스크립트/설정 파일 위치,
키 출처(값은 표시하지 않음), Python 경로를 API 호출 없이 확인할 수 있습니다.

API 호출 순서: Riot ID → PUUID(선택) → `/replays`의 파일명에서 경기 ID 추출
(또는 `/ids`) → 각 경기 상세 → 각 경기 타임라인.
공식 문서: [ACCOUNT-V1 / MATCH-V5](https://developer.riotgames.com/apis),
[계정 식별자와 지역](https://developer.riotgames.com/docs/lol),
[오류 및 호출 제한](https://developer.riotgames.com/docs/portal).

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

이 프로젝트는 Riot Games가 보증하거나 후원하지 않습니다. League of Legends와 관련 자산의 권리는 Riot Games에 있습니다.
