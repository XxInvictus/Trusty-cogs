import asyncio
import logging
import re
from datetime import datetime
from typing import Any, List, Optional, Pattern, Union

import discord
from redbot.core import commands
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_list
from redbot.vendored.discord.ext import menus

from .constants import TEAMS
from .errors import NoSchedule
from .helper import DATE_RE
from .player import Player
from .schedule import Schedule
from .standings import Standings

_ = Translator("Hockey", __file__)
log = logging.getLogger("red.trusty-cogs.hockey")


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
        await self.view.message.delete()


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


class SkipForwardButton(discord.ui.Button):
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
        await self.view.show_page(0, skip_next=True, interaction=interaction)


class SkipBackButton(discord.ui.Button):
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
        await self.view.show_page(0, skip_prev=True, interaction=interaction)


class PickTeamButton(discord.ui.Button):
    def __init__(self, style: discord.ButtonStyle, row: Optional[int]):
        super().__init__(style=style, row=row, label=_("Pick Team"))
        self.style = style

    async def callback(self, interaction: discord.Interaction):
        """stops the pagination session."""
        send_msg = await interaction.response.send_message(
            _("Enter the team you would like to filter for.")
        )

        def check(m: discord.Message):
            return m.author == self.view.author

        try:
            msg = await self.view.cog.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            await send_msg.delete()
            return
        potential_teams = msg.clean_content.split()
        teams: List[str] = []
        for team, data in TEAMS.items():
            if "Team" in teams:
                continue
            nick = data["nickname"]
            short = data["tri_code"]
            pattern = fr"{short}\b|" + r"|".join(fr"\b{i}\b" for i in team.split())
            if nick:
                pattern += r"|" + r"|".join(fr"\b{i}\b" for i in nick)
            # log.debug(pattern)
            reg: Pattern = re.compile(fr"\b{pattern}", flags=re.I)
            for pot in potential_teams:
                find = reg.findall(pot)
                if find:
                    teams.append(team)
            self.view.source.team = teams
        try:
            await self.view.source.prepare()
        except NoSchedule:
            return await self.view.ctx.send(self.format_error())
        await self.view.show_page(0, interaction)


class PickDateButton(discord.ui.Button):
    def __init__(self, style: discord.ButtonStyle, row: Optional[int]):
        super().__init__(style=style, row=row, label=_("Change Date"))
        self.style = style

    async def callback(self, interaction: discord.Interaction):
        """stops the pagination session."""
        send_msg = await interaction.response.send_message(
            _("Enter the date you would like to see `YYYY-MM-DD` format is accepted.")
        )

        def check(m: discord.Message):
            return m.author == self.view.author and DATE_RE.search(m.clean_content)

        try:
            msg = await self.view.cog.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            await send_msg.delete()
            return
        search = DATE_RE.search(msg.clean_content)
        if search:
            date_str = f"{search.group(1)}-{search.group(3)}-{search.group(4)}"
            date = datetime.strptime(date_str, "%Y-%m-%d")
            # log.debug(date)
            self.view.source.date = date
            try:
                await self.view.source.prepare()
            except NoSchedule:
                return await self.ctx.send(self.format_error())
            await self.view.show_page(0, interaction)


class HeatmapButton(discord.ui.Button):
    def __init__(self, style: discord.ButtonStyle, row: Optional[int]):
        super().__init__(style=style, row=row, label=_("Heatmap"))
        self.style = style

    async def callback(self, interaction: discord.Interaction):
        """stops the pagination session."""
        mapping = {
            "all": "ev",
            "ev": "5v5",
            "5v5": "sva",
            "sva": "home5v4",
            "home5v4": "away5v4",
            "away5v4": "all",
        }
        if self.view.source.include_gameflow:
            self.view.source.include_gameflow = False
        if not self.view.source.include_heatmap:
            self.view.source.include_heatmap = True
            self.label = _("Heatmap {style}").format(style=self.view.source.style)
            await self.view.show_page(0, interaction)
            return
        else:
            self.view.source.style = mapping[self.view.source.style]
            self.label = _("Heatmap {style}").format(style=self.view.source.style)
            await self.view.show_page(0, interaction)
            return


