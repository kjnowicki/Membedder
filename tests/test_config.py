import os
import tempfile
import unittest

from config import (
    DEFAULT_TRACKING_HOURS,
    get_admin_user_ids,
    is_registered_admin,
    get_tracking_hours,
    set_tracking_hours,
    reset_tracking_hours,
    parse_duration_to_hours,
    format_tracking_duration,
)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._orig_ids = os.environ.pop('ADMIN_USER_IDS', None)
        self._orig_id = os.environ.pop('ADMIN_USER_ID', None)

    def tearDown(self):
        if self._orig_ids is not None:
            os.environ['ADMIN_USER_IDS'] = self._orig_ids
        else:
            os.environ.pop('ADMIN_USER_IDS', None)
        if self._orig_id is not None:
            os.environ['ADMIN_USER_ID'] = self._orig_id
        else:
            os.environ.pop('ADMIN_USER_ID', None)

    def test_admin_user_ids_parsing(self):
        os.environ['ADMIN_USER_IDS'] = '111111, 222222; invalid'
        os.environ['ADMIN_USER_ID'] = '333333'
        self.assertEqual(get_admin_user_ids(), {111111, 222222, 333333})
        self.assertTrue(is_registered_admin(111111))
        self.assertTrue(is_registered_admin(333333))
        self.assertFalse(is_registered_admin(999999))
        self.assertFalse(is_registered_admin(None))

    def test_parse_duration_to_hours(self):
        self.assertEqual(parse_duration_to_hours('24'), 24.0)
        self.assertEqual(parse_duration_to_hours('12h'), 12.0)
        self.assertEqual(parse_duration_to_hours('30m'), 0.5)
        self.assertEqual(parse_duration_to_hours('2d'), 48.0)
        self.assertAlmostEqual(parse_duration_to_hours('90s'), 90 / 3600.0)

        for bad in ['0', '-5', 'abc', '10x', '']:
            with self.assertRaises(ValueError):
                parse_duration_to_hours(bad)

    def test_format_tracking_duration(self):
        self.assertEqual(format_tracking_duration(24.0), '24 hours')
        self.assertEqual(format_tracking_duration(1.0), '1 hour')
        self.assertEqual(format_tracking_duration(0.5), '30 minutes')
        self.assertEqual(format_tracking_duration(45 / 3600.0), '45s')

    def test_tracking_hours_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, 'config.json')
            self.assertEqual(get_tracking_hours(path=cfg_path), DEFAULT_TRACKING_HOURS)
            set_tracking_hours(12.0, path=cfg_path)
            self.assertEqual(get_tracking_hours(path=cfg_path), 12.0)
            reset_tracking_hours(path=cfg_path)
            self.assertEqual(get_tracking_hours(path=cfg_path), DEFAULT_TRACKING_HOURS)


if __name__ == '__main__':
    unittest.main()
