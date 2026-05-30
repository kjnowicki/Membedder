import os
import re
import asyncio
import discord
from discord.ext import commands
import yt_dlp

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


async def override_with_webhook(message, output_file):
    webhook = await message.channel.create_webhook(name="Media Mirror")
    try:
        await message.delete()
        await webhook.send(
            content=message.content,
            file=discord.File(output_file),
            username=message.author.display_name,
            avatar_url=message.author.display_avatar.url
        )
    finally:
        # 4. Clean up and delete the temporary webhook so you don't hit server limits
        await webhook.delete()

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
                    await message.add_reaction("❌")

            except yt_dlp.utils.DownloadError as e:
                await message.add_reaction("⚠️")
                print(f"yt-dlp error: {e}")
            except Exception as e:
                await message.add_reaction("⚠️")
                print(f"General error: {e}")
    await bot.process_commands(message)

bot.run('MTUwOTk3NTE5NzY3NDExNTI1Ng.GoVM4A.cEy25_Qvas4McUV8JAG6k6cZpPw1rrsIG6LcvA')