class GameflowButton(discord.ui.Button):
    def __init__(self, style: discord.ButtonStyle, row: Optional[int]):
        super().__init__(style=style, row=row, label=_("Gameflow"))
        self.style = style

    async def callback(self, interaction: discord.Interaction):
        """stops the pagination session."""
        mapping = {
            (True, "all"): (True, "ev"),
            (True, "ev"): (True, "5v5"),
            (True, "5v5"): (True, "sva"),
            (True, "sva"): (False, "all"),
            (False, "all"): (False, "ev"),
            (False, "ev"): (False, "5v5"),
            (False, "5v5"): (False, "sva"),
            (False, "sva"): (True, "all"),
        }
        if self.view.source.include_heatmap:
            self.view.source.include_heatmap = False
        if not self.view.source.include_gameflow:
            self.view.source.include_gameflow = True
            corsi = "Corsi" if self.view.source.corsi else "Expected Goals"
            strength = self.view.source.strength
            self.label = _("Gameflow {corsi} {strength}").format(corsi=corsi, strength=strength)
            await self.view.show_page(0, interaction)
            return
        else:
            lookup = (self.view.source.corsi, self.view.source.strength)
            corsi_bool, strength = mapping[lookup]
            self.view.source.corsi = corsi_bool
            self.view.source.strength = strength
            corsi = "Corsi" if corsi_bool else "Expected Goals"
            self.label = _("Gameflow {corsi} {strength}").format(corsi=corsi, strength=strength)
            await self.view.show_page(0, interaction)
            return


