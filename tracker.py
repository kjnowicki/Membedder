import os
import json
import time
import shutil

TRACKER_PATH = os.path.join('data', 'tracked_embeds.json')


class EmbedTracker:
    """
    Tracks recent bot embed replies linked to their original user messages.
    Persists state to JSON so deletion sync, carousel expansion, and expiry cleanup
    continue working reliably across the tracking window (and across bot restarts).
    """

    def __init__(self, path=TRACKER_PATH):
        self.path = path
        self._entries = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self._entries = {}
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._entries = data
            else:
                self._entries = {}
        except Exception as e:
            print(f"[TRACKER] Failed to load {self.path}: {e}")
            self._entries = {}

    def _save(self):
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(self._entries, f, indent=2)
        os.replace(tmp_path, self.path)

    @staticmethod
    def cleanup_carousel_dir(entry):
        carousel = (entry or {}).get('carousel')
        if not carousel:
            return
        carousel_dir = carousel.get('carousel_dir')
        if carousel_dir and os.path.exists(carousel_dir):
            shutil.rmtree(carousel_dir, ignore_errors=True)

    def register(
        self,
        original_message_id,
        channel_id,
        guild_id,
        author_id,
        bot_message_ids,
        ttl_seconds,
        carousel=None,
        now=None,
    ):
        created_at = time.time() if now is None else float(now)
        entry = {
            'original_message_id': int(original_message_id),
            'channel_id': int(channel_id),
            'guild_id': int(guild_id) if guild_id is not None else None,
            'author_id': int(author_id),
            'bot_message_ids': [int(mid) for mid in bot_message_ids],
            'created_at': created_at,
            'expires_at': created_at + float(ttl_seconds),
            'carousel': carousel,
        }
        self._entries[str(original_message_id)] = entry
        self._save()
        return entry

    def get(self, original_message_id):
        return self._entries.get(str(original_message_id))

    def add_bot_message_id(self, original_message_id, bot_message_id):
        entry = self._entries.get(str(original_message_id))
        if not entry:
            return
        mid = int(bot_message_id)
        if mid not in entry['bot_message_ids']:
            entry['bot_message_ids'].append(mid)
            self._save()

    def mark_carousel_expanded(self, original_message_id):
        entry = self._entries.get(str(original_message_id))
        if not entry:
            return
        self.cleanup_carousel_dir(entry)
        entry['carousel'] = None
        self._save()

    def pop_by_original_message(self, original_message_id):
        entry = self._entries.pop(str(original_message_id), None)
        if entry is not None:
            self._save()
        return entry

    def remove_bot_message_id(self, bot_message_id):
        """
        Called when a bot embed message itself is deleted manually.
        Removes it from tracking and cleans up carousel files if no bot messages remain.
        """
        mid = int(bot_message_id)
        for key, entry in list(self._entries.items()):
            if mid in entry.get('bot_message_ids', []):
                entry['bot_message_ids'].remove(mid)
                if not entry['bot_message_ids']:
                    self.cleanup_carousel_dir(entry)
                    self._entries.pop(key, None)
                self._save()
                return True
        return False

    def update_all_expirations(self, new_ttl_seconds):
        ttl = float(new_ttl_seconds)
        if not self._entries:
            return
        for entry in self._entries.values():
            entry['expires_at'] = float(entry.get('created_at', time.time())) + ttl
        self._save()

    def pop_expired(self, now=None):
        current = time.time() if now is None else float(now)
        expired = []
        for key in list(self._entries.keys()):
            entry = self._entries[key]
            if float(entry.get('expires_at', 0)) <= current:
                expired.append(self._entries.pop(key))
        if expired:
            self._save()
        return expired

    def active_carousel_dirs(self):
        dirs = set()
        for entry in self._entries.values():
            carousel = entry.get('carousel')
            if carousel and carousel.get('carousel_dir'):
                dirs.add(os.path.abspath(carousel['carousel_dir']))
        return dirs

    def stats(self):
        total = len(self._entries)
        expandable = sum(1 for e in self._entries.values() if e.get('carousel'))
        return total, expandable
