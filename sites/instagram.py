import os
import re
import html
import json
import uuid
import shutil
import asyncio
from urllib.parse import urlparse, parse_qs

import aiohttp
import yt_dlp
from sites.cookies import get_cookie_opts, load_cookies_dict, ytdl_extract_info

_ENCODING_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
_IG_SHORTCODE_RE = re.compile(
    r'https?://(?:www\.|m\.)?instagram\.com(?:/(?!share/)[^/?#]+)?/(?P<kind>p|tv|reels?(?!/audio/))/(?P<id>[^/?#&]+)'
)
_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
)
_IG_API_HEADERS = {
    'User-Agent': _USER_AGENT,
    'X-IG-App-ID': '936619743392459',
    'X-ASBD-ID': '198387',
    'X-IG-WWW-Claim': '0',
    'Origin': 'https://www.instagram.com',
    'Accept': '*/*',
}


def parse_img_index(url):
    """
    Extracts Instagram's 1-based img_index query parameter and converts it
    to a 0-based starting index. Returns 0 when absent or invalid.
    """
    try:
        parsed = urlparse(html.unescape(url))
        qs = parse_qs(parsed.query)
        raw = qs.get('img_index', [None])[0]
        if raw is not None:
            idx = int(raw)
            if idx >= 1:
                return idx - 1
    except (ValueError, TypeError):
        pass
    return 0


def extract_shortcode(url):
    """
    Extracts (shortcode, kind) from an Instagram post/reel/tv URL, or (None, None).
    """
    match = _IG_SHORTCODE_RE.search(url)
    if match:
        return match.group('id'), match.group('kind')
    return None, None


def shortcode_to_pk(shortcode):
    """Converts an Instagram base64 shortcode to its numeric media pk."""
    if not shortcode:
        return None
    if len(shortcode) > 28:
        shortcode = shortcode[:-28]
    pk = 0
    try:
        for ch in shortcode:
            pk = pk * 64 + _ENCODING_CHARS.index(ch)
        return pk
    except ValueError:
        return None