class HockeySelectGame(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(min_values=1, max_values=1, options=options, placeholder=_("Pick a game"))

    async def callback(self, interaction: discord.Interaction):
        game_id = int(self.values[0])
        await self.view.show_page(0, game_id=game_id, interaction=interaction)


class GamesMenu(discord.ui.View):
    def __init__(
        self,
        source: menus.PageSource,
        cog: Optional[commands.Cog] = None,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 180,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.source = source
        self.message = message
        self.current_page = 0
        self.ctx: commands.Context = None
        self.forward_button = ForwardButton(discord.ButtonStyle.grey, 0)
        self.back_button = BackButton(discord.ButtonStyle.grey, 0)
        self.first_item = SkipBackButton(discord.ButtonStyle.grey, 0)
        self.last_item = SkipForwardButton(discord.ButtonStyle.grey, 0)
        self.stop_button = StopButton(discord.ButtonStyle.red, 0)
        self.pick_team_button = PickTeamButton(discord.ButtonStyle.primary, 1)
        self.change_date_button = PickDateButton(discord.ButtonStyle.primary, 1)
        self.heatmap_button = HeatmapButton(discord.ButtonStyle.primary, 1)
        self.gameflow_button = GameflowButton(discord.ButtonStyle.primary, 1)
        self.add_item(self.first_item)
        self.add_item(self.back_button)
        self.add_item(self.forward_button)
        self.add_item(self.last_item)
        self.add_item(self.stop_button)
        self.add_item(self.pick_team_button)
        self.add_item(self.change_date_button)
        if isinstance(self.source, Schedule):
            self.heatmap_button.label = _("Heatmap {style}").format(style=self.source.style)
            corsi = "Corsi" if self.source.corsi else "Expected Goals"
            self.gameflow_button.label = _("Gameflow {corsi} {strength}").format(
                corsi=corsi, strength=self.source.strength
            )
            self.add_item(self.heatmap_button)
            self.add_item(self.gameflow_button)
        self.select_view: Optional[HockeySelectGame] = None
        self.author = None

    async def on_timeout(self):
        await self.message.edit(view=None)

    async def start(self, ctx: commands.Context):
        await self.source._prepare_once()
        if hasattr(self.source, "select_options") and len(self.source.select_options) > 1:
            self.select_view = HockeySelectGame(self.source.select_options[:25])
            self.add_item(self.select_view)
        self.ctx = ctx
        if isinstance(ctx, discord.Interaction):
            self.author = ctx.user
        else:
            self.author = ctx.author
        self.message = await self.send_initial_message(ctx, ctx.channel)

    async def show_page(
        self,
        page_number: int,
        *,
        interaction: discord.Interaction,
        skip_next: bool = False,
        skip_prev: bool = False,
        game_id: Optional[int] = None,
    ) -> None:
        try:
            page = await self.source.get_page(
                page_number, skip_next=skip_next, skip_prev=skip_prev, game_id=game_id
            )
        except NoSchedule:
            if interaction.response.is_done():
                await interaction.followup.edit(content=self.format_error(), embed=None, view=self)
            else:
                await interaction.response.edit_message(
                    content=self.format_error(), embed=None, view=self
                )
            return
        if hasattr(self.source, "select_options") and len(self.source.select_options) > 1:
            self.remove_item(self.select_view)
            if page_number >= 12:
                self.select_view = HockeySelectGame(
                    self.source.select_options[page_number - 12 : page_number + 13]
                )
            else:
                self.select_view = HockeySelectGame(self.source.select_options[:25])
            self.add_item(self.select_view)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        if interaction.response.is_done():
            await interaction.followup.edit(**kwargs, view=self)
        else:
            await interaction.response.edit_message(**kwargs, view=self)

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(self.source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def send_initial_message(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> discord.Message:
        """|coro|
        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.
        This implementation shows the first page of the source.
        """
        is_slash = False
        if isinstance(ctx, discord.Interaction):
            is_slash = True
            self.author = ctx.user
        else:
            self.author = ctx.author

        try:
            page = await self.source.get_page(0)
        except (IndexError, NoSchedule):
            return await channel.send(self.format_error(), view=self)
        kwargs = await self._get_kwargs_from_page(page)
        if is_slash:
            self.message = await ctx.followup.send(**kwargs, view=self)
        else:
            self.message = await channel.send(**kwargs, view=self)
        return self.message

    def format_error(self):
        team = ""
        if self.source.team:
            team = _("for {teams} ").format(teams=humanize_list(self.source.team))
        msg = _("No schedule could be found {team}in dates between {last_searched}").format(
            team=team, last_searched=self.source._last_searched
        )
        return msg

    async def show_checked_page(self, page_number: int, interaction: discord.Interaction) -> None:
        try:
            await self.show_page(page_number, interaction=interaction)
        except IndexError:
            # An error happened that can be handled, so ignore it.
            pass

    async def interaction_check(self, interaction: discord.Interaction):
        """Just extends the default reaction_check to use owner_ids"""
        if self.author and interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content=_("You are not authorized to interact with this."), ephemeral=True
            )
            return False
        return True


class StandingsPages(menus.ListPageSource):
    def __init__(self, pages: list):
        super().__init__(pages, per_page=1)
        self.pages = pages

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: List[Standings]) -> discord.Embed:
        return await Standings.all_standing_embed(self.pages)


class TeamStandingsPages(menus.ListPageSource):
    def __init__(self, pages: list):
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: Standings) -> discord.Embed:
        return await Standings.make_team_standings_embed(page)


class ConferenceStandingsPages(menus.ListPageSource):
    def __init__(self, pages: list):
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: List[Standings]) -> discord.Embed:
        return await Standings.make_conference_standings_embed(page)


class DivisionStandingsPages(menus.ListPageSource):
    def __init__(self, pages: list):
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: List[Standings]) -> discord.Embed:
        return await Standings.make_division_standings_embed(page)


class LeaderboardPages(menus.ListPageSource):
    def __init__(self, pages: list, style: str):
        super().__init__(pages, per_page=1)
        self.style = style

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: List[str]) -> discord.Embed:
        em = discord.Embed(timestamp=datetime.now())
        description = ""
        for msg in page:
            description += msg
        em.description = description
        em.set_author(
            name=view.ctx.guild.name + _(" Pickems {style} Leaderboard").format(style=self.style),
            icon_url=view.ctx.guild.icon.url,
        )
        em.set_thumbnail(url=view.ctx.guild.icon.url)
        em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return em


class PlayerPages(menus.ListPageSource):
    def __init__(self, pages: list, season: str, players: dict):
        super().__init__(pages, per_page=1)
        self.pages: List[int] = pages
        self.players = players
        self.season: str = season
        self.select_options = []
        for count, player_id in enumerate(self.pages):
            player_name = self.players[player_id]["fullName"]
            self.select_options.append(
                discord.SelectOption(
                    label=f"Page {count + 1}",
                    description=player_name[:50],
                    value=player_id,
                )
            )

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: int) -> discord.Embed:
        player = await Player.from_id(page, session=view.cog.session)
        log.debug(player)
        player = await player.get_full_stats(self.season, session=view.cog.session)
        em = player.get_embed()
        em.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return em


