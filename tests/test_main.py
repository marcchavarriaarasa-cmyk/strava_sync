import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import main


class ResponseSession:
    def __init__(self, status_code):
        self.status_code = status_code

    def get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = self.status_code
        response.url = url
        response._content = b'not available'
        return response


class StravaSyncTests(unittest.TestCase):
    def setUp(self):
        main.API_CALLS = 0

    def test_missing_credentials_raise_a_sync_error(self):
        with (
            patch.object(main, 'CLIENT_ID', None),
            patch.object(main, 'CLIENT_SECRET', None),
            patch.object(main, 'REFRESH_TOKEN', None),
        ):
            with self.assertRaisesRegex(
                main.StravaSyncError,
                'STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN',
            ):
                main.require_credentials()

    def test_activity_fetch_error_is_not_silently_ignored(self):
        with patch.object(main, 'SESSION', ResponseSession(503)):
            with self.assertRaises(main.StravaSyncError):
                main.get_activities('token')

    def test_missing_zones_are_an_expected_empty_result(self):
        with patch.object(main, 'SESSION', ResponseSession(404)):
            self.assertEqual(main.get_zones('123', 'token'), [])

    def test_detail_failure_does_not_replace_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'entrenamientos_contexto.txt'
            output.write_text('existing data\n', encoding='utf-8')
            activity = {'id': 123, 'sport_type': 'Run'}

            with (
                patch.object(main, 'OUTPUT_FILE', str(output)),
                patch.object(
                    main,
                    'get_activity_detail',
                    side_effect=main.StravaSyncError('temporary failure'),
                ),
            ):
                with self.assertRaises(main.StravaSyncError):
                    main.save_activities([activity], 'token')

            self.assertEqual(output.read_text(encoding='utf-8'), 'existing data\n')

    def test_iso_dates_with_or_without_z_are_supported(self):
        base = {
            'sport_type': 'Run',
            'distance': 5000,
            'moving_time': 1500,
        }
        for date in ('2026-07-11T08:30:00Z', '2026-07-11T08:30:00'):
            description = main.format_activity({**base, 'start_date_local': date})
            self.assertIn('11/07/2026', description)


if __name__ == '__main__':
    unittest.main()