async def _resolve_share_url(url, cookies):
    """Follows redirects for /share/... Instagram URLs to uncover the canonical /p/ or /reel/ URL."""
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(
            cookies=cookies or None,
            headers={'User-Agent': _USER_AGENT},
            timeout=timeout,
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                return str(resp.url)
    except Exception as e:
        print(f"[INSTAGRAM] Failed to resolve share URL {url}: {e}")
        return url


def _pick_best_image_candidate(image_versions2):
    candidates = (image_versions2 or {}).get('candidates') or []
    valid = [c for c in candidates if isinstance(c, dict) and c.get('url')]
    if not valid:
        return None
    return max(valid, key=lambda c: (c.get('width') or 0) * (c.get('height') or 0))


def _pick_best_video_version(video_versions):
    valid = [v for v in (video_versions or []) if isinstance(v, dict) and v.get('url')]
    if not valid:
        return None
    under_720 = [v for v in valid if (v.get('height') or 0) <= 720]
    if under_720:
        return max(under_720, key=lambda v: ((v.get('height') or 0), (v.get('width') or 0)))
    return min(valid, key=lambda v: (v.get('height') or 999999))


def _parse_api_item(item, shortcode):
    """Normalizes an Instagram v1 media/info item into a common post structure."""
    code = item.get('code') or shortcode
    user = item.get('user') or {}
    caption = (item.get('caption') or {}).get('text') or ''
    username = user.get('username')
    full_name = user.get('full_name')
    media_type = item.get('media_type')  # 1 = photo, 2 = video, 8 = carousel

    items = []
    carousel_media = item.get('carousel_media') or []
    if carousel_media:
        for cm in carousel_media:
            best_img = _pick_best_image_candidate(cm.get('image_versions2'))
            best_vid = _pick_best_video_version(cm.get('video_versions'))
            if best_vid and (cm.get('media_type') == 2 or not best_img):
                items.append({
                    'is_video': True,
                    'url': best_vid['url'],
                    'thumbnail': best_img['url'] if best_img else None,
                    'height': best_vid.get('height'),
                })
            elif best_img:
                items.append({
                    'is_video': False,
                    'url': best_img['url'],
                    'thumbnail': best_img['url'],
                    'height': best_img.get('height'),
                })
    else:
        best_img = _pick_best_image_candidate(item.get('image_versions2'))
        best_vid = _pick_best_video_version(item.get('video_versions'))
        if best_vid and (media_type == 2 or not best_img):
            items.append({
                'is_video': True,
                'url': best_vid['url'],
                'thumbnail': best_img['url'] if best_img else None,
                'height': best_vid.get('height'),
            })
        elif best_img:
            items.append({
                'is_video': False,
                'url': best_img['url'],
                'thumbnail': best_img['url'],
                'height': best_img.get('height'),
            })

    return {
        'shortcode': code,
        'media_type': media_type,
        'caption': caption,
        'uploader': full_name or username,
        'uploader_id': username,
        'webpage_url': f'https://www.instagram.com/p/{code}/',
        'items': items,
    }


def _parse_graphql_media(media, shortcode):
    """Normalizes an Instagram GraphQL xdt_shortcode_media dict into a common post structure."""
    code = media.get('shortcode') or shortcode
    owner = media.get('owner') or {}
    username = owner.get('username')
    full_name = owner.get('full_name')
    cap_edges = (media.get('edge_media_to_caption') or {}).get('edges') or []
    caption = (cap_edges[0].get('node') or {}).get('text', '') if cap_edges else ''

    items = []
    sidecar_edges = (media.get('edge_sidecar_to_children') or {}).get('edges') or []
    if sidecar_edges:
        for edge in sidecar_edges:
            node = edge.get('node') or {}
            is_video = bool(node.get('is_video') or node.get('__typename') == 'GraphVideo')
            if is_video and node.get('video_url'):
                items.append({
                    'is_video': True,
                    'url': node['video_url'],
                    'thumbnail': node.get('display_url'),
                    'height': (node.get('dimensions') or {}).get('height'),
                })
            elif node.get('display_url'):
                items.append({
                    'is_video': False,
                    'url': node['display_url'],
                    'thumbnail': node['display_url'],
                    'height': (node.get('dimensions') or {}).get('height'),
                })
    else:
        is_video = bool(media.get('is_video') or media.get('__typename') == 'GraphVideo')
        if is_video and media.get('video_url'):
            items.append({
                'is_video': True,
                'url': media['video_url'],
                'thumbnail': media.get('display_url'),
                'height': (media.get('dimensions') or {}).get('height'),
            })
        elif media.get('display_url'):
            items.append({
                'is_video': False,
                'url': media['display_url'],
                'thumbnail': media['display_url'],
                'height': (media.get('dimensions') or {}).get('height'),
            })

    media_type = 8 if len(items) > 1 else (2 if (items and items[0]['is_video']) else 1)
    return {
        'shortcode': code,
        'media_type': media_type,
        'caption': caption,
        'uploader': full_name or username,
        'uploader_id': username,
        'webpage_url': f'https://www.instagram.com/p/{code}/',
        'items': items,
    }


async def _fetch_post_metadata(shortcode, pk, cookies):
    """
    Fetches Instagram post metadata (supporting photo posts, carousels, and mixed posts).
    Tier 1: Authenticated i.instagram.com/api/v1/media/{pk}/info/ (when sessionid cookie is available).
    Tier 2: Public GraphQL xdt_shortcode_media fallback.
    """
    timeout = aiohttp.ClientTimeout(total=20)

    # Tier 1: Authenticated media/info endpoint
    if cookies and cookies.get('sessionid'):
        try:
            async with aiohttp.ClientSession(
                cookies=cookies,
                headers=_IG_API_HEADERS,
                timeout=timeout,
            ) as session:
                api_url = f'https://i.instagram.com/api/v1/media/{pk}/info/'
                async with session.get(api_url) as resp:
                    if resp.status == 200 and 'json' in (resp.headers.get('Content-Type') or ''):
                        data = await resp.json()
                        items_list = data.get('items') or []
                        if items_list:
                            return _parse_api_item(items_list[0], shortcode)
                    else:
                        print(f"[INSTAGRAM] media/info returned status {resp.status}")
        except Exception as e:
            print(f"[INSTAGRAM] media/info request failed: {e}")

    # Tier 2: GraphQL fallback (works on some public posts without sessionid)
    try:
        async with aiohttp.ClientSession(
            headers=_IG_API_HEADERS,
            timeout=timeout,
        ) as session:
            ruling_url = (
                f'https://i.instagram.com/api/v1/web/get_ruling_for_content/'
                f'?content_type=MEDIA&target_id={pk}'
            )
            csrf = ''
            try:
                async with session.get(ruling_url):
                    pass
                for c in session.cookie_jar:
                    if c.key == 'csrftoken':
                        csrf = c.value
                        break
            except Exception:
                pass

            variables = {
                'shortcode': shortcode,
                'child_comment_count': 3,
                'fetch_comment_count': 40,
                'parent_comment_count': 24,
                'has_threaded_comments': True,
            }
            gql_headers = {
                **_IG_API_HEADERS,
                'X-CSRFToken': csrf,
                'X-Requested-With': 'XMLHttpRequest',
                'Referer': f'https://www.instagram.com/p/{shortcode}/',
            }
            params = {
                'doc_id': '8845758582119845',
                'variables': json.dumps(variables, separators=(',', ':')),
            }
            async with session.get(
                'https://www.instagram.com/graphql/query/',
                headers=gql_headers,
                params=params,
            ) as gresp:
                if gresp.status == 200 and 'json' in (gresp.headers.get('Content-Type') or ''):
                    gdata = await gresp.json()
                    media = (gdata.get('data') or {}).get('xdt_shortcode_media')
                    if isinstance(media, dict) and media:
                        return _parse_graphql_media(media, shortcode)
    except Exception as e:
        print(f"[INSTAGRAM] GraphQL fallback failed: {e}")

    return None


def _extension_from_content_type(content_type, is_video):
    if is_video:
        return '.mp4'
    ct = (content_type or '').lower()
    if 'webp' in ct:
        return '.webp'
    if 'png' in ct:
        return '.png'
    if 'gif' in ct:
        return '.gif'
    return '.jpg'


async def _download_media_item(session, media_url, base_path, is_video):
    headers = {
        'User-Agent': _USER_AGENT,
        'Referer': 'https://www.instagram.com/',
    }
    try:
        async with session.get(media_url, headers=headers) as resp:
            if resp.status != 200:
                print(f"[INSTAGRAM] Media download failed ({resp.status}): {media_url[:80]}")
                return None
            ext = _extension_from_content_type(resp.headers.get('Content-Type'), is_video)
            filepath = f"{base_path}{ext}"
            with open(filepath, 'wb') as f:
                while True:
                    chunk = await resp.content.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                return filepath
            return None
    except Exception as e:
        print(f"[INSTAGRAM] Error downloading media {media_url[:80]}: {e}")
        return None


def get_ytdl_opts(download_path):
    opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': os.path.join(download_path, '%(id)s.%(ext)s'),
        'max_filesize': 100 * 1024 * 1024,
        'quiet': True,
        'no_warnings': True,
    }
    # Check for instagram cookies (handles txt, json, and browser fallbacks)
    cookie_opts = get_cookie_opts('instagram', download_path)
    opts.update(cookie_opts)
    return opts