class HockeySelectPlayer(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(
            min_values=1, max_values=1, options=options, placeholder=_("Pick a Player")
        )

    async def callback(self, interaction: discord.Interaction):
        player_id = int(self.values[0])
        index = self.view.source.pages.index(player_id)
        await self.view.show_page(index, interaction)


class SimplePages(menus.ListPageSource):
    def __init__(self, pages: List[Union[discord.Embed, str]]):
        super().__init__(pages, per_page=1)

    def is_paginating(self) -> bool:
        return True

    async def format_page(self, view: discord.ui.View, page: Any) -> Union[discord.Embed, str]:
        if isinstance(page, discord.Embed):
            page.set_footer(text=f"Page {view.current_page + 1}/{self.get_max_pages()}")
        return page


class BaseMenu(discord.ui.View):
    def __init__(
        self,
        source: menus.PageSource,
        cog: Optional[commands.Cog] = None,
        page_start: Optional[int] = 0,
        clear_reactions_after: bool = True,
        delete_message_after: bool = False,
        timeout: int = 180,
        message: discord.Message = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self._source = source
        self.ctx: commands.Context = None
        self.message: discord.Message = None
        self.page_start = page_start
        self.current_page = page_start
        self.forward_button = ForwardButton(discord.ButtonStyle.grey, 0)
        self.back_button = BackButton(discord.ButtonStyle.grey, 0)
        self.first_item = FirstItemButton(discord.ButtonStyle.grey, 0)
        self.last_item = LastItemButton(discord.ButtonStyle.grey, 0)
        self.stop_button = StopButton(discord.ButtonStyle.red, 0)
        self.add_item(self.first_item)
        self.add_item(self.back_button)
        self.add_item(self.forward_button)
        self.add_item(self.last_item)
        self.add_item(self.stop_button)
        self.select_view = None
        if hasattr(self.source, "select_options"):
            self.select_view = HockeySelectPlayer(self.source.select_options[:25])
            self.add_item(self.select_view)
        self.author = None

    @property
    def source(self):
        return self._source

    async def on_timeout(self):
        await self.message.edit(view=None)

    async def start(self, ctx: commands.Context):
        await self.source._prepare_once()
        self.ctx = ctx
        self.message = await self.send_initial_message(ctx, ctx.channel)

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(self.source.format_page, self, page)
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return {"content": value, "embed": None}
        elif isinstance(value, discord.Embed):
            return {"embed": value, "content": None}

    async def update_select_view(self, page_number: int):
        if self.select_view is not None:
            self.remove_item(self.select_view)
        if not hasattr(self.source, "select_options"):
            return
        options = self.source.select_options[:25]
        if page_number >= 12:
            options = self.source.select_options[page_number - 12 : page_number + 13]
        self.select_view = HockeySelectPlayer(options)
        self.add_item(self.select_view)

    async def show_page(self, page_number: int, interaction: discord.Interaction):
        page = await self._source.get_page(page_number)
        self.current_page = page_number
        kwargs = await self._get_kwargs_from_page(page)
        await self.update_select_view(page_number)
        if interaction.response.is_done():
            await interaction.followup.edit(**kwargs, view=self)
        else:
            await interaction.response.edit_message(**kwargs, view=self)

    async def send_initial_message(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> discord.Message:
        """|coro|
        The default implementation of :meth:`Menu.send_initial_message`
        for the interactive pagination session.
        This implementation shows the first page of the source.
        """
        is_slash = False

        if isinstance(ctx, discord.Interaction):
            is_slash = True
        page = await self._source.get_page(self.page_start)
        kwargs = await self._get_kwargs_from_page(page)
        if is_slash:
            self.author = ctx.user
            return await ctx.followup.send(**kwargs, view=self, wait=True)
        else:
            self.author = ctx.author
            return await channel.send(**kwargs, view=self)

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
        if self.author and interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content=_("You are not authorized to interact with this."), ephemeral=True
            )
            return False
        return True
