import os
import re
import json
import html
import asyncio
import aiohttp
import http.cookiejar
from urllib.parse import urlparse, urlunparse

def load_reddit_cookies():
    cookies = {}
    found_any = False
    for cookie_file in ['cookies/www.reddit.com_cookies.txt', 'cookies/www.reddit.com_cookies.json']:
        if os.path.exists(cookie_file):
            found_any = True
            # Try loading as JSON first (common format from browser extensions)
            try:
                with open(cookie_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and 'name' in item and 'value' in item:
                                cookies[item['name']] = item['value']
                        if cookies:
                            return cookies, True
            except json.JSONDecodeError:
                # Not a JSON file, fall through to Netscape format
                pass
            except Exception as e:
                print(f"[REDDIT] Error reading JSON cookies from {cookie_file}: {e}")

            # Fall back to MozillaCookieJar (Netscape text format)
            try:
                cookie_jar = http.cookiejar.MozillaCookieJar(cookie_file)
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                for cookie in cookie_jar:
                    cookies[cookie.name] = cookie.value
                if cookies:
                    return cookies, True
            except Exception as e:
                print(f"[REDDIT] Error loading cookies from {cookie_file} using MozillaCookieJar: {e}")
    return cookies, found_any

async def get_reddit_json(url):
    # Normalize URL: extract subreddit and post ID
    # e.g., https://www.reddit.com/r/aww/comments/18x7p68/my_dog_waiting_for_me/ -> sub='aww', post_id='18x7p68'
    match = re.search(r'/r/([^/]+)/comments/([^/]+)', url)
    if not match:
        match = re.search(r'/comments/([^/]+)', url)
        if not match:
            # Maybe it is a short link like https://v.redd.it/id
            # Or https://reddit.com/id
            # We can try to follow redirect first
            return await resolve_and_get_reddit_json(url)
        sub = 'all'
        post_id = match.group(1)
    else:
        sub, post_id = match.group(1), match.group(2)

    return await fetch_reddit_json_by_id(sub, post_id)

async def resolve_and_get_reddit_json(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    cookies, _ = load_reddit_cookies()

    async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                resolved_url = str(resp.url)
                return await get_reddit_json(resolved_url)
        except Exception as e:
            print(f"[REDDIT] Failed to resolve URL {url}: {e}")
    return None

async def fetch_reddit_json_by_id(sub, post_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # Tier 1: Try using cookies if available
    cookies, files_exist = load_reddit_cookies()

    if not files_exist:
        print("[REDDIT INFO] No cookie files found. (cookies/www.reddit.com_cookies.txt/json)")

    if cookies:
        api_url = f"https://www.reddit.com/r/{sub}/comments/{post_id}/.json"
        async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
            try:
                async with session.get(api_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    
                    if resp.status in [401, 403, 429]:
                        print(f"[REDDIT WARNING] Request with cookies returned status {resp.status}. Your cookies might be expired or invalid.")
                    else:
                        print(f"[REDDIT] Fetch with cookies returned status {resp.status}")
            except Exception as e:
                print(f"[REDDIT] Fetch with cookies failed: {e}")


    # Tier 2: Try using Client ID & Secret if configured in .env
    client_id = os.environ.get('REDDIT_CLIENT_ID')
    client_secret = os.environ.get('REDDIT_CLIENT_SECRET')
    if client_id and client_secret:
        auth = aiohttp.BasicAuth(client_id, client_secret)
        data = {'grant_type': 'client_credentials'}
        async with aiohttp.ClientSession(headers={"User-Agent": "Membedder/1.0 by kjnowicki"}) as session:
            try:
                async with session.post('https://www.reddit.com/api/v1/access_token', auth=auth, data=data) as resp:
                    if resp.status == 200:
                        token_data = await resp.json()
                        access_token = token_data.get('access_token')
                        if access_token:
                            oauth_headers = {
                                'User-Agent': 'Membedder/1.0 by kjnowicki',
                                'Authorization': f'Bearer {access_token}'
                            }
                            oauth_url = f'https://oauth.reddit.com/r/{sub}/comments/{post_id}.json'
                            async with session.get(oauth_url, headers=oauth_headers) as oauth_resp:
                                if oauth_resp.status == 200:
                                    return await oauth_resp.json()
                                print(f"OAuth request failed with status {oauth_resp.status}")
            except Exception as e:
                print(f"OAuth request failed: {e}")

    # Tier 3: Direct fetch without credentials (fallback)
    api_url = f"https://www.reddit.com/r/{sub}/comments/{post_id}/.json"
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(api_url) as resp:
                if resp.status == 200:
                    return await resp.json()
                print(f"Direct fetch failed with status {resp.status}")
        except Exception as e:
            print(f"Direct fetch failed: {e}")

    return None

async def parse_dash_manifest(dash_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(dash_url) as resp:
                if resp.status != 200:
                    print(f"Failed to fetch DASH manifest: {resp.status}")
                    return None, None
                content = await resp.text()
        except Exception as e:
            print(f"Failed to fetch DASH manifest: {e}")
            return None, None
              
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        
        base_urls = []
        for elem in root.iter():
            if elem.tag.endswith('BaseURL'):
                base_urls.append(elem.text)
                  
        if not base_urls:
            return None, None
              
        # Filter video and audio filenames
        video_files = []
        audio_files = []
        for url in base_urls:
            if 'AUDIO' in url.upper() or 'audio' in url.lower():
                audio_files.append(url)
            else:
                video_files.append(url)
                  
        # Sort video and audio by resolution/bitrate
        def extract_res(filename):
            match = re.search(r'_(\d+)\.mp4', filename)
            return int(match.group(1)) if match else 0
              
        video_files.sort(key=extract_res, reverse=True)
        audio_files.sort(key=extract_res, reverse=True)
          
        best_video = video_files[0] if video_files else None
        best_audio = audio_files[0] if audio_files else None
          
        # Construct full CDN URLs
        parsed_dash = urlparse(dash_url)
        path_parts = parsed_dash.path.split('/')
        if path_parts:
            path_parts.pop()  # remove DASHPlaylist.mpd
        base_path = '/'.join(path_parts)
        if not base_path.endswith('/'):
            base_path += '/'
              
        video_url = f"{parsed_dash.scheme}://{parsed_dash.netloc}{base_path}{best_video}" if best_video else None
        audio_url = f"{parsed_dash.scheme}://{parsed_dash.netloc}{base_path}{best_audio}" if best_audio else None
          
        return video_url, audio_url
    except Exception as e:
        print(f"Error parsing DASH manifest: {e}")
        return None, None

async def download_file(url, path):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    with open(path, 'wb') as f:
                        while True:
                            chunk = await resp.content.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                else:
                    print(f"Failed to download file from {url}: status {resp.status}")
        except Exception as e:
            print(f"Download error for {url}: {e}")

async def merge_video_audio(video_path, audio_path, output_path):
    ffmpeg_cmd = (
        f'ffmpeg -y -i "{video_path}" -i "{audio_path}" -c:v copy -c:a aac "{output_path}"'
    )
    try:
        p = await asyncio.create_subprocess_shell(ffmpeg_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.communicate()
        return p.returncode == 0
    except Exception as e:
        print(f"FFmpeg merge error: {e}")
        return False

async def handle_reddit(url, temp_dir):
    data = await get_reddit_json(url)
    if not data:
        return {
            'title': '',
            'files': [],
            'error': 'Failed to retrieve Reddit post JSON data.'
        }
    
    try:
        post_data = data[0]['data']['children'][0]['data']
    except (IndexError, KeyError, TypeError) as e:
        return {
            'title': '',
            'files': [],
            'error': f'Failed to parse Reddit post JSON structure: {e}'
        }

    title = post_data.get('title', '')
    files = []

    # Check if it is a native video
    is_video = post_data.get('is_video', False)
    reddit_video = (
        post_data.get('secure_media', {}).get('reddit_video') 
        or post_data.get('media', {}).get('reddit_video')
        if post_data.get('secure_media') or post_data.get('media') 
        else None
    )

    # Check if it's a gallery
    is_gallery = post_data.get('is_gallery', False)

    # Check if it's a single image
    post_url = post_data.get('url', '')
    is_image = False
    if post_url:
        parsed_url = urlparse(post_url)
        path_lower = parsed_url.path.lower()
        if path_lower.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')) or 'i.redd.it' in parsed_url.netloc or 'i.imgur.com' in parsed_url.netloc:
            is_image = True

    # 1. Video post download
    if is_video and reddit_video:
        dash_url_encoded = reddit_video.get('dash_url')
        if dash_url_encoded:
            dash_url = html.unescape(dash_url_encoded)
            video_url, audio_url = await parse_dash_manifest(dash_url)
            
            # Fallback to fallback_url if DASH parsing fails
            if not video_url:
                video_url = reddit_video.get('fallback_url')
            
            if video_url:
                post_id = post_data.get('id', 'reddit_post')
                video_filename = os.path.join(temp_dir, f"{post_id}_temp_video.mp4")
                audio_filename = os.path.join(temp_dir, f"{post_id}_temp_audio.mp4")
                merged_filename = os.path.join(temp_dir, f"{post_id}.mp4")
                
                await download_file(video_url, video_filename)
                
                has_audio = reddit_video.get('has_audio', False) and audio_url
                if has_audio:
                    await download_file(audio_url, audio_filename)
                    if os.path.exists(audio_filename) and os.path.getsize(audio_filename) > 0:
                        success = await merge_video_audio(video_filename, audio_filename, merged_filename)
                        if success and os.path.exists(merged_filename):
                            files.append(merged_filename)
                        else:
                            # Fallback if merge fails
                            os.rename(video_filename, merged_filename)
                            files.append(merged_filename)
                    else:
                        os.rename(video_filename, merged_filename)
                        files.append(merged_filename)
                else:
                    os.rename(video_filename, merged_filename)
                    files.append(merged_filename)
                
                # Cleanup temp files
                if os.path.exists(video_filename):
                    os.remove(video_filename)
                if os.path.exists(audio_filename):
                    os.remove(audio_filename)

    # 2. Gallery post download
    elif is_gallery:
        gallery_data = post_data.get('gallery_data')
        media_metadata = post_data.get('media_metadata')
        if gallery_data and media_metadata:
            items = gallery_data.get('items', [])
            for item in items:
                media_id = item.get('media_id')
                metadata_item = media_metadata.get(media_id)
                if metadata_item and metadata_item.get('status') == 'valid':
                    mime_type = metadata_item.get('m', 'image/jpg')
                    ext = 'png' if 'png' in mime_type else ('gif' if 'gif' in mime_type else 'jpg')
                    media_url = f"https://i.redd.it/{media_id}.{ext}"
                    filename = os.path.join(temp_dir, f"{media_id}.{ext}")
                    
                    await download_file(media_url, filename)
                    if os.path.exists(filename):
                        files.append(filename)

    # 3. Single image post download
    elif is_image and post_url:
        parsed_url = urlparse(post_url)
        filename_part = os.path.basename(parsed_url.path)
        if '.' not in filename_part:
            # Try to get extension from metadata or default to jpg
            filename_part = filename_part + ".jpg"
            
        filename = os.path.join(temp_dir, filename_part)
        await download_file(post_url, filename)
        if os.path.exists(filename):
            files.append(filename)

    # 4. External video download fallback (e.g. streamable, youtube, etc.)
    if not files and post_url:
        post_hint = post_data.get('post_hint', '')
        is_self = post_data.get('is_self', False)
        if not is_self and (post_hint in ['rich:video', 'hosted:video'] or post_data.get('media') or post_data.get('secure_media')):
            import yt_dlp
            from sites.cookies import get_cookie_opts, ytdl_extract_info
            ydl_opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
                'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
                'max_filesize': 100 * 1024 * 1024,
                'quiet': True,
                'no_warnings': True,
            }
            # Check for reddit cookies (handles txt, json, and browser fallbacks)
            cookie_opts = get_cookie_opts('reddit', temp_dir)
            ydl_opts.update(cookie_opts)

            loop = asyncio.get_event_loop()
            try:
                info = await ytdl_extract_info(ydl_opts, post_url, loop)
                if info:
                    clean_opts = ydl_opts.copy()
                    clean_opts.pop('cookiefile', None)
                    clean_opts.pop('cookiesfrombrowser', None)
                    with yt_dlp.YoutubeDL(clean_opts) as ydl:
                        if info.get('_type') == 'playlist':
                            for entry in info.get('entries', []):
                                if entry:
                                    try:
                                        filename = ydl.prepare_filename(entry)
                                        if os.path.exists(filename):
                                            files.append(filename)
                                    except Exception:
                                        entry_id = entry.get('id')
                                        if entry_id:
                                            for f in os.listdir(temp_dir):
                                                if f.startswith(entry_id):
                                                    files.append(os.path.join(temp_dir, f))
                        else:
                            filename = ydl.prepare_filename(info)
                            if os.path.exists(filename):
                                files.append(filename)
                            else:
                                info_id = info.get('id')
                                if info_id:
                                    for f in os.listdir(temp_dir):
                                        if f.startswith(info_id):
                                            files.append(os.path.join(temp_dir, f))
                                            break

            except Exception as e:
                print(f"yt-dlp fallback download failed for {post_url}: {e}")

    # Return structure with title and downloaded files
    thumbnail_url = post_data.get('thumbnail') if post_data.get('thumbnail') and post_data.get('thumbnail').startswith('http') else None
    height = reddit_video.get('height') if reddit_video else None
    if 'info' in locals() and info:
        if not thumbnail_url:
            thumbnail_url = info.get('thumbnail')
        if not height:
            height = info.get('height')

    return {
        'title': title,
        'files': files,
        'subreddit': post_data.get('subreddit_name_prefixed') or f"r/{post_data.get('subreddit')}" if 'post_data' in locals() and post_data else None,
        'webpage_url': f"https://www.reddit.com{post_data.get('permalink')}" if 'post_data' in locals() and post_data and post_data.get('permalink') else url,
        'thumbnail': thumbnail_url,
        'height': height
    }
