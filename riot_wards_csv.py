"""Export one Riot account's Match-V5 ward statistics and events (Python 3.10+)."""
import argparse
import csv
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


REGIONS = ('americas', 'asia', 'europe', 'sea')
IDENTITY = ['puuid', 'userId', 'participants', 'participantId']
STAT_FIELDS = [
    'detectorWardsPlaced', 'wardsKilled', 'wardsPlaced', 'controlWardsPlaced',
    'stealthWardsPlaced', 'wardTakedownsBefore20M',
    'controlWardTimeCoverageInRiverOrEnemyHalf', 'visionWardsBoughtInGame',
]
CHALLENGE_FIELDS = {
    'controlWardsPlaced', 'stealthWardsPlaced', 'wardTakedownsBefore20M',
    'controlWardTimeCoverageInRiverOrEnemyHalf',
}
EVENT_FIELDS = IDENTITY + ['creatorId', 'killerId', 'matchId', 'riotIdGameName',
                           'riotIdTagline', 'championName', 'lane',
                           'timestamp', 'type', 'wardType']
SUMMARY_FIELDS = IDENTITY + ['matchId', 'riotIdGameName', 'riotIdTagline',
                             'championName', 'lane'] + STAT_FIELDS
TIMELINE_FILENAME = 'ward_timeline.csv'
SUMMARY_FILENAME = 'ward_end_to_end.csv'


class RiotError(RuntimeError):
    pass


def load_env_value(variable, env_path=None):
    """Read environment before the script's .env; values are never executed."""
    value = os.environ.get(variable, '').strip()
    if value:
        return value
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().with_name('.env')
    if not path.exists():
        return ''
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        if separator and name.strip() == variable:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            return value.strip()
    return ''


def load_api_key(env_path=None):
    return load_env_value('RIOT_API_KEY', env_path)


class RiotClient:
    def __init__(self, api_key, region, retries=3):
        self.api_key = api_key
        self.base_url = f'https://{region}.api.riotgames.com'
        self.retries = retries

    def get(self, path, params=None):
        operation = ('ACCOUNT-V1' if path.startswith('/riot/account/') else
                     'MATCH-V5 /replays' if path.endswith('/replays') else
                     'MATCH-V5 /ids' if path.endswith('/ids') else
                     'MATCH-V5 /timeline' if path.endswith('/timeline') else 'MATCH-V5 /match')
        url = self.base_url + path
        if params:
            url += '?' + urlencode(params)
        for attempt in range(self.retries + 1):
            request = Request(url, headers={
                'X-Riot-Token': self.api_key,
                'User-Agent': 'detect-wards/1.0',
                'Accept': 'application/json',
            })
            try:
                with urlopen(request, timeout=30) as response:
                    return json.load(response)
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get('Retry-After', '1')
                body = exc.read(2048)
                cloudflare_block = status == 403 and b'error code: 1010' in body
                exc.close()
                if cloudflare_block:
                    raise RiotError(f'HTTP 403 [{operation}]: Cloudflare 요청 차단(1010). '
                                    'API 키 만료 여부와 별개인 요청 필터 오류입니다.') from None
                if (status == 429 or 500 <= status < 600) and attempt < self.retries:
                    try:
                        delay = max(float(retry_after), 1.0) if status == 429 else 2 ** attempt
                    except ValueError:
                        delay = 2 ** attempt
                    print(f'HTTP {status}: {delay:g}초 후 재시도', file=sys.stderr)
                    time.sleep(delay)
                    continue
                hints = {401: 'RIOT_API_KEY 환경변수를 확인하세요.',
                         403: 'API 키 만료/권한 또는 요청 경로를 확인하세요.',
                         404: '계정/경기 ID와 --region을 확인하세요.',
                         429: '호출 제한입니다. 잠시 후 다시 실행하세요.'}
                message = ''
                try:
                    message = json.loads(body).get('status', {}).get('message', '')
                except (ValueError, AttributeError):
                    pass
                if not isinstance(message, str):
                    message = ''
                message = message.replace(self.api_key, '[REDACTED]')
                message = re.sub(r'RGAPI-[A-Za-z0-9-]+', '[REDACTED]', message)
                message = ' '.join(message.split())[:200]
                detail = f' Riot 응답: {message}' if message else ''
                raise RiotError(f'HTTP {status} [{operation}]: '
                                f'{hints.get(status, "Riot API 요청 실패")}{detail}') from None
            except URLError:
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RiotError('Riot API 연결 실패: 네트워크를 확인하세요.') from None
            except (ValueError, TimeoutError):
                raise RiotError('Riot API 응답이 잘못되었거나 시간이 초과되었습니다.') from None

    def account(self, game_name, tag_line):
        return self.get('/riot/account/v1/accounts/by-riot-id/'
                        + quote(game_name, safe='') + '/' + quote(tag_line, safe=''))

    def match_ids(self, puuid, count, start=0, queue=None):
        result = []
        while len(result) < count:
            size = min(100, count - len(result))
            params = {'start': start + len(result), 'count': size}
            if queue is not None:
                params['queue'] = queue
            page = self.get('/lol/match/v5/matches/by-puuid/'
                            + quote(puuid, safe='') + '/ids', params)
            result.extend(page)
            if len(page) < size:
                break
        return list(dict.fromkeys(result))

    def replay_match_ids(self, puuid, count, start=0):
        response = self.get('/lol/match/v5/matches/by-puuid/'
                            + quote(puuid, safe='') + '/replays')
        urls = response.get('matchFileURLs')
        if not isinstance(urls, list):
            raise RiotError('MATCH-V5 /replays: matchFileURLs 배열이 없습니다.')
        ids = []
        for url in urls:
            if not isinstance(url, str):
                raise RiotError('MATCH-V5 /replays: 잘못된 리플레이 URL입니다.')
            filename = urlsplit(url).path.rsplit('/', 1)[-1]
            match = re.fullmatch(r'([A-Z0-9]+_[0-9]+)\.(?:replay|rofl)', filename)
            if match is None:
                raise RiotError('MATCH-V5 /replays: URL에서 경기 ID를 식별할 수 없습니다.')
            ids.append(match.group(1))
        return list(dict.fromkeys(ids))[start:start + count]


