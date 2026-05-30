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

def get_ytdl_opts(download_path):
    return {
        'format': 'best',
        'outtmpl': os.path.join(download_path, '%(id)s.%(ext)s'),
        'max_filesize': 25 * 1024 * 1024,
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
                    discord_file = discord.File(filename)
                    await message.reply(file=discord_file, mention_author=False)
                    await message.edit(suppress=True)
                    os.remove(filename)
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