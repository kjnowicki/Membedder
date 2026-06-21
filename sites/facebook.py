import os
import asyncio
import yt_dlp

def get_ytdl_opts(download_path):
    opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': os.path.join(download_path, '%(id)s.%(ext)s'),
        'max_filesize': 100 * 1024 * 1024,
        'quiet': True,
        'no_warnings': True,
    }
    # Check for facebook cookies
    cookie_file = 'cookies/facebook.com_cookies.txt'
    if os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file
    return opts

async def handle_facebook(url, temp_dir):
    loop = asyncio.get_event_loop()
    ydl_opts = get_ytdl_opts(temp_dir)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            if not info:
                return {
                    'title': '',
                    'files': [],
                    'error': 'Failed to extract Facebook post info'
                }
            
            caption = info.get('description') or info.get('title') or ""
            
            if info.get('_type') == 'playlist':
                files = []
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
                return {
                    'title': caption,
                    'files': files,
                    'uploader': info.get('uploader'),
                    'uploader_id': info.get('uploader_id'),
                    'webpage_url': info.get('webpage_url'),
                    'description': caption,
                    'thumbnail': info.get('thumbnail'),
                    'height': info.get('height')
                }
            else:
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename):
                    return {
                        'title': caption,
                        'files': [filename],
                        'uploader': info.get('uploader'),
                        'uploader_id': info.get('uploader_id'),
                        'webpage_url': info.get('webpage_url'),
                        'description': caption,
                        'thumbnail': info.get('thumbnail'),
                        'height': info.get('height')
                    }
                else:
                    info_id = info.get('id')
                    if info_id:
                        for f in os.listdir(temp_dir):
                            if f.startswith(info_id):
                                return {
                                    'title': caption,
                                    'files': [os.path.join(temp_dir, f)],
                                    'uploader': info.get('uploader'),
                                    'uploader_id': info.get('uploader_id'),
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
                        'uploader_id': info.get('uploader_id'),
                        'webpage_url': info.get('webpage_url'),
                        'description': caption,
                        'thumbnail': info.get('thumbnail'),
                        'height': info.get('height')
                    }
    except Exception as e:
        return {
            'title': '',
            'files': [],
            'error': f"Facebook downloader error: {e}"
        }