def extract_match(match, timeline, puuid, user_id):
    """Resolve the participant afresh in every match; never assume a fixed slot."""
    match_id = match['metadata']['matchId']
    if timeline['metadata']['matchId'] != match_id:
        raise RiotError('경기 상세와 타임라인의 matchId가 다릅니다.')
    participants = match['info']['participants']
    targets = [p for p in participants if p.get('puuid') == puuid]
    if len(targets) != 1:
        raise RiotError(f'{match_id}: 대상 PUUID 참가자를 하나로 식별할 수 없습니다.')
    target = targets[0]
    participant_id = target['participantId']
    if not isinstance(participant_id, int) or isinstance(participant_id, bool) or participant_id <= 0:
        raise RiotError(f'{match_id}: 잘못된 participantId입니다.')
    roster = [{'matchId': match_id, 'participantId': p['participantId'],
               'championName': p.get('championName')} for p in participants]
    participant_json = json.dumps(
        [{key: p[key] for key in ('participantId', 'championName')} for p in roster],
        ensure_ascii=False, separators=(',', ':'),
    )
    base = {'puuid': puuid, 'userId': user_id, 'participants': participant_json,
            'participantId': participant_id,
            'matchId': match_id, 'riotIdGameName': target.get('riotIdGameName'),
            'riotIdTagline': target.get('riotIdTagline'),
            'championName': target.get('championName'), 'lane': target.get('lane')}
    challenges = target.get('challenges') or {}
    summary = dict(base)
    for name in STAT_FIELDS:
        source = challenges if name in CHALLENGE_FIELDS else target
        # Some versions expose a challenge at participant level. Preserve 0.
        summary[name] = source.get(name, target.get(name))
    events = []
    for frame in timeline['info']['frames']:
        for event in frame.get('events', []):
            kind = event.get('type')
            actor_field = {'WARD_PLACED': 'creatorId', 'WARD_KILL': 'killerId'}.get(kind)
            if actor_field is None or event.get(actor_field) != participant_id:
                continue
            row = {key: summary.get(key) for key in EVENT_FIELDS}
            for key in ('creatorId', 'killerId', 'timestamp', 'type', 'wardType'):
                row[key] = event.get(key)
            events.append(row)
    events.sort(key=lambda row: row['timestamp'])
    return summary, events, roster


