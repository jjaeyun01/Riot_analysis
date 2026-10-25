import copy
import csv
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from riot_wards_csv import RiotClient, RiotError, export, extract_match, load_api_key, load_env_value, main


def fixture(slot=7):
    match = {'metadata': {'matchId': 'NA1_123'}, 'info': {'participants': [
        {'puuid': 'other', 'participantId': 2, 'championName': 'Lux', 'wardsPlaced': 99},
        {'puuid': 'target', 'participantId': slot, 'championName': 'Ahri',
         'riotIdGameName': 'Example Player', 'riotIdTagline': 'TAG', 'lane': 'MIDDLE',
         'wardsPlaced': 3, 'wardsKilled': 1, 'detectorWardsPlaced': 2,
         'challenges': {'controlWardsPlaced': 0, 'stealthWardsPlaced': 3,
                        'controlWardTimeCoverageInRiverOrEnemyHalf': 0.25}},
    ]}}
    timeline = {'metadata': {'matchId': 'NA1_123'}, 'info': {'frames': [{'events': [
        {'type': 'WARD_KILL', 'killerId': slot, 'timestamp': 200, 'wardType': 'SIGHT_WARD'},
        {'type': 'WARD_PLACED', 'creatorId': 2, 'timestamp': 100, 'wardType': 'CONTROL_WARD'},
        {'type': 'WARD_PLACED', 'creatorId': slot, 'timestamp': 50, 'wardType': 'YELLOW_TRINKET'},
        # Wrong actor fields must not admit another user's action.
        {'type': 'WARD_PLACED', 'creatorId': 2, 'killerId': slot, 'timestamp': 300},
        {'type': 'WARD_KILL', 'killerId': 2, 'creatorId': slot, 'timestamp': 400},
        {'type': 'CHAMPION_KILL', 'killerId': slot, 'timestamp': 500},
        {'type': 'WARD_KILL', 'killerId': 0, 'timestamp': 600},
    ]}]}}
    return match, timeline


