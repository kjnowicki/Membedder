import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sites.cookies import json_to_netscape, load_cookies_dict


class TestCookies(unittest.TestCase):
    def test_json_to_netscape(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, 'cookies.json')
            txt_path = os.path.join(tmp, 'cookies.txt')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump([
                    {'domain': '.instagram.com', 'path': '/', 'secure': True, 'expirationDate': 1999999999, 'name': 'sessionid', 'value': 'abc123'},
                ], f)

            self.assertTrue(json_to_netscape(json_path, txt_path))
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.assertIn('.instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tabc123', content)

    def test_load_cookies_dict_netscape_with_httponly(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = os.path.join(tmp, 'ig_cookies.txt')
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write('# Netscape HTTP Cookie File\n')
                f.write('.instagram.com\tTRUE\t/\tTRUE\t1999999999\tcsrftoken\ttok123\n')
                f.write('#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tsess999\n')

            with patch('sites.cookies._get_candidate_cookie_files', return_value=[txt_path]):
                cookies = load_cookies_dict('instagram')
            self.assertEqual(cookies.get('csrftoken'), 'tok123')
            self.assertEqual(cookies.get('sessionid'), 'sess999')


if __name__ == '__main__':
    unittest.main()
