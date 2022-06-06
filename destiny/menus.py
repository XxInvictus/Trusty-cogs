from __future__ import annotations

import logging
from typing import Any, List, Optional

import discord

# from discord.ext.commands.errors import BadArgument
from redbot.core.commands import commands
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.vendored.discord.ext import menus

BASE_URL = "https://bungie.net"


log = logging.getLogger("red.Trusty-cogs.destiny")
_ = Translator("Destiny", __file__)


class BasePages(menus.ListPageSource):
    def __init__(self, pages: list):
        super().__init__(pages, per_page=1)
        self.pages = pages
        self.select_options = []
        for count, page in enumerate(pages):
            self.select_options.append(
                discord.SelectOption(
                    label=_("Page {number}").format(number=count + 1),
                    value=count,
                    description=page.title[:50],
                )
            )

    def is_paginating(self):
        return True

    async def format_page(self, menu: menus.MenuPages, page):
        page.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return page


class VaultPages(menus.ListPageSource):
    def __init__(self, pages: list, cog: commands.Cog):
        super().__init__(pages, per_page=1)
        self.pages = pages
        self.select_options = []
        self.cog = cog
        self.current_item_hash = None
        self.current_item_instance = None

        for count, page in enumerate(pages):
            self.select_options.append(
                discord.SelectOption(
                    label=_("Page {number}").format(number=count + 1),
                    value=count,
                    description="figure out how to get the gun name here",
                )
            )

    def is_paginating(self):
        return True

    async def format_page(self, menu: menus.MenuPages, page):
        self.current_item_hash = page["itemHash"]
        self.current_item_instance = page.get("itemInstanceId", None)
        items = await self.cog.get_definition(
            "DestinyInventoryItemDefinition", [self.current_item_hash]
        )
        item_data = items[str(self.current_item_hash)]
        embed = discord.Embed(
            title=item_data.get("displayProperties", {"name": "None"}).get("name")
        )
        if "displayProperties" in item_data:
            embed.set_thumbnail(url=BASE_URL + item_data["displayProperties"]["icon"])
        if item_data.get("screenshot", None):
            embed.set_image(url=BASE_URL + item_data["screenshot"])
        if self.current_item_instance is not None:
            instance_data = await self.cog.get_instanced_item(
                menu.author, self.current_item_instance
            )
            perk_hashes = [i["perkHash"] for i in instance_data["perks"]["data"]["perks"]]
            perk_info = await self.cog.get_definition(
                "DestinyInventoryItemDefinition", perk_hashes
            )
            perk_str = "\n".join(perk["displayProperties"]["name"] for perk in perk_info.values())
            embed.description = perk_str

        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return embed


class StopButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{HEAVY MULTIPLICATION X}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        self.view.stop()
        if interaction.message.flags.ephemeral:
            await interaction.response.edit_message(view=None)
            return
        await interaction.message.delete()


class ForwardButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{BLACK RIGHT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_checked_page(self.view.current_page + 1, interaction)


class BackButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = "\N{BLACK LEFT-POINTING TRIANGLE}\N{VARIATION SELECTOR-16}"

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_checked_page(self.view.current_page - 1, interaction)


class LastItemButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(self.view._source.get_max_pages() - 1, interaction)


class FirstItemButton(discord.ui.Button):
    def __init__(
        self,
        style: discord.ButtonStyle,
        row: Optional[int],
    ):
        super().__init__(style=style, row=row)
        self.style = style
        self.emoji = (
            "\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\N{VARIATION SELECTOR-16}"
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.show_page(0, interaction)


class DestinySelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        index = int(self.values[0])
        await self.view.show_checked_page(index, interaction)


class BaseMenu(discord.ui.View):
    def __init__(
        self,
        source: menus.PageSource,
        cog: commands.Cog,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 180,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            timeout=timeout,
        )
        self.cog = cog
        self.message = message
        self._source = source
        self.ctx = None
        self.current_page = kwargs.get("page_start", 0)
        self.forward_button = ForwardButton(discord.ButtonStyle.grey, 0)
        self.back_button = BackButton(discord.ButtonStyle.grey, 0)
        self.first_item = FirstItemButton(discord.ButtonStyle.grey, 0)
        self.last_item = LastItemButton(discord.ButtonStyle.grey, 0)
        self.stop_button = StopButton(discord.ButtonStyle.red, 0)
        self.add_item(self.stop_button)
        self.add_item(self.first_item)
        self.add_item(self.back_button)
        self.add_item(self.forward_button)
        self.add_item(self.last_item)

        if hasattr(self.source, "select_options"):
            self.select_view = self._get_select_menu()
            self.add_item(self.select_view)
        self.author = None

    @property
    def source(self):
        return self._source

    async def on_timeout(self):
        await self.message.edit(view=None)

    async def start(self, ctx: commands.Context):
        self.ctx = ctx
        # await self.source._prepare_once()
        self.message = await self.send_initial_message(ctx)

    def check_disabled_buttons(self):
        if len(self._source.entries) == 1:
            self.first_item.disabled = True
            self.last_item.disabled = True
            self.back_button.disabled = True
            self.forward_button.disabled = True
            if hasattr(self.source, "select_options"):
                self.select_view.disabled = True
        else:
            self.first_item.disabled = False
            self.last_item.disabled = False
            self.back_button.disabled = False
            self.forward_button.disabled = False
            if hasattr(self.source, "select_options"):
                self.select_view.disabled = False

    def _get_select_menu(self) -> Optional[DestinySelect]:
        # handles modifying the select menu if more than 25 pages are provided
        # this will show the previous 12 and next 13 pages in the select menu
        # based on the currently displayed page. Once you reach close to the max
        # pages it will display the last 25 pages.
        if not hasattr(self.source, "select_options"):
            return None
        if len(self.source.select_options) > 25:
            minus_diff = None
            plus_diff = 25
            if 12 < self.current_page < len(self.source.select_options) - 25:
                minus_diff = self.current_page - 12
                plus_diff = self.current_page + 13
            elif self.current_page >= len(self.source.select_options) - 25:
                minus_diff = len(self.source.select_options) - 25
                plus_diff = None
            options = self.source.select_options[minus_diff:plus_diff]
        else:
            options = self.source.select_options[:25]
        return DestinySelect(options)

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(self._source.format_page, self, page)
        self.check_disabled_buttons()
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def send_initial_message(self, ctx: commands.Context):
        """|coro|
        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.
        This implementation shows the first page of the source.
        """

        page = await self._source.get_page(self.current_page)
        kwargs = await self._get_kwargs_from_page(page)
        self.message = await ctx.send(**kwargs, view=self)
        self.author = ctx.author
        return self.message

    async def show_page(self, page_number: int, interaction: discord.Interaction):
        page = await self._source.get_page(page_number)
        if hasattr(self.source, "select_options") and page_number >= 12:
            self.remove_item(self.select_view)
            self.select_view = self._get_select_menu()
            self.add_item(self.select_view)
        self.current_page = self.source.pages.index(page)
        kwargs = await self._get_kwargs_from_page(page)
        await interaction.response.edit_message(**kwargs, view=self)

    async def show_checked_page(self, page_number: int, interaction: discord.Interaction) -> None:
        max_pages = self._source.get_max_pages()
        try:
            if max_pages is None:
                # If it doesn't give maximum pages, it cannot be checked
                await self.show_page(page_number, interaction)
            elif page_number >= max_pages:
                await self.show_page(0, interaction)
            elif page_number < 0:
                await self.show_page(max_pages - 1, interaction)
            elif max_pages > page_number >= 0:
                await self.show_page(page_number, interaction)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def interaction_check(self, interaction: discord.Interaction):
        """Just extends the default reaction_check to use owner_ids"""
        if interaction.user.id not in (self.author.id,):
            await interaction.response.send_message(
                content=_("You are not authorized to interact with this."), ephemeral=True
            )
            return False
        return True