class ExtractionTests(unittest.TestCase):
    def test_all_requested_fields_present_together_in_event_csv(self):
        match, timeline = fixture()
        target = match['info']['participants'][1]
        target['visionWardsBoughtInGame'] = 4
        target['challenges']['wardTakedownsBefore20M'] = 1
        client = RiotClient('fake', 'americas')
        with TemporaryDirectory() as folder, patch.object(client, 'get', side_effect=[match, timeline]):
            export(client, 'target', 'Example Player#TAG', ['NA1_123'], folder)
            with (Path(folder) / 'merged_wards.csv').open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(reader.fieldnames[:6],
                                 ['puuid', 'userId', 'participants', 'participantId', 'creatorId', 'killerId'])
                rows = list(reader)
        expected = {
            'riotIdGameName': 'Example Player', 'championName': 'Ahri', 'lane': 'MIDDLE',
            'detectorWardsPlaced': '2', 'wardsKilled': '1', 'wardsPlaced': '3',
            'controlWardsPlaced': '0', 'stealthWardsPlaced': '3',
            'wardTakedownsBefore20M': '1', 'controlWardTimeCoverageInRiverOrEnemyHalf': '0.25',
            'visionWardsBoughtInGame': '4',
        }
        for row in rows:
            for key, value in expected.items():
                self.assertEqual(row[key], value)
            self.assertEqual(json.loads(row['participants']),
                             [{'participantId': 2, 'championName': 'Lux'},
                              {'participantId': 7, 'championName': 'Ahri'}])
        self.assertEqual(rows[0]['type'], 'WARD_PLACED')
        self.assertEqual(rows[0]['wardType'], 'YELLOW_TRINKET')
        self.assertEqual(rows[0]['timestamp'], '50')
        self.assertEqual(rows[1]['type'], 'WARD_KILL')

    def test_only_target_actions_correct_actor_and_no_mutation(self):
        match, timeline = fixture()
        original = copy.deepcopy((match, timeline))
        summary, events, roster = extract_match(match, timeline, 'target', 'Example Player#TAG')
        self.assertEqual(summary['wardsPlaced'], 3)
        self.assertEqual(summary['controlWardsPlaced'], 0)
        self.assertEqual(summary['controlWardTimeCoverageInRiverOrEnemyHalf'], .25)
        self.assertIsNone(summary['wardTakedownsBefore20M'])
        self.assertEqual([e['timestamp'] for e in events], [50, 200])
        self.assertEqual(events[0]['creatorId'], 7)
        self.assertIsNone(events[0]['killerId'])
        self.assertEqual(events[1]['killerId'], 7)
        self.assertIsNone(events[1]['creatorId'])
        self.assertEqual(roster, [{'matchId': 'NA1_123', 'participantId': 2, 'championName': 'Lux'},
                                 {'matchId': 'NA1_123', 'participantId': 7, 'championName': 'Ahri'}])
        self.assertEqual((match, timeline), original)

    def test_slot_changes_per_match(self):
        for slot in (1, 9):
            summary, events, _ = extract_match(*fixture(slot), 'target', 'account')
            self.assertEqual(summary['participantId'], slot)
            self.assertEqual(len(events), 2)
            self.assertTrue(all(e['participantId'] == slot for e in events))

    def test_missing_target_and_mismatched_match_rejected(self):
        match, timeline = fixture()
        with self.assertRaises(RiotError):
            extract_match(match, timeline, 'absent', 'account')
        timeline['metadata']['matchId'] = 'NA1_999'
        with self.assertRaises(RiotError):
            extract_match(match, timeline, 'target', 'account')

    def test_export_csv_roundtrip_and_no_overwrite(self):
        match, timeline = fixture()
        client = RiotClient('fake', 'americas')
        with TemporaryDirectory() as folder, patch.object(client, 'get', side_effect=[match, timeline]):
            export(client, 'target', '한글,계정', ['NA1_123'], folder)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ['merged_wards.csv'])
            with (Path(folder) / 'merged_wards.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['userId'], '한글,계정')
            self.assertEqual(rows[0]['wardsPlaced'], '3')
            self.assertEqual(json.loads(rows[0]['participants'])[0],
                             {'participantId': 2, 'championName': 'Lux'})
            with self.assertRaises(RiotError):
                export(client, 'target', 'account', [], folder)

    def test_zero_events_still_has_summary(self):
        match, timeline = fixture()
        timeline['info']['frames'] = []
        summary, events, _ = extract_match(match, timeline, 'target', 'account')
        self.assertEqual(summary['wardsPlaced'], 3)
        self.assertEqual(events, [])
        client = RiotClient('fake', 'americas')
        with TemporaryDirectory() as folder, patch.object(client, 'get', side_effect=[match, timeline]):
            export(client, 'target', 'account', ['NA1_123'], folder)
            with (Path(folder) / 'merged_wards.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['wardsPlaced'], '3')
            self.assertEqual(rows[0]['wardTakedownsBefore20M'], '')
            for field in ('creatorId', 'killerId', 'timestamp', 'type', 'wardType'):
                self.assertEqual(rows[0][field], '')

    def test_multiple_matches_keep_their_own_stats_and_actor_ids(self):
        first, first_timeline = fixture(slot=7)
        second, second_timeline = fixture(slot=9)
        second['metadata']['matchId'] = second_timeline['metadata']['matchId'] = 'NA1_456'
        second['info']['participants'][1]['wardsPlaced'] = 8
        client = RiotClient('fake', 'americas')
        with TemporaryDirectory() as folder, patch.object(client, 'get',
                side_effect=[first, first_timeline, second, second_timeline]):
            export(client, 'target', 'account', ['NA1_123', 'NA1_456'], folder)
            with (Path(folder) / 'merged_wards.csv').open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)
        for row in rows:
            expected_slot, expected_wards = ('7', '3') if row['matchId'] == 'NA1_123' else ('9', '8')
            self.assertEqual(row['participantId'], expected_slot)
            self.assertEqual(row['wardsPlaced'], expected_wards)
            actor = 'creatorId' if row['type'] == 'WARD_PLACED' else 'killerId'
            self.assertEqual(row[actor], expected_slot)

    def test_empty_match_list_writes_only_header(self):
        with TemporaryDirectory() as folder:
            export(RiotClient('fake', 'americas'), 'target', 'account', [], folder)
            with (Path(folder) / 'merged_wards.csv').open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle)
                self.assertIn('participants', reader.fieldnames)
                self.assertEqual(list(reader), [])