def export(client, puuid, user_id, match_ids, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental overwrite of earlier exports.
    specs = [(TIMELINE_FILENAME, EVENT_FIELDS), (SUMMARY_FILENAME, SUMMARY_FIELDS)]
    if any((output / name).exists() for name, _ in specs):
        raise RiotError('출력 CSV가 이미 있습니다. 새로운 --output 폴더를 지정하세요.')
    with ExitStack() as stack:
        writers = []
        for name, fields in specs:
            handle = stack.enter_context((output / name).open('x', encoding='utf-8-sig', newline=''))
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writers.append(writer)
        for index, match_id in enumerate(match_ids, 1):
            print(f'[{index}/{len(match_ids)}] {match_id}', file=sys.stderr)
            path = '/lol/match/v5/matches/' + quote(match_id, safe='')
            match = client.get(path)
            timeline = client.get(path + '/timeline')
            summary, events, _ = extract_match(match, timeline, puuid, user_id)
            writers[0].writerows(events)
            # Exactly one final-stat row per match, even if no events occurred.
            writers[1].writerow(summary)


def main(argv=None):
    parser = argparse.ArgumentParser(description='한 유저의 Riot Match-V5 와드 데이터를 CSV로 저장')
    parser.add_argument('--region', choices=REGIONS,
                        help='NA: americas, KR/JP: asia, 유럽: europe (기본: americas)')
    parser.add_argument('--puuid', help='지정하면 Riot ID → PUUID 조회를 생략')
    parser.add_argument('--game-name', help='계정 이름 (기본: RIOT_GAME_NAME)')
    parser.add_argument('--tag-line', help='태그 (기본: RIOT_TAG_LINE)')
    parser.add_argument('--user-id', help='CSV userId 값 (기본: gameName#tagLine 또는 PUUID)')
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--start', type=int, default=0)
    parser.add_argument('--queue', type=int, help='예: 솔로 랭크 420')
    parser.add_argument('--match-source', choices=('replays', 'ids'),
                        help='경기 목록 출처 (기본: .env RIOT_MATCH_SOURCE 또는 replays)')
    parser.add_argument('--diagnose', action='store_true', help='설정 위치와 실행 환경 확인 후 종료 (API 호출 없음)')
    parser.add_argument('--output', type=Path,
                        help='출력 폴더 (기본: 프로젝트의 output/riot_wards_실행시각)')
    args = parser.parse_args(argv)
    if args.count <= 0 or args.start < 0:
        parser.error('--count는 양수, --start는 0 이상이어야 합니다.')
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%fZ')
        args.output = Path(__file__).resolve().parent / 'output' / f'riot_wards_{stamp}'
    try:
        api_key = load_api_key()
        args.game_name = args.game_name or load_env_value('RIOT_GAME_NAME')
        args.tag_line = args.tag_line or load_env_value('RIOT_TAG_LINE')
        args.region = args.region or load_env_value('RIOT_REGION') or 'americas'
        args.match_source = args.match_source or load_env_value('RIOT_MATCH_SOURCE') or 'replays'
    except OSError:
        parser.error('프로젝트 .env 파일을 읽을 수 없습니다.')
    if not api_key:
        parser.error('프로젝트 .env 또는 환경변수에 RIOT_API_KEY를 설정하세요.')
    if not args.puuid and (not args.game_name or not args.tag_line):
        parser.error('.env의 RIOT_GAME_NAME과 RIOT_TAG_LINE을 설정하세요.')
    if args.region not in REGIONS:
        parser.error('RIOT_REGION은 americas, asia, europe, sea 중 하나여야 합니다.')
    if args.match_source not in ('replays', 'ids'):
        parser.error('RIOT_MATCH_SOURCE는 replays 또는 ids여야 합니다.')
    if args.queue is not None and args.match_source != 'ids':
        parser.error('--queue는 --match-source ids와 함께 사용하세요.')
    print(f'스크립트: {Path(__file__).resolve()}', file=sys.stderr)
    print(f'설정 파일: {Path(__file__).resolve().with_name(".env")}', file=sys.stderr)
    key_source = '터미널 환경변수' if os.environ.get('RIOT_API_KEY', '').strip() else '.env'
    print(f'API 키 출처: {key_source} | 지역: {args.region} | 경기 목록: {args.match_source}', file=sys.stderr)
    if args.diagnose:
        print(f'Python: {sys.executable}')
        if key_source == '터미널 환경변수':
            print('환경변수가 .env보다 우선합니다. .env를 사용하려면 unset RIOT_API_KEY를 실행하세요.')
        return 0
    exporting = False
    try:
        client = RiotClient(api_key, args.region)
        if args.puuid:
            puuid = args.puuid
            user_id = args.user_id or puuid
        else:
            print('[ACCOUNT-V1] Riot ID → PUUID 조회', file=sys.stderr)
            account = client.account(args.game_name, args.tag_line)
            puuid = account['puuid']
            user_id = args.user_id or f'{account["gameName"]}#{account["tagLine"]}'
        print(f'[MATCH-V5 /{args.match_source}] 경기 목록 조회', file=sys.stderr)
        if args.match_source == 'replays':
            ids = client.replay_match_ids(puuid, args.count, args.start)
        else:
            ids = client.match_ids(puuid, args.count, args.start, args.queue)
        print(f'조회된 경기: {len(ids)}개', file=sys.stderr)
        exporting = True
        export(client, puuid, user_id, ids, args.output)
    except (RiotError, OSError, KeyError, TypeError) as exc:
        print(f'실패: {exc}', file=sys.stderr)
        if exporting:
            print('출력 CSV 두 개에 완료한 경기만 남을 수 있습니다.', file=sys.stderr)
        else:
            print('CSV 저장 전에 실패했습니다.', file=sys.stderr)
        return 1
    print(f'완료: {len(ids)}경기 → {args.output.resolve()}')
    print(f'타임라인: {TIMELINE_FILENAME} | 종합 통계: {SUMMARY_FILENAME}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