async def _handle_via_ytdl(url, temp_dir, requested_index=0):
    loop = asyncio.get_event_loop()
    ydl_opts = get_ytdl_opts(temp_dir)

    try:
        info = await ytdl_extract_info(ydl_opts, url, loop)
        if not info:
            return {
                'title': '',
                'files': [],
                'error': 'Failed to extract Instagram post info'
            }

        caption = info.get('description') or info.get('title') or ""

        clean_opts = ydl_opts.copy()
        clean_opts.pop('cookiefile', None)
        clean_opts.pop('cookiesfrombrowser', None)
        with yt_dlp.YoutubeDL(clean_opts) as ydl:
            # Instagram post can be a carousel (playlist type in yt_dlp)
            if info.get('_type') == 'playlist':
                files = []
                entries_meta = []
                for entry in info.get('entries', []):
                    if entry:
                        found_path = None
                        try:
                            filename = ydl.prepare_filename(entry)
                            if os.path.exists(filename):
                                found_path = filename
                        except Exception:
                            pass
                        if not found_path:
                            entry_id = entry.get('id')
                            if entry_id:
                                for f in os.listdir(temp_dir):
                                    if f.startswith(entry_id):
                                        found_path = os.path.join(temp_dir, f)
                                        break
                        if found_path:
                            files.append(found_path)
                            entries_meta.append({
                                'path': found_path,
                                'is_video': found_path.lower().endswith(('.mp4', '.mov', '.webm')),
                                'thumbnail': entry.get('thumbnail'),
                                'height': entry.get('height'),
                            })

                result = {
                    'title': caption,
                    'files': files,
                    'uploader': info.get('uploader'),
                    'uploader_id': info.get('channel') or info.get('uploader_id'),
                    'webpage_url': info.get('webpage_url'),
                    'description': caption,
                    'thumbnail': info.get('thumbnail'),
                    'height': info.get('height')
                }

                # If yt-dlp extracted a multi-item playlist, move into a carousel_dir so it can be browsed interactively
                if len(entries_meta) > 1:
                    carousel_dir = os.path.join(temp_dir, f"carousel_{uuid.uuid4().hex[:10]}")
                    os.makedirs(carousel_dir, exist_ok=True)
                    moved_slides = []
                    moved_files = []
                    for slide in entries_meta:
                        dst = os.path.join(carousel_dir, os.path.basename(slide['path']))
                        shutil.move(slide['path'], dst)
                        slide['path'] = dst
                        moved_slides.append(slide)
                        moved_files.append(dst)
                    result['files'] = moved_files
                    result['slides'] = moved_slides
                    result['start_index'] = max(0, min(requested_index, len(moved_slides) - 1))
                    result['carousel_dir'] = carousel_dir

                return result
            else:
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return {
                        'title': caption,
                        'files': [filename],
                        'uploader': info.get('uploader'),
                        'uploader_id': info.get('channel') or info.get('uploader_id'),
                        'webpage_url': info.get('webpage_url'),
                        'description': caption,
                        'thumbnail': info.get('thumbnail'),
                        'height': info.get('height')
                    }
                else:
                    # Fallback: check if any file in temp matches the info id
                    info_id = info.get('id')
                    if info_id:
                        for f in os.listdir(temp_dir):
                            if f.startswith(info_id):
                                return {
                                    'title': caption,
                                    'files': [os.path.join(temp_dir, f)],
                                    'uploader': info.get('uploader'),
                                    'uploader_id': info.get('channel') or info.get('uploader_id'),
                                    'webpage_url': info.get('webpage_url'),
                                    'description': caption,
                                    'thumbnail': info.get('thumbnail'),
                                    'height': info.get('height')
                                }
                    return {
                        'title': caption,
                        'files': [],
                        'error': f"Downloaded file not found: {filename}",
                        'uploader': info.get('uploader'),
                        'uploader_id': info.get('channel') or info.get('uploader_id'),
                        'webpage_url': info.get('webpage_url'),
                        'description': caption,
                        'thumbnail': info.get('thumbnail'),
                        'height': info.get('height')
                    }
    except Exception as e:
        return {
            'title': '',
            'files': [],
            'error': f"Instagram downloader error: {e}"
        }


