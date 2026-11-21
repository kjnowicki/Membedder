import os
import re
import json

CONFIG_DIR = 'data'
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
DEFAULT_TRACKING_HOURS = 24.0

_DURATION_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days)?\s*$', re.IGNORECASE)


def load_config(path=CONFIG_PATH):
    """Loads persisted bot configuration from disk."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[CONFIG] Failed to load {path}: {e}")
        return {}


def save_config(data, path=CONFIG_PATH):
    """Atomically writes bot configuration to disk."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def get_admin_user_ids():
    """
    Parses registered admin Discord user IDs from ADMIN_USER_IDS (or ADMIN_USER_ID)
    in the environment. Accepts comma- or whitespace-separated integer IDs.
    """
    ids = set()
    for env_key in ('ADMIN_USER_IDS', 'ADMIN_USER_ID'):
        raw = os.environ.get(env_key, '')
        if not raw:
            continue
        for token in re.split(r'[\s,;]+', raw.strip()):
            if token.isdigit():
                ids.add(int(token))
    return ids


def is_registered_admin(user_id):
    """Returns True if user_id is registered as an admin in .env."""
    if user_id is None:
        return False
    return int(user_id) in get_admin_user_ids()


def get_tracking_hours(path=CONFIG_PATH):
    """Returns the configured embed tracking duration in hours (defaults to 24.0)."""
    cfg = load_config(path)
    val = cfg.get('tracking_hours')
    if val is not None:
        try:
            hours = float(val)
            if hours > 0:
                return hours
        except (ValueError, TypeError):
            pass
    return DEFAULT_TRACKING_HOURS


def get_tracking_seconds(path=CONFIG_PATH):
    return get_tracking_hours(path) * 3600.0


def set_tracking_hours(hours, path=CONFIG_PATH):
    """Persists embed tracking duration in hours (> 0)."""
    hours = float(hours)
    if hours <= 0:
        raise ValueError("Tracking duration must be greater than 0.")
    cfg = load_config(path)
    cfg['tracking_hours'] = hours
    save_config(cfg, path)
    return hours


def reset_tracking_hours(path=CONFIG_PATH):
    """Resets embed tracking duration back to the default 24 hours."""
    return set_tracking_hours(DEFAULT_TRACKING_HOURS, path=path)


def parse_duration_to_hours(raw):
    """
    Parses a human duration string into hours.
    Plain numbers are interpreted as hours (e.g. '24', '0.5').
    Optional units: s/sec, m/min, h/hr, d/day (e.g. '30m', '12h', '2d').
    """
    if not isinstance(raw, str):
        raise ValueError("Duration must be a string.")
    match = _DURATION_RE.match(raw)
    if not match:
        raise ValueError(f"Invalid duration format: {raw!r}")

    value = float(match.group(1))
    if value <= 0:
        raise ValueError("Duration must be greater than 0.")

    unit = (match.group(2) or 'h').lower()
    if unit.startswith('s'):
        return value / 3600.0
    if unit.startswith('m'):
        return value / 60.0
    if unit.startswith('d'):
        return value * 24.0
    return value


def format_tracking_duration(hours):
    """Formats a duration in hours into a clean human-readable string."""
    total_seconds = round(hours * 3600)
    if total_seconds < 60:
        return f"{total_seconds}s"
    if total_seconds < 3600 and total_seconds % 60 == 0:
        mins = total_seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''}"
    if abs(hours - round(hours)) < 1e-6:
        whole = int(round(hours))
        return f"{whole} hour{'s' if whole != 1 else ''}"
    return f"{hours:.2f} hours"
