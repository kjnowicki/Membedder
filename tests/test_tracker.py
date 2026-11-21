import os
import tempfile
import unittest

from tracker import EmbedTracker


class TestEmbedTracker(unittest.TestCase):
    def test_persistence_expansion_deletion_and_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = os.path.join(tmp, 'tracked_embeds.json')
            cdir = os.path.join(tmp, 'carousel_abc')
            os.makedirs(cdir)
            img_path = os.path.join(cdir, '1.jpg')
            with open(img_path, 'wb') as f:
                f.write(b'img1')

            t1 = EmbedTracker(path=tracker_path)
            now = 1000000.0
            t1.register(
                original_message_id=1001,
                channel_id=500,
                guild_id=600,
                author_id=777,
                bot_message_ids=[2001],
                ttl_seconds=3600,
                carousel={'carousel_dir': cdir, 'slides': [{'path': img_path, 'is_video': False}], 'start_index': 0},
                now=now,
            )
            t1.register(
                original_message_id=1002,
                channel_id=500,
                guild_id=600,
                author_id=888,
                bot_message_ids=[2002],
                ttl_seconds=3600,
                carousel=None,
                now=now,
            )
            t1.add_bot_message_id(1001, 2099)

            # Simulate bot restart
            t2 = EmbedTracker(path=tracker_path)
            self.assertEqual(t2.stats(), (2, 1))
            self.assertIn(os.path.abspath(cdir), t2.active_carousel_dirs())
            self.assertEqual(t2.get(1001)['bot_message_ids'], [2001, 2099])

            # Marking carousel expanded deletes carousel_dir while keeping deletion tracking alive
            t2.mark_carousel_expanded(1001)
            self.assertFalse(os.path.exists(cdir))
            self.assertEqual(t2.stats(), (2, 0))
            self.assertIsNotNone(t2.get(1001))

            # Popping original message returns all bot message IDs
            popped = t2.pop_by_original_message(1001)
            self.assertEqual(popped['bot_message_ids'], [2001, 2099])
            self.assertIsNone(t2.get(1001))

            # Updating expirations & popping expired
            t2.update_all_expirations(60)
            self.assertEqual(t2.pop_expired(now=now + 30), [])
            expired = t2.pop_expired(now=now + 61)
            self.assertEqual(len(expired), 1)
            self.assertEqual(expired[0]['original_message_id'], 1002)
            self.assertEqual(t2.stats(), (0, 0))

    def test_remove_bot_message_cleans_up_carousel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = os.path.join(tmp, 'tracked.json')
            cdir = os.path.join(tmp, 'carousel_del')
            os.makedirs(cdir)
            t = EmbedTracker(path=tracker_path)
            t.register(
                original_message_id=3001,
                channel_id=500,
                guild_id=600,
                author_id=777,
                bot_message_ids=[4001],
                ttl_seconds=3600,
                carousel={'carousel_dir': cdir, 'slides': [], 'start_index': 0},
            )
            self.assertTrue(t.remove_bot_message_id(4001))
            self.assertFalse(os.path.exists(cdir))
            self.assertIsNone(t.get(3001))


if __name__ == '__main__':
    unittest.main()
