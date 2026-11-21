import unittest
from sites.instagram import (
    parse_img_index,
    extract_shortcode,
    shortcode_to_pk,
    _parse_api_item,
    _parse_graphql_media,
)


class TestInstagramParsing(unittest.TestCase):
    def test_parse_img_index(self):
        self.assertEqual(
            parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?utm_source=ig_web_copy_link&bqrl=NTc4MTIwNjQ2YQ=="),
            0,
        )
        self.assertEqual(
            parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?utm_source=ig_web_copy_link&bqrl=NTc4MTIwNjQ2YQ%3D%3D&img_index=2"),
            1,
        )
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?img_index=1"), 0)
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?img_index=3"), 2)
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?img_index=0"), 0)
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?img_index=-2"), 0)
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?img_index=abc"), 0)
        self.assertEqual(parse_img_index("https://www.instagram.com/p/DgAcu4fnzfO/?a=1&amp;img_index=4"), 3)

    def test_extract_shortcode_and_pk(self):
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/p/DgAcu4fnzfO/?utm_source=ig_web_copy_link"),
            ("DgAcu4fnzfO", "p"),
        )
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/premierleague/p/DgAcu4fnzfO/?img_index=2"),
            ("DgAcu4fnzfO", "p"),
        )
        self.assertEqual(
            extract_shortcode("https://m.instagram.com/reel/Chunk8-jurw/"),
            ("Chunk8-jurw", "reel"),
        )
        self.assertEqual(
            extract_shortcode("https://instagram.com/reels/Cop84x6u7CP/"),
            ("Cop84x6u7CP", "reels"),
        )
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/tv/BkfuX9UB-eK/"),
            ("BkfuX9UB-eK", "tv"),
        )
        self.assertEqual(
            extract_shortcode("https://www.instagram.com/share/p/ABC123"),
            (None, None),
        )
        self.assertEqual(shortcode_to_pk("DgAcu4fnzfO"), 4035351633182275534)
        self.assertIsNone(shortcode_to_pk("invalid!char"))
        self.assertIsNone(shortcode_to_pk(""))

    def test_parse_api_item_mixed_carousel(self):
        mock_item = {
            'code': 'DgAcu4fnzfO',
            'media_type': 8,
            'user': {'username': 'premierleague', 'full_name': 'Premier League'},
            'caption': {'text': 'Hello carousel!'},
            'carousel_media': [
                {
                    'media_type': 1,
                    'image_versions2': {
                        'candidates': [
                            {'url': 'https://cdn/small.jpg', 'width': 400, 'height': 400},
                            {'url': 'https://cdn/big.jpg', 'width': 1080, 'height': 1350},
                        ]
                    },
                },
                {
                    'media_type': 2,
                    'video_versions': [
                        {'url': 'https://cdn/v1080.mp4', 'width': 1080, 'height': 1920},
                        {'url': 'https://cdn/v720.mp4', 'width': 720, 'height': 720},
                    ],
                    'image_versions2': {
                        'candidates': [{'url': 'https://cdn/thumb.jpg', 'width': 720, 'height': 720}]
                    },
                },
            ],
        }
        parsed = _parse_api_item(mock_item, 'DgAcu4fnzfO')
        self.assertEqual(parsed['uploader'], 'Premier League')
        self.assertEqual(parsed['uploader_id'], 'premierleague')
        self.assertEqual(parsed['webpage_url'], 'https://www.instagram.com/p/DgAcu4fnzfO/')
        self.assertEqual(len(parsed['items']), 2)
        self.assertFalse(parsed['items'][0]['is_video'])
        self.assertEqual(parsed['items'][0]['url'], 'https://cdn/big.jpg')
        self.assertTrue(parsed['items'][1]['is_video'])
        self.assertEqual(parsed['items'][1]['url'], 'https://cdn/v720.mp4')
        self.assertEqual(parsed['items'][1]['thumbnail'], 'https://cdn/thumb.jpg')

    def test_parse_graphql_media_sidecar(self):
        mock_gql = {
            'shortcode': 'TestCode123',
            'owner': {'username': 'alice', 'full_name': 'Alice'},
            'edge_media_to_caption': {'edges': [{'node': {'text': 'GQL caption'}}]},
            'edge_sidecar_to_children': {
                'edges': [
                    {'node': {'__typename': 'GraphImage', 'is_video': False, 'display_url': 'https://cdn/1.jpg', 'dimensions': {'height': 1080}}},
                    {'node': {'__typename': 'GraphVideo', 'is_video': True, 'video_url': 'https://cdn/2.mp4', 'display_url': 'https://cdn/2.jpg', 'dimensions': {'height': 720}}},
                ]
            },
        }
        parsed = _parse_graphql_media(mock_gql, 'TestCode123')
        self.assertEqual(parsed['caption'], 'GQL caption')
        self.assertEqual(len(parsed['items']), 2)
        self.assertFalse(parsed['items'][0]['is_video'])
        self.assertTrue(parsed['items'][1]['is_video'])
        self.assertEqual(parsed['items'][1]['url'], 'https://cdn/2.mp4')


if __name__ == '__main__':
    unittest.main()
