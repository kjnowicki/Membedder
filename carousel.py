import os
import time
import asyncio
import discord


def batch_file_entries(entries, max_files=10, max_bytes=8 * 1024 * 1024):
    """
    Groups slide/file dicts (each having a 'path' to an existing file) into batches
    that stay within Discord's per-message file count (<=10) and payload size (<=8MB).
    """
    batches = []
    current_batch = []
    current_bytes = 0

    for entry in entries:
        path = entry.get('path')
        if not path or not os.path.exists(path):
            continue
        size = os.path.getsize(path)
        if current_batch and (
            len(current_batch) >= max_files or current_bytes + size > max_bytes
        ):
            batches.append(current_batch)
            current_batch = []
            current_bytes = 0
        current_batch.append(entry)
        current_bytes += size

    if current_batch:
        batches.append(current_batch)
    return batches


def reorder_slides_from_index(slides, start_index):
    """
    Rotates slides so the slide at start_index appears first, preserving relative order.
    Example: [1, 2, 3] with start_index=1 -> [2, 3, 1].
    """
    if not slides:
        return []
    idx = max(0, min(start_index, len(slides) - 1))
    if idx == 0:
        return list(slides)
    return list(slides[idx:]) + list(slides[:idx])


def build_single_slide_payload(
    slides,
    start_index,
    author_name="",
    post_url="",
    description="",
    color=0xE1306C,
):
    """
    Builds (embed, files) displaying ONLY the single starting slide (respecting img_index),
    used as the initial message for Instagram carousels before the author clicks Show all.
    """
    idx = max(0, min(start_index, len(slides) - 1))
    slide = slides[idx]

    embed = discord.Embed(
        description=description,
        color=color,
    )
    if author_name:
        embed.set_author(name=author_name, url=post_url or None)

    files = []
    footer_text = f"{idx + 1} / {len(slides)}"

    if slide.get('exceeded_limit'):
        thumbnail_url = slide.get('thumbnail')
        if thumbnail_url:
            embed.set_image(url=thumbnail_url)
        limit_note = "Video exceeded 100MB, view it at original link"
        if slide.get('height'):
            limit_note += f" ({slide['height']}px)"
        footer_text = f"{footer_text} • {limit_note}"
    else:
        path = slide.get('path')
        if path and os.path.exists(path):
            _, ext = os.path.splitext(path)
            if not ext:
                ext = '.mp4' if slide.get('is_video') else '.jpg'
            filename = f"slide_{idx + 1}{ext.lower()}"
            files.append(discord.File(path, filename=filename))
            if not slide.get('is_video'):
                embed.set_image(url=f"attachment://{filename}")

    embed.set_footer(text=footer_text)
    return embed, files


def build_expanded_gallery_payload(
    slides,
    start_index,
    author_name="",
    post_url="",
    description="",
    color=0xE1306C,
):
    """
    Builds Option 3 (embed, file_batches): attaches all predownloaded slides as a native
    Discord multi-image gallery/lightbox rotated so the linked img_index slide appears first.
    """
    idx = max(0, min(start_index, len(slides) - 1)) if slides else 0
    ordered = reorder_slides_from_index(slides, idx)
    uploadable = [s for s in ordered if not s.get('exceeded_limit') and s.get('path') and os.path.exists(s['path'])]
    exceeded_slides = [s for s in ordered if s.get('exceeded_limit')]

    embed = discord.Embed(
        description=description,
        color=color,
    )
    if author_name:
        embed.set_author(name=author_name, url=post_url or None)

    if exceeded_slides:
        embed.set_footer(text="Video exceeded 100MB, view it at original link")

    slide_batches = batch_file_entries(uploadable, max_files=10, max_bytes=8 * 1024 * 1024)
    if not slide_batches:
        if exceeded_slides and exceeded_slides[0].get('thumbnail'):
            embed.set_image(url=exceeded_slides[0]['thumbnail'])
        return embed, []

    file_batches = []
    file_counter = 0
    for batch in slide_batches:
        discord_files = []
        for slide in batch:
            file_counter += 1
            _, ext = os.path.splitext(slide['path'])
            if not ext:
                ext = '.mp4' if slide.get('is_video') else '.jpg'
            discord_files.append(
                discord.File(slide['path'], filename=f"media_{file_counter}{ext.lower()}")
            )
        file_batches.append(discord_files)

    return embed, file_batches


class ExpandCarouselButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'ig_expand:(?P<orig_id>\d+)',
):
    """
    Persistent author-only button attached to Instagram carousel embeds.
    Clicking it edits the embed message into Option 3 (all predownloaded photos
    attached as a native Discord gallery), removes the button, and deletes the
    predownloaded temp images.
    """

    tracker = None
    _locks = {}

    def __init__(self, original_message_id: int, total_slides: int = 0):
        self.original_message_id = int(original_message_id)
        label = f"Show all ({total_slides})" if total_slides > 0 else "Show all"
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="🖼️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"ig_expand:{self.original_message_id}",
            )
        )

    @classmethod
    def bind_tracker(cls, tracker):
        cls.tracker = tracker

    @classmethod
    def _get_lock(cls, original_message_id: int):
        lock = cls._locks.get(original_message_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[original_message_id] = lock
        return lock

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        orig_id = int(match.group('orig_id'))
        return cls(orig_id)

    async def callback(self, interaction: discord.Interaction):
        tracker = self.__class__.tracker
        if tracker is None:
            await interaction.response.edit_message(view=None)
            return

        entry = tracker.get(self.original_message_id)
        if not entry or not entry.get('carousel') or time.time() >= float(entry.get('expires_at', 0)):
            # Tracking expired or already expanded: clean up & remove stale button
            if entry and time.time() >= float(entry.get('expires_at', 0)):
                expired_entry = tracker.pop_by_original_message(self.original_message_id)
                if expired_entry:
                    tracker.cleanup_carousel_dir(expired_entry)
                self.__class__._locks.pop(self.original_message_id, None)
            try:
                await interaction.response.edit_message(view=None)
            except Exception:
                pass
            return

        # Only the author of the original message may trigger expansion
        if interaction.user.id != entry.get('author_id'):
            await interaction.response.send_message(
                "Only the author of the original message can expand this post.",
                ephemeral=True,
            )
            return

        lock = self.__class__._get_lock(self.original_message_id)
        if lock.locked():
            await interaction.response.defer()
            return

        async with lock:
            entry = tracker.get(self.original_message_id)
            carousel = entry.get('carousel') if entry else None
            if not entry or not carousel:
                try:
                    await interaction.response.edit_message(view=None)
                except Exception:
                    pass
                return

            await interaction.response.defer()

            slides = carousel.get('slides') or []
            has_any_slide = any(
                s.get('exceeded_limit') or (s.get('path') and os.path.exists(s['path']))
                for s in slides
            )
            if not has_any_slide:
                try:
                    await interaction.edit_original_response(view=None)
                except Exception:
                    pass
                tracker.mark_carousel_expanded(self.original_message_id)
                self.__class__._locks.pop(self.original_message_id, None)
                try:
                    await interaction.followup.send(
                        "The predownloaded images for this post have expired.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return

            embed, file_batches = build_expanded_gallery_payload(
                slides=slides,
                start_index=carousel.get('start_index', 0),
                author_name=carousel.get('author_name', ''),
                post_url=carousel.get('post_url', ''),
                description=carousel.get('description', ''),
                color=carousel.get('color', 0xE1306C),
            )

            try:
                if file_batches:
                    await interaction.edit_original_response(
                        embed=embed,
                        attachments=file_batches[0],
                        view=None,
                    )
                    for extra_batch in file_batches[1:]:
                        if interaction.channel is not None:
                            extra_msg = await interaction.channel.send(files=extra_batch)
                            tracker.add_bot_message_id(self.original_message_id, extra_msg.id)
                else:
                    await interaction.edit_original_response(
                        embed=embed,
                        attachments=[],
                        view=None,
                    )
            finally:
                for batch in file_batches:
                    for f in batch:
                        try:
                            f.close()
                        except Exception:
                            pass

            tracker.mark_carousel_expanded(self.original_message_id)
            self.__class__._locks.pop(self.original_message_id, None)


def create_expand_view(original_message_id: int, total_slides: int):
    view = discord.ui.View(timeout=None)
    view.add_item(ExpandCarouselButton(original_message_id, total_slides))
    return view
