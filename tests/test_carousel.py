import os
import time
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from tracker import EmbedTracker
from carousel import (
    ExpandCarouselButton,
    batch_file_entries,
    reorder_slides_from_index,
    build_single_slide_payload,
    build_expanded_gallery_payload,
    create_expand_view,
)


class TestCarouselHelpers(unittest.TestCase):
    def test_batch_file_entries_count_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = []
            for i in range(12):
                p = os.path.join(tmp, f'img_{i}.jpg')
                with open(p, 'wb') as f:
                    f.write(b'x' * 100)
                entries.append({'path': p})
            batches = batch_file_entries(entries, max_files=10, max_bytes=8 * 1024 * 1024)
            self.assertEqual([len(b) for b in batches], [10, 2])

            big = []
            for i in range(3):
                p = os.path.join(tmp, f'big_{i}.jpg')
                with open(p, 'wb') as f:
                    f.write(b'x' * (3 * 1024 * 1024))
                big.append({'path': p})
            big_batches = batch_file_entries(big, max_files=10, max_bytes=8 * 1024 * 1024)
            self.assertEqual([len(b) for b in big_batches], [2, 1])

    def test_reorder_slides_from_index(self):
        self.assertEqual(reorder_slides_from_index([1, 2, 3], 0), [1, 2, 3])
        self.assertEqual(reorder_slides_from_index([1, 2, 3], 1), [2, 3, 1])
        self.assertEqual(reorder_slides_from_index([1, 2, 3], 2), [3, 1, 2])
        self.assertEqual(reorder_slides_from_index([1, 2, 3], 99), [3, 1, 2])
        self.assertEqual(reorder_slides_from_index([1, 2, 3], -5), [1, 2, 3])

    def test_single_and_expanded_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(1, 4):
                p = os.path.join(tmp, f'post_{i}.jpg')
                with open(p, 'wb') as f:
                    f.write(f'photo-{i}'.encode())
                paths.append(p)

            slides = [
                {'path': paths[0], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1349},
                {'path': paths[1], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1362},
                {'path': paths[2], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1873},
            ]

            init_embed, init_files = build_single_slide_payload(
                slides=slides,
                start_index=1,
                author_name='Premier League (@premierleague)',
                post_url='https://www.instagram.com/p/DgAcu4fnzfO/',
                description='Test caption',
            )
            self.assertEqual(init_embed.footer.text, '2 / 3')
            self.assertEqual(init_embed.image.url, 'attachment://slide_2.jpg')
            self.assertEqual(len(init_files), 1)
            self.assertEqual(init_files[0].filename, 'slide_2.jpg')
            init_files[0].close()

            exp_embed, exp_batches = build_expanded_gallery_payload(
                slides=slides,
                start_index=1,
                author_name='Premier League (@premierleague)',
                post_url='https://www.instagram.com/p/DgAcu4fnzfO/',
                description='Test caption',
            )
            self.assertIsNone(exp_embed.image.url)
            self.assertIsNone(exp_embed.footer.text)
            self.assertEqual(len(exp_batches), 1)
            self.assertEqual(len(exp_batches[0]), 3)
            for f in exp_batches[0]:
                f.close()


class TestExpandCarouselButton(unittest.IsolatedAsyncioTestCase):
    async def test_author_only_expand_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = os.path.join(tmp, 'tracked.json')
            cdir = os.path.join(tmp, 'carousel_test')
            os.makedirs(cdir)
            paths = []
            for i in range(1, 4):
                p = os.path.join(cdir, f'post_{i}.jpg')
                with open(p, 'wb') as f:
                    f.write(f'photo-{i}'.encode())
                paths.append(p)

            slides = [
                {'path': paths[0], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1080},
                {'path': paths[1], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1080},
                {'path': paths[2], 'is_video': False, 'exceeded_limit': False, 'thumbnail': None, 'height': 1080},
            ]

            tracker = EmbedTracker(path=tracker_path)
            ExpandCarouselButton.bind_tracker(tracker)
            orig_id = 1234567890
            author_id = 424242

            tracker.register(
                original_message_id=orig_id,
                channel_id=999,
                guild_id=888,
                author_id=author_id,
                bot_message_ids=[555555],
                ttl_seconds=86400,
                carousel={
                    'carousel_dir': cdir,
                    'slides': slides,
                    'start_index': 1,
                    'author_name': 'Premier League (@premierleague)',
                    'post_url': 'https://www.instagram.com/p/DgAcu4fnzfO/',
                    'description': 'Test caption',
                    'color': 0xE1306C,
                },
            )

            view = create_expand_view(orig_id, 3)
            btn_item = view.children[0]
            match = btn_item.template.match(btn_item.custom_id)
            self.assertIsNotNone(match)
            reconstructed = await ExpandCarouselButton.from_custom_id(None, None, match)

            # Non-author click is rejected ephemerally
            inter_other = MagicMock()
            inter_other.user.id = 999999
            inter_other.response.send_message = AsyncMock()
            inter_other.response.defer = AsyncMock()
            await reconstructed.callback(inter_other)
            inter_other.response.send_message.assert_awaited_once()
            self.assertTrue(inter_other.response.send_message.call_args.kwargs['ephemeral'])
            inter_other.response.defer.assert_not_awaited()
            self.assertTrue(os.path.exists(cdir))

            # Author click expands to Option 3, removes button, deletes carousel_dir, keeps delete tracking
            inter_author = MagicMock()
            inter_author.user.id = author_id
            inter_author.channel = MagicMock()
            inter_author.response.defer = AsyncMock()
            inter_author.edit_original_response = AsyncMock()
            await reconstructed.callback(inter_author)
            inter_author.response.defer.assert_awaited_once()
            inter_author.edit_original_response.assert_awaited_once()
            kw = inter_author.edit_original_response.call_args.kwargs
            self.assertIsNone(kw['view'])
            self.assertIsNone(kw['embed'].image.url)
            self.assertEqual(len(kw['attachments']), 3)
            self.assertFalse(os.path.exists(cdir))

            entry_after = tracker.get(orig_id)
            self.assertIsNotNone(entry_after)
            self.assertIsNone(entry_after['carousel'])
            self.assertEqual(entry_after['bot_message_ids'], [555555])

            view.stop()

    async def test_expired_button_removes_view_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = os.path.join(tmp, 'tracked.json')
            cdir = os.path.join(tmp, 'carousel_exp')
            os.makedirs(cdir)
            tracker = EmbedTracker(path=tracker_path)
            ExpandCarouselButton.bind_tracker(tracker)

            orig_id = 7777777
            tracker.register(
                original_message_id=orig_id,
                channel_id=999,
                guild_id=888,
                author_id=123,
                bot_message_ids=[888888],
                ttl_seconds=10,
                carousel={'carousel_dir': cdir, 'slides': [], 'start_index': 0},
                now=time.time() - 100,  # already expired
            )

            btn = ExpandCarouselButton(orig_id, 2)
            inter = MagicMock()
            inter.user.id = 123
            inter.response.edit_message = AsyncMock()
            await btn.callback(inter)
            inter.response.edit_message.assert_awaited_once_with(view=None)
            self.assertFalse(os.path.exists(cdir))
            self.assertIsNone(tracker.get(orig_id))


if __name__ == '__main__':
    unittest.main()
