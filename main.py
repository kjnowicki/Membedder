import os
import re
import asyncio
import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

URL_REGEX = r'(https?://(?:www\.)?(?:instagram\.com|x\.com)/[^\s]+)'

import subprocess

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

def get_ytdl_opts(download_path):
    return {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': os.path.join(download_path, '%(id)s.%(ext)s'),
        'max_filesize': 100 * 1024 * 1024,
        'quiet': True,
        'no_warnings': True,
    }

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    if not os.path.exists('temp'):
        os.makedirs('temp')


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    match = re.search(URL_REGEX, message.content)
    if match:
        url = match.group(0)

        async with message.channel.typing():
            try:
                loop = asyncio.get_event_loop()
                ydl_opts = get_ytdl_opts('temp')
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                    filename = ydl.prepare_filename(info)

                if os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    max_discord_size = 9 * 1024 * 1024

                    final_upload_file = filename

                    if file_size > max_discord_size:
                        compressed_filename = filename.replace(".mp4", "_compressed.mp4")
                        await compress_video(filename, compressed_filename, target_size_mb=9)
                        if os.path.exists(compressed_filename):
                            os.remove(filename)
                            final_upload_file = compressed_filename

                    discord_file = discord.File(final_upload_file)
                    await message.reply(file=discord_file, mention_author=False)
                    await message.edit(suppress=True)
                    if os.path.exists(final_upload_file):
                        os.remove(final_upload_file)
                else:
                    print(f"File {filename} was not found!")

            except yt_dlp.utils.DownloadError as e:
                print(f"yt-dlp error: {e}")
            except Exception as e:
                print(f"General error: {e}")
            await message.clear_reactions()
    await bot.process_commands(message)

load_dotenv(override=False)
TOKEN = str(os.environ.get('DISCORD_BOT_TOKEN'))
bot.run(TOKEN)