class EnvTests(unittest.TestCase):
    def test_account_settings_from_file(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            path = Path(folder) / '.env'
            path.write_text('RIOT_GAME_NAME="다른 유저"\nRIOT_TAG_LINE=KR1\nRIOT_REGION=asia\n')
            self.assertEqual(load_env_value('RIOT_GAME_NAME', path), '다른 유저')
            self.assertEqual(load_env_value('RIOT_TAG_LINE', path), 'KR1')
            self.assertEqual(load_env_value('RIOT_REGION', path), 'asia')

    def test_main_uses_config_and_cli_overrides(self):
        settings = {'RIOT_API_KEY': 'fake', 'RIOT_GAME_NAME': '다른 유저',
                    'RIOT_TAG_LINE': 'KR1', 'RIOT_REGION': 'asia'}
        for argv, expected in [([], ('다른 유저', 'KR1', 'asia')),
                               (['--game-name', 'Override', '--tag-line', 'NA1',
                                 '--region', 'americas'], ('Override', 'NA1', 'americas'))]:
            with patch('riot_wards_csv.load_env_value', side_effect=lambda key, env_path=None: settings.get(key, '')), \
                    patch('riot_wards_csv.RiotClient') as factory, \
                    patch('riot_wards_csv.export'):
                client = factory.return_value
                client.account.return_value = {'puuid': 'target', 'gameName': expected[0],
                                               'tagLine': expected[1]}
                client.match_ids.return_value = []
                client.replay_match_ids.return_value = []
                self.assertEqual(main(argv), 0)
                factory.assert_called_once_with('fake', expected[2])
                client.account.assert_called_once_with(expected[0], expected[1])

    def test_env_file_and_environment_precedence(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(load_api_key(path), '')
                path.write_text('# comment\nOTHER=ignored\nexport RIOT_API_KEY="fake-key"\n')
                self.assertEqual(load_api_key(path), 'fake-key')
            with patch.dict('os.environ', {'RIOT_API_KEY': 'existing-key'}, clear=True):
                self.assertEqual(load_api_key(path), 'existing-key')

    def test_values_are_literal(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            path = Path(folder) / '.env'
            path.write_text("RIOT_API_KEY='$(echo do-not-execute)'\n")
            self.assertEqual(load_api_key(path), '$(echo do-not-execute)')


class ClientTests(unittest.TestCase):
    def test_replay_ids_from_signed_urls_are_unique_and_paginated(self):
        client = RiotClient('fake', 'americas')
        response = {'total': 3, 'matchFileURLs': [
            'https://example.com/NA1_123.replay?signature=secret',
            'https://example.com/NA1_123.replay?signature=other',
            'https://example.com/NA1_456.rofl?signature=secret',
        ]}
        with patch.object(client, 'get', return_value=response) as get:
            self.assertEqual(client.replay_match_ids('a/b', 20), ['NA1_123', 'NA1_456'])
            self.assertEqual(client.replay_match_ids('a/b', 1, start=1), ['NA1_456'])
        self.assertTrue(get.call_args.args[0].endswith('/a%2Fb/replays'))

    def test_empty_or_invalid_replays(self):
        client = RiotClient('fake', 'americas')
        with patch.object(client, 'get', return_value={'total': 0, 'matchFileURLs': []}):
            self.assertEqual(client.replay_match_ids('target', 20), [])
        for response in ({}, {'matchFileURLs': ['https://example.com/invalid.replay']}):
            with patch.object(client, 'get', return_value=response), self.assertRaises(RiotError):
                client.replay_match_ids('target', 20)

    def test_error_identifies_endpoint_and_redacts_response_key(self):
        client = RiotClient('secret-key', 'americas')
        body = json.dumps({'status': {'message': 'Forbidden secret-key'}}).encode()
        error = HTTPError('https://example.com', 403, 'Forbidden', {}, BytesIO(body))
        with patch('riot_wards_csv.urlopen', side_effect=error):
            with self.assertRaises(RiotError) as caught:
                client.get('/lol/match/v5/matches/by-puuid/target/replays')
        self.assertIn('MATCH-V5 /replays', str(caught.exception))
        self.assertIn('Forbidden [REDACTED]', str(caught.exception))
        self.assertNotIn('secret-key', str(caught.exception))

    def test_pagination(self):
        client = RiotClient('fake', 'americas')
        with patch.object(client, 'get', side_effect=[[str(i) for i in range(100)], ['last']]) as get:
            ids = client.match_ids('a/b', 150, start=10, queue=420)
        self.assertEqual(len(ids), 101)
        self.assertIn('a%2Fb/ids', get.call_args_list[0].args[0])
        self.assertEqual(get.call_args_list[1].args[1], {'start': 110, 'count': 50, 'queue': 420})

    def test_account_url_encoding(self):
        client = RiotClient('fake', 'americas')
        with patch.object(client, 'get', return_value={'puuid': 'target'}) as get:
            client.account('Example Player', 'TAG/#')
        self.assertTrue(get.call_args.args[0].endswith('/Example%20Player/TAG%2F%23'))

    def test_rate_limit_retries_using_header_and_token_header(self):
        client = RiotClient('fake', 'americas')
        response = BytesIO(b'{"ok": true}')
        error = HTTPError('https://example.com', 429, 'limited', {'Retry-After': '3'}, None)
        with patch('riot_wards_csv.urlopen', side_effect=[error, response]) as request, \
                patch('riot_wards_csv.time.sleep') as sleep:
            self.assertEqual(client.get('/test'), {'ok': True})
        sleep.assert_called_once_with(3)
        self.assertEqual(request.call_args.args[0].get_header('X-riot-token'), 'fake')
        self.assertEqual(request.call_args.args[0].get_header('User-agent'), 'detect-wards/1.0')
        self.assertEqual(request.call_args.args[0].get_header('Accept'), 'application/json')

    def test_cloudflare_block_is_distinguished_from_invalid_key(self):
        client = RiotClient('fake', 'americas')
        error = HTTPError('https://example.com', 403, 'Forbidden', {}, BytesIO(b'error code: 1010\n'))
        with patch('riot_wards_csv.urlopen', side_effect=error) as request:
            with self.assertRaisesRegex(RiotError, 'Cloudflare 요청 차단'):
                client.get('/test')
        self.assertEqual(request.call_count, 1)

    def test_auth_error_not_retried_and_key_not_in_error(self):
        client = RiotClient('secret-key', 'americas')
        error = HTTPError('https://example.com', 403, 'secret-key', {}, None)
        with patch('riot_wards_csv.urlopen', side_effect=error) as request:
            with self.assertRaises(RiotError) as caught:
                client.get('/test')
        self.assertNotIn('secret-key', str(caught.exception))
        self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
