import os
import re
import shutil
import asyncio
import subprocess
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Import platform handlers
from sites.instagram import handle_instagram
from sites.x import handle_x
from sites.reddit import handle_reddit
from sites.facebook import handle_facebook
from carousel import MediaCarouselView

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

URL_REGEX = r'(https?://(?:www\.|m\.)?(?:instagram\.com|x\.com|twitter\.com|reddit\.com|old\.reddit\.com|v\.redd\.it|facebook\.com|fb\.watch)/[^\s<>]+)'

def truncate_text(text, max_chars=200, max_lines=3):
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        truncated = "\n".join(lines[:max_lines])
        if not truncated.endswith("[...]"):
            text = truncated + "\n[...]"
        else:
            text = truncated
            
    if len(text) > max_chars:
        text = text[:max_chars - 6].rstrip()
        text += " [...]"
    return text

async def compress_video(input_path, output_path, target_size_mb=24):
    target_size_bytes = target_size_mb * 1024 * 1024

    cmd_probe = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{input_path}"'
    probe_output = subprocess.check_output(cmd_probe, shell=True).decode('utf-8').strip()
    duration = float(probe_output)

    total_bitrate = (target_size_bytes * 8) / duration
    audio_bitrate = 128000  # 128 kbps
    video_bitrate = total_bitrate - audio_bitrate
    if video_bitrate < 100000:
        video_bitrate = 100000

    ffmpeg_pass1 = (
        f'ffmpeg -y -i "{input_path}" -c:v libx264 -b:v {int(video_bitrate)} '
        f'-pass 1 -an -f null NULL'
    )
    ffmpeg_pass2 = (
        f'ffmpeg -y -i "{input_path}" -c:v libx264 -b:v {int(video_bitrate)} '
        f'-pass 2 -c:a aac -b:a {audio_bitrate} "{output_path}"'
    )

    p1 = await asyncio.create_subprocess_shell(ffmpeg_pass1, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await p1.communicate()
    p2 = await asyncio.create_subprocess_shell(ffmpeg_pass2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await p2.communicate()

    for log_file in os.listdir('.'):
        if log_file.startswith('ffmpeg2pass'):
            os.remove(log_file)

async def prepare_media_file(filepath, height_hint=None):
    """
    Validates a downloaded media file against Discord upload size limits.
    Compresses .mp4 videos between 9MB and 100MB, and flags videos >100MB.
    Returns (final_filepath_or_None, exceeded_limit, height).
    """
    if not filepath or not os.path.exists(filepath):
        return None, False, height_hint

    file_size = os.path.getsize(filepath)
    max_discord_size = 9 * 1024 * 1024
    final_filepath = filepath
    height = height_hint

    if filepath.lower().endswith('.mp4') and file_size > max_discord_size:
        if file_size <= 100 * 1024 * 1024:
            compressed_filename = filepath.replace(".mp4", "_compressed.mp4")
            await compress_video(filepath, compressed_filename, target_size_mb=9)
            if os.path.exists(compressed_filename):
                os.remove(filepath)
                final_filepath = compressed_filename
        else:
            if not height:
                try:
                    cmd_probe = f'ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=noprint_wrappers=1:nokey=1 "{filepath}"'
                    probe_output = subprocess.check_output(cmd_probe, shell=True).decode('utf-8').strip()
                    if probe_output.isdigit():
                        height = int(probe_output)
                except Exception as e:
                    print(f"Error probing height for large file {filepath}: {e}")
            return None, True, height

    return final_filepath, False, height


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    if not os.path.exists('temp'):
        os.makedirs('temp')
    else:
        for entry in os.listdir('temp'):
            if entry.startswith('carousel_'):
                shutil.rmtree(os.path.join('temp', entry), ignore_errors=True)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    match = re.search(URL_REGEX, message.content)
    if match:
        url = match.group(0)

        async with message.channel.typing():
            try:
                res = None
                if "instagram.com" in url:
                    res = await handle_instagram(url, 'temp')
                elif "x.com" in url or "twitter.com" in url:
                    res = await handle_x(url, 'temp')
                elif "reddit.com" in url or "old.reddit.com" in url or "v.redd.it" in url:
                    res = await handle_reddit(url, 'temp')
                elif "facebook.com" in url or "fb.watch" in url:
                    res = await handle_facebook(url, 'temp')

                if res:
                    if res.get('error'):
                        print(f"Error handling URL {url}: {res['error']}")

                    # Interactive carousel flow for multi-slide posts (e.g. Instagram photo/mixed carousels)
                    raw_slides = res.get('slides')
                    if raw_slides and len(raw_slides) > 1:
                        raw_start_index = res.get('start_index', 0)
                        processed_slides = []
                        adjusted_start_index = 0
                        for idx, slide in enumerate(raw_slides):
                            final_path, slide_exceeded, slide_height = await prepare_media_file(
                                slide.get('path'),
                                height_hint=slide.get('height'),
                            )
                            entry = None
                            if slide_exceeded:
                                if slide.get('path') and os.path.exists(slide['path']):
                                    try:
                                        os.remove(slide['path'])
                                    except Exception:
                                        pass
                                entry = {
                                    'path': None,
                                    'is_video': True,
                                    'exceeded_limit': True,
                                    'thumbnail': slide.get('thumbnail'),
                                    'height': slide_height,
                                }
                            elif final_path:
                                entry = {
                                    'path': final_path,
                                    'is_video': bool(slide.get('is_video')),
                                    'exceeded_limit': False,
                                    'thumbnail': slide.get('thumbnail'),
                                    'height': slide_height,
                                }
                            if entry is not None:
                                if idx == raw_start_index:
                                    adjusted_start_index = len(processed_slides)
                                elif idx < raw_start_index:
                                    adjusted_start_index = len(processed_slides)
                                processed_slides.append(entry)

                        if processed_slides:
                            raw_desc = res.get('description') or res.get('title') or ""
                            cleaned_desc = truncate_text(raw_desc)
                            uploader = res.get('uploader')
                            uploader_id = res.get('uploader_id')
                            author_name = uploader or uploader_id or "Instagram User"
                            if uploader and uploader_id and uploader.lower() != uploader_id.lower():
                                author_name = f"{uploader} (@{uploader_id})"
                            post_url = res.get('webpage_url') or url

                            view = MediaCarouselView(
                                slides=processed_slides,
                                start_index=adjusted_start_index,
                                author_name=author_name,
                                post_url=post_url,
                                description=cleaned_desc,
                                color=0xE1306C,
                                cleanup_dir=res.get('carousel_dir'),
                            )
                            try:
                                embed, initial_files = view.build_slide_payload()
                                sent_msg = await message.reply(
                                    embed=embed,
                                    files=initial_files,
                                    view=view,
                                    mention_author=False,
                                )
                                view.message = sent_msg
                            except Exception:
                                view.cleanup_files()
                                raise

                            await message.edit(suppress=True)
                        else:
                            if res.get('carousel_dir') and os.path.exists(res['carousel_dir']):
                                shutil.rmtree(res['carousel_dir'], ignore_errors=True)
                            print(f"No valid carousel slides found for URL: {url}")
                    else:
                        exceeded_limit = False
                        files_to_upload = []
                        for filepath in res.get('files', []):
                            final_filepath, file_exceeded, probed_height = await prepare_media_file(
                                filepath,
                                height_hint=res.get('height'),
                            )
                            if file_exceeded:
                                exceeded_limit = True
                                if probed_height and not res.get('height'):
                                    res['height'] = probed_height
                                continue
                            if final_filepath:
                                files_to_upload.append(final_filepath)

                        if files_to_upload or exceeded_limit:
                            discord_files = [discord.File(f) for f in files_to_upload]
                            
                            # Custom layout for X/Twitter posts
                            if "x.com" in url or "twitter.com" in url:
                                raw_desc = res.get('description') or res.get('title') or ""
                                # Remove trailing t.co short links if they are at the end
                                cleaned_desc = re.sub(r'\s*https://t\.co/\w+\s*$', '', raw_desc)
                                cleaned_desc = truncate_text(cleaned_desc)
                                
                                embed = discord.Embed(
                                    description=cleaned_desc,
                                    color=0x1DA1F2  # X / Twitter blue color
                                )
                                
                                uploader = res.get('uploader')
                                uploader_id = res.get('uploader_id')
                                author_name = uploader or uploader_id or "X User"
                                if uploader and uploader_id and uploader.lower() != uploader_id.lower():
                                    author_name = f"{uploader} (@{uploader_id})"
                                    
                                post_url = res.get('webpage_url') or url
                                embed.set_author(name=author_name, url=post_url)
                                
                                if exceeded_limit:
                                    thumbnail_url = res.get('thumbnail')
                                    if thumbnail_url:
                                        embed.set_image(url=thumbnail_url)
                                    height = res.get('height')
                                    footer_text = "Video exceeded 100MB, view it at original link"
                                    if height:
                                        footer_text += f" ({height}px)"
                                    embed.set_footer(text=footer_text)
                                    
                                await message.reply(embed=embed, files=discord_files, mention_author=False)
                            elif "reddit.com" in url or "old.reddit.com" in url or "v.redd.it" in url:
                                raw_desc = res.get('title') or ""
                                cleaned_desc = truncate_text(raw_desc)
                                
                                embed = discord.Embed(
                                    description=cleaned_desc,
                                    color=0xFF4500  # Reddit Orangered color
                                )
                                
                                subreddit = res.get('subreddit') or "r/reddit"
                                post_url = res.get('webpage_url') or url
                                embed.set_author(name=subreddit, url=post_url)
                                
                                if exceeded_limit:
                                    thumbnail_url = res.get('thumbnail')
                                    if thumbnail_url:
                                        embed.set_image(url=thumbnail_url)
                                    height = res.get('height')
                                    footer_text = "Video exceeded 100MB, view it at original link"
                                    if height:
                                        footer_text += f" ({height}px)"
                                    embed.set_footer(text=footer_text)
                                    
                                await message.reply(embed=embed, files=discord_files, mention_author=False)
                            elif "instagram.com" in url:
                                raw_desc = res.get('description') or res.get('title') or ""
                                cleaned_desc = truncate_text(raw_desc)
                                
                                embed = discord.Embed(
                                    description=cleaned_desc,
                                    color=0xE1306C  # Instagram Cherry Pink color
                                )
                                
                                uploader = res.get('uploader')
                                uploader_id = res.get('uploader_id')
                                author_name = uploader or uploader_id or "Instagram User"
                                if uploader and uploader_id and uploader.lower() != uploader_id.lower():
                                    author_name = f"{uploader} (@{uploader_id})"
                                    
                                post_url = res.get('webpage_url') or url
                                embed.set_author(name=author_name, url=post_url)
                                
                                if exceeded_limit:
                                    thumbnail_url = res.get('thumbnail')
                                    if thumbnail_url:
                                        embed.set_image(url=thumbnail_url)
                                    height = res.get('height')
                                    footer_text = "Video exceeded 100MB, view it at original link"
                                    if height:
                                        footer_text += f" ({height}px)"
                                    embed.set_footer(text=footer_text)
                                elif len(files_to_upload) == 1 and files_to_upload[0].lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                                    img_name = os.path.basename(files_to_upload[0])
                                    embed.set_image(url=f"attachment://{img_name}")
                                    
                                await message.reply(embed=embed, files=discord_files, mention_author=False)
                            elif "facebook.com" in url or "fb.watch" in url:
                                raw_desc = res.get('description') or res.get('title') or ""
                                cleaned_desc = truncate_text(raw_desc)
                                
                                embed = discord.Embed(
                                    description=cleaned_desc,
                                    color=0x1877F2  # Facebook Blue color
                                )
                                
                                uploader = res.get('uploader')
                                uploader_id = res.get('uploader_id')
                                author_name = uploader or uploader_id or "Facebook User"
                                if uploader and uploader_id and uploader.lower() != uploader_id.lower():
                                    author_name = f"{uploader} (@{uploader_id})"
                                    
                                post_url = res.get('webpage_url') or url
                                embed.set_author(name=author_name, url=post_url)
                                
                                if exceeded_limit:
                                    thumbnail_url = res.get('thumbnail')
                                    if thumbnail_url:
                                        embed.set_image(url=thumbnail_url)
                                    height = res.get('height')
                                    footer_text = "Video exceeded 100MB, view it at original link"
                                    if height:
                                        footer_text += f" ({height}px)"
                                    embed.set_footer(text=footer_text)
                                    
                                await message.reply(embed=embed, files=discord_files, mention_author=False)
                            else:
                                if exceeded_limit:
                                    embed = discord.Embed(
                                        title=res.get('title') or "Video",
                                        url=res.get('webpage_url') or url
                                    )
                                    thumbnail_url = res.get('thumbnail')
                                    if thumbnail_url:
                                        embed.set_image(url=thumbnail_url)
                                    height = res.get('height')
                                    footer_text = "Video exceeded 100MB, view it at original link"
                                    if height:
                                        footer_text += f" ({height}px)"
                                    embed.set_footer(text=footer_text)
                                    await message.reply(embed=embed, mention_author=False)
                                else:
                                    content = res.get('title')
                                    await message.reply(content=content, files=discord_files, mention_author=False)
                                
                            await message.edit(suppress=True)
                            for f in res.get('files', []):
                                if os.path.exists(f):
                                    try:
                                        os.remove(f)
                                    except Exception:
                                        pass
                            for f in files_to_upload:
                                if os.path.exists(f):
                                    try:
                                        os.remove(f)
                                    except Exception:
                                        pass
                        else:
                            print(f"No files were extracted/found for URL: {url}")

            except Exception as e:
                print(f"General error: {e}")
            await message.clear_reactions()
    await bot.process_commands(message)

load_dotenv(override=False)
TOKEN = str(os.environ.get('DISCORD_BOT_TOKEN'))
bot.run(TOKEN)