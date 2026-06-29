import os
import json

def json_to_netscape(json_path, netscape_path):
    """
    Converts a JSON cookie file (from browser extensions like EditThisCookie)
    to a Netscape-formatted cookies text file for yt-dlp.
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        if not isinstance(cookies, list):
            return False
            
        with open(netscape_path, 'w', encoding='utf-8') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# http://curl.haxx.se/rfc/cookie_spec.html\n")
            f.write("# This is a generated file! Do not edit.\n\n")
            for c in cookies:
                if not isinstance(c, dict):
                    continue
                domain = c.get('domain', '')
                if not domain:
                    continue
                
                flag = "TRUE" if domain.startswith('.') else "FALSE"
                path = c.get('path', '/')
                secure = "TRUE" if c.get('secure', False) else "FALSE"
                
                # Expiration
                expires = c.get('expirationDate') or c.get('expires')
                if expires is None:
                    expires = 0
                else:
                    try:
                        expires = int(expires)
                    except ValueError:
                        expires = 0
                        
                name = c.get('name', '')
                value = c.get('value', '')
                
                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
        return True
    except Exception as e:
        print(f"[COOKIES] Error converting JSON cookie file {json_path}: {e}")
        return False

def get_cookie_opts(platform, temp_dir):
    """
    Returns cookie dictionary options ('cookiefile' or 'cookiesfrombrowser')
    to merge with ytdl_opts.
    """
    opts = {}
    
    # 1. Define candidate files based on the platform
    candidate_files = []
    if platform == 'instagram':
        candidate_files = [
            'cookies/instagram.com_cookies.txt',
            'cookies/www.instagram.com_cookies.txt',
            'cookies/instagram.com_cookies.json',
            'cookies/www.instagram.com_cookies.json',
        ]
    elif platform == 'facebook':
        candidate_files = [
            'cookies/facebook.com_cookies.txt',
            'cookies/www.facebook.com_cookies.txt',
            'cookies/facebook.com_cookies.json',
            'cookies/www.facebook.com_cookies.json',
        ]
    elif platform == 'x':
        candidate_files = [
            'cookies/x.com_cookies.txt',
            'cookies/www.x.com_cookies.txt',
            'cookies/twitter.com_cookies.txt',
            'cookies/www.twitter.com_cookies.txt',
            'cookies/x.com_cookies.json',
            'cookies/www.x.com_cookies.json',
            'cookies/twitter.com_cookies.json',
            'cookies/www.twitter.com_cookies.json',
        ]
    elif platform == 'reddit':
        candidate_files = [
            'cookies/reddit.com_cookies.txt',
            'cookies/www.reddit.com_cookies.txt',
            'cookies/reddit.com_cookies.json',
            'cookies/www.reddit.com_cookies.json',
        ]

    # 2. Check if any candidate file exists
    for cookie_file in candidate_files:
        if os.path.exists(cookie_file):
            if cookie_file.endswith('.json'):
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)
                temp_cookie_name = f"{platform}_cookies_converted.txt"
                temp_cookie_path = os.path.join(temp_dir, temp_cookie_name)
                if json_to_netscape(cookie_file, temp_cookie_path):
                    opts['cookiefile'] = temp_cookie_path
                    return opts
            else:
                opts['cookiefile'] = cookie_file
                return opts

    # 3. Fallback to COOKIES_FROM_BROWSER if configured in .env
    cookies_from_browser = os.environ.get('COOKIES_FROM_BROWSER')
    if cookies_from_browser:
        if ':' in cookies_from_browser:
            browser, profile = cookies_from_browser.split(':', 1)
            opts['cookiesfrombrowser'] = (browser, profile)
        else:
            opts['cookiesfrombrowser'] = (cookies_from_browser,)
            
    return opts


async def ytdl_extract_info(ydl_opts, url, loop):
    """
    Runs extract_info using yt_dlp.YoutubeDL. If it fails with a cookie-related error
    (such as browser database locked/unable to copy), it automatically retries
    without cookies so public posts can still download successfully.
    """
    import yt_dlp
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
    except Exception as e:
        err_msg = str(e)
        has_cookies = 'cookiefile' in ydl_opts or 'cookiesfrombrowser' in ydl_opts
        is_cookie_error = (
            'cookie' in err_msg.lower() or 
            'could not copy' in err_msg.lower() or 
            'permission' in err_msg.lower()
        )
        
        if has_cookies and is_cookie_error:
            print(f"[COOKIES] Cookie error encountered: {e}. Retrying without cookies...")
            opts_no_cookies = ydl_opts.copy()
            opts_no_cookies.pop('cookiefile', None)
            opts_no_cookies.pop('cookiesfrombrowser', None)
            with yt_dlp.YoutubeDL(opts_no_cookies) as ydl:
                return await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
        raise e