async def handle_instagram(url, temp_dir):
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir, exist_ok=True)

    cookies = load_cookies_dict('instagram')
    requested_index = parse_img_index(url)

    resolved_url = url
    shortcode, _ = extract_shortcode(resolved_url)
    if not shortcode and '/share/' in url:
        resolved_url = await _resolve_share_url(url, cookies)
        shortcode, _ = extract_shortcode(resolved_url)
        if requested_index == 0:
            requested_index = parse_img_index(resolved_url)

    post = None
    if shortcode:
        pk = shortcode_to_pk(shortcode)
        if pk is not None:
            post = await _fetch_post_metadata(shortcode, pk, cookies)

    if post and post.get('items'):
        items = post['items']

        # Case 1: Multi-slide carousel (photos, videos, or mixed)
        if len(items) > 1:
            carousel_dir = os.path.join(temp_dir, f"carousel_{uuid.uuid4().hex[:10]}")
            os.makedirs(carousel_dir, exist_ok=True)

            sem = asyncio.Semaphore(5)
            timeout = aiohttp.ClientTimeout(total=60)

            async def _fetch_one(idx, item):
                async with sem:
                    base = os.path.join(carousel_dir, f"{post['shortcode']}_{idx + 1}")
                    path = await _download_media_item(session, item['url'], base, item['is_video'])
                    if not path:
                        return None
                    return {
                        'orig_idx': idx,
                        'path': path,
                        'is_video': item['is_video'],
                        'thumbnail': item.get('thumbnail'),
                        'height': item.get('height'),
                    }

            async with aiohttp.ClientSession(timeout=timeout) as session:
                downloaded = await asyncio.gather(
                    *[_fetch_one(i, it) for i, it in enumerate(items)]
                )

            slides = [s for s in downloaded if s is not None]
            if slides:
                # Map requested 0-based index to the matching downloaded slide
                clamped_req = max(0, min(requested_index, len(items) - 1))
                start_index = 0
                for pos, s in enumerate(slides):
                    if s['orig_idx'] == clamped_req:
                        start_index = pos
                        break
                    if s['orig_idx'] < clamped_req:
                        start_index = pos

                for s in slides:
                    s.pop('orig_idx', None)

                return {
                    'title': post['caption'],
                    'description': post['caption'],
                    'files': [s['path'] for s in slides],
                    'slides': slides,
                    'start_index': start_index,
                    'carousel_dir': carousel_dir,
                    'uploader': post['uploader'],
                    'uploader_id': post['uploader_id'],
                    'webpage_url': post['webpage_url'],
                    'thumbnail': slides[start_index].get('thumbnail'),
                    'height': slides[start_index].get('height'),
                }
            else:
                shutil.rmtree(carousel_dir, ignore_errors=True)

        # Case 2: Single photo post
        elif len(items) == 1 and not items[0]['is_video']:
            timeout = aiohttp.ClientTimeout(total=30)
            base = os.path.join(temp_dir, f"{post['shortcode']}_{uuid.uuid4().hex[:6]}")
            async with aiohttp.ClientSession(timeout=timeout) as session:
                filepath = await _download_media_item(session, items[0]['url'], base, is_video=False)
            if filepath:
                return {
                    'title': post['caption'],
                    'description': post['caption'],
                    'files': [filepath],
                    'uploader': post['uploader'],
                    'uploader_id': post['uploader_id'],
                    'webpage_url': post['webpage_url'],
                    'thumbnail': items[0].get('thumbnail'),
                    'height': items[0].get('height'),
                }

    # Case 3: Single video / Reel or API fallback -> use yt-dlp
    ytdl_res = await _handle_via_ytdl(resolved_url, temp_dir, requested_index=requested_index)
    if ytdl_res and ytdl_res.get('files'):
        return ytdl_res

    # Final fallback: if yt-dlp failed on a single video but API gave us a direct video URL
    if post and len(post.get('items') or []) == 1 and post['items'][0]['is_video']:
        item = post['items'][0]
        timeout = aiohttp.ClientTimeout(total=60)
        base = os.path.join(temp_dir, f"{post['shortcode']}_{uuid.uuid4().hex[:6]}")
        async with aiohttp.ClientSession(timeout=timeout) as session:
            filepath = await _download_media_item(session, item['url'], base, is_video=True)
        if filepath:
            return {
                'title': post['caption'],
                'description': post['caption'],
                'files': [filepath],
                'uploader': post['uploader'],
                'uploader_id': post['uploader_id'],
                'webpage_url': post['webpage_url'],
                'thumbnail': item.get('thumbnail'),
                'height': item.get('height'),
            }

    return ytdl_res
