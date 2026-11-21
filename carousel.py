import os
import shutil
import asyncio
import discord


class MediaCarouselView(discord.ui.View):
    """
    Interactive Discord carousel view for multi-slide media posts (photos and/or videos).
    Supports starting at an arbitrary 0-based index (e.g., from Instagram's img_index query param)
    and cleans up its temporary media directory upon timeout.
    """

    def __init__(
        self,
        slides,
        start_index=0,
        author_name="",
        post_url="",
        description="",
        color=0xE1306C,
        cleanup_dir=None,
        timeout=1800,
    ):
        super().__init__(timeout=timeout)
        self.slides = slides
        self.index = max(0, min(start_index, len(slides) - 1)) if slides else 0
        self.author_name = author_name
        self.post_url = post_url
        self.description = description
        self.color = color
        self.cleanup_dir = cleanup_dir
        self.message = None
        self._lock = asyncio.Lock()

        self._use_jump_buttons = len(self.slides) > 3
        self._build_buttons()
        self._sync_buttons()

    def _build_buttons(self):
        if self._use_jump_buttons:
            self.first_btn = discord.ui.Button(
                emoji="⏮️",
                style=discord.ButtonStyle.secondary,
            )
            self.first_btn.callback = self._on_first
            self.add_item(self.first_btn)
        else:
            self.first_btn = None

        self.prev_btn = discord.ui.Button(
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
        )
        self.prev_btn.callback = self._on_prev
        self.add_item(self.prev_btn)

        self.counter_btn = discord.ui.Button(
            label=f"{self.index + 1} / {len(self.slides)}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.add_item(self.counter_btn)

        self.next_btn = discord.ui.Button(
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
        )
        self.next_btn.callback = self._on_next
        self.add_item(self.next_btn)

        if self._use_jump_buttons:
            self.last_btn = discord.ui.Button(
                emoji="⏭️",
                style=discord.ButtonStyle.secondary,
            )
            self.last_btn.callback = self._on_last
            self.add_item(self.last_btn)
        else:
            self.last_btn = None

    def _sync_buttons(self):
        at_start = self.index <= 0
        at_end = self.index >= len(self.slides) - 1

        if self.first_btn is not None:
            self.first_btn.disabled = at_start
        self.prev_btn.disabled = at_start
        self.counter_btn.label = f"{self.index + 1} / {len(self.slides)}"
        self.next_btn.disabled = at_end
        if self.last_btn is not None:
            self.last_btn.disabled = at_end

    def _disable_all_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    def build_slide_payload(self):
        """
        Builds (embed, files) for the currently selected slide.
        Uses unique per-slide filenames so Discord clients do not serve a cached attachment.
        """
        slide = self.slides[self.index]
        embed = discord.Embed(
            description=self.description,
            color=self.color,
        )
        if self.author_name:
            embed.set_author(name=self.author_name, url=self.post_url or None)

        files = []
        footer_text = f"{self.index + 1} / {len(self.slides)}"

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
                filename = f"slide_{self.index + 1}{ext.lower()}"
                files.append(discord.File(path, filename=filename))
                if not slide.get('is_video'):
                    embed.set_image(url=f"attachment://{filename}")

        embed.set_footer(text=footer_text)
        return embed, files

    async def _go_to(self, interaction: discord.Interaction, target_index: int):
        await interaction.response.defer()
        async with self._lock:
            target_index = max(0, min(target_index, len(self.slides) - 1))
            slide = self.slides[target_index]

            if not slide.get('exceeded_limit'):
                path = slide.get('path')
                if not path or not os.path.exists(path):
                    self._disable_all_buttons()
                    try:
                        await interaction.edit_original_response(view=self)
                    except Exception:
                        pass
                    try:
                        await interaction.followup.send(
                            "This carousel has expired. Please re-post the link to view it again.",
                            ephemeral=True,
                        )
                    except Exception:
                        pass
                    self.stop()
                    return

            self.index = target_index
            self._sync_buttons()
            embed, files = self.build_slide_payload()
            await interaction.edit_original_response(
                embed=embed,
                attachments=files,
                view=self,
            )

    async def _on_first(self, interaction: discord.Interaction):
        await self._go_to(interaction, 0)

    async def _on_prev(self, interaction: discord.Interaction):
        await self._go_to(interaction, self.index - 1)

    async def _on_next(self, interaction: discord.Interaction):
        await self._go_to(interaction, self.index + 1)

    async def _on_last(self, interaction: discord.Interaction):
        await self._go_to(interaction, len(self.slides) - 1)

    def cleanup_files(self):
        if self.cleanup_dir and os.path.exists(self.cleanup_dir):
            shutil.rmtree(self.cleanup_dir, ignore_errors=True)

    async def on_timeout(self):
        self._disable_all_buttons()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
        self.cleanup_files()
