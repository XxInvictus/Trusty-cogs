from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple, Union

import aiohttp
import discord
from red_commons.logging import getLogger
from redbot.core.bot import Red
from redbot.core.i18n import Translator
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import pagify

from .constants import BASE_URL, CONTENT_URL, TEAMS
from .goal import Goal
from .helper import (
    check_to_post,
    get_channel_obj,
    get_team,
    get_team_role,
    utc_to_local,
)
from .standings import LeagueRecord, Playoffs, Standings

_ = Translator("Hockey", __file__)

log = getLogger("red.trusty-cogs.Hockey")


class GameType(Enum):
    pre_season = "PR"
    regular_season = "R"
    playoffs = "P"
    allstars = "A"
    allstars_women = "WA"
    olympics = "O"
    world_cup_exhibition = "WCOH_EXH"
    world_cup_prelim = "WCOH_PRELIM"
    world_cup_final = "WCOH_FINAL"

    def __str__(self):
        return str(self.value)


@dataclass
class GameStatus:
    abstractGameState: str
    codedGameState: int
    detailedState: str
    statusCode: int
    startTimeTBD: bool

    @classmethod
    def sim(cls):
        return cls(
            abstractGameState="Preview",
            codedGameState="1",
            detailedState="Scheduled",
            statusCode="1",
            startTimeTBD=False,
        )


@dataclass
class Team:
    id: int
    name: str
    link: str


@dataclass
class GameTeam:
    leagueRecord: LeagueRecord
    score: int
    team: Team

    @classmethod
    def sim(cls):
        return cls(
            leagueRecord=LeagueRecord(wins=0, losses=0, ot=0, type="league"),
            score=0,
            team=Team(id=22, name="Edmonton Oilers", link="/api/v1/teams/22"),
        )

    @classmethod
    def from_json(cls, data: dict) -> GameTeam:
        return cls(
            leagueRecord=LeagueRecord(**data["leagueRecord"]),
            score=int(data["score"]),
            team=Team(**data["team"]),
        )


@dataclass
class GameTeams:
    away: GameTeam
    home: GameTeam

    @classmethod
    def sim(cls):
        return cls(
            away=GameTeam.sim(),
            home=GameTeam.sim(),
        )

    @classmethod
    def from_json(cls, data: dict) -> GameTeams:
        return cls(away=GameTeam.from_json(data["away"]), home=GameTeam.from_json(data["home"]))


@dataclass
class Venue:
    id: Optional[int]
    name: str
    link: str

    @classmethod
    def sim(cls):
        return cls(id=999999, name="Trusty's Bagel Barn", link="/api/v1/venues/99999999")


@dataclass
class ScheduleGame:
    gamePk: int
    link: str
    gameType: str
    gameDate: datetime
    status: GameStatus
    teams: GameTeams
    venue: Venue
    content: Dict[str, str]

    @classmethod
    def sim(cls):
        return cls(
            gamePk=2020020474,
            link="/v1/game/2020020474/feed/live",
            gameType="R",
            gameDate=datetime.now(timezone.utc),
            status=GameStatus.sim(),
            teams=GameTeams.sim(),
            venue=Venue.sim(),
            content={"links": "/v1/game/2020020474/content"},
        )

    @classmethod
    def from_json(cls, data: dict) -> ScheduleGame:
        game_start_str = data.get("gameDate", "")
        game_start = datetime.strptime(game_start_str, "%Y-%m-%dT%H:%M:%SZ")
        game_start = game_start.replace(tzinfo=timezone.utc)
        return cls(
            gamePk=data["gamePk"],
            link=data["link"],
            gameType=data["gameType"],
            gameDate=game_start,
            status=GameStatus(**data["status"]),
            teams=GameTeams.from_json(data["teams"]),
            venue=Venue(id=data["venue"].pop("id", None), **data["venue"]),
            content=data["content"],
        )


class Game:
    """
    This is the object that handles game information
    game state updates and goal posts
    """

    game_id: int
    game_state: str
    home_team: str
    away_team: str
    period: int
    home_shots: int
    away_shots: int
    home_score: int
    away_score: int
    game_start: datetime
    goals: List[Goal]
    home_goals: list
    away_goals: list
    home_abr: str
    away_abr: str
    period_ord: str
    period_time_left: str
    period_starts: Dict[str, datetime]
    plays: List[dict]
    first_star: Optional[str]
    second_star: Optional[str]
    third_star: Optional[str]
    away_roster: Optional[dict]
    home_roster: Optional[dict]
    link: Optional[str]

    def __init__(self, **kwargs):
        super().__init__()
        self.game_id = kwargs.get("game_id")
        self.game_state = kwargs.get("game_state")
        self.home_team = kwargs.get("home_team")
        self.away_team = kwargs.get("away_team")
        self.home_shots = kwargs.get("home_shots")
        self.away_shots = kwargs.get("away_shots")
        self.home_score = kwargs.get("home_score")
        self.away_score = kwargs.get("away_score")
        self.goals = kwargs.get("goals")
        self.home_abr = kwargs.get("home_abr")
        self.away_abr = kwargs.get("away_abr")
        self.period = kwargs.get("period")
        self.period_ord = kwargs.get("period_ord")
        self.period_time_left = kwargs.get("period_time_left")
        self.period_starts = kwargs.get("period_starts", {})
        self.plays = kwargs.get("plays")
        self.game_start_str = kwargs.get("game_start", "")
        game_start = datetime.strptime(self.game_start_str, "%Y-%m-%dT%H:%M:%SZ")
        self.game_start = game_start.replace(tzinfo=timezone.utc)
        home_team = kwargs.get("home_team")
        away_team = kwargs.get("away_team")
        self.home_logo = (
            TEAMS[home_team]["logo"]
            if home_team in TEAMS
            else "https://cdn.bleacherreport.net/images/team_logos/328x328/nhl.png"
        )
        self.away_logo = (
            TEAMS[away_team]["logo"]
            if away_team in TEAMS
            else "https://cdn.bleacherreport.net/images/team_logos/328x328/nhl.png"
        )
        self.home_emoji = (
            "<:{}>".format(TEAMS[home_team]["emoji"])
            if home_team in TEAMS
            else "\N{HOUSE BUILDING}\N{VARIATION SELECTOR-16}"
        )
        self.away_emoji = (
            "<:{}>".format(TEAMS[away_team]["emoji"])
            if away_team in TEAMS
            else "\N{AIRPLANE}\N{VARIATION SELECTOR-16}"
        )
        self.first_star = kwargs.get("first_star")
        self.second_star = kwargs.get("second_star")
        self.third_star = kwargs.get("third_star")
        self.away_roster = kwargs.get("away_roster")
        self.home_roster = kwargs.get("home_roster")
        self.game_type: str = kwargs.get("game_type", "")
        self.link = kwargs.get("link")
        self.season = kwargs.get("season")
        self._recap_url: Optional[str] = kwargs.get("recap_url", None)
        self.data = kwargs.get("data", {})

    def __repr__(self):
        return "<Hockey Game home={0.home_team} away={0.away_team} state={0.game_state}>".format(
            self
        )

    @property
    def home_goals(self):
        return [g for g in self.goals if g.team_name == self.home_team]

    @property
    def away_goals(self):
        return [g for g in self.goals if g.team_name == self.away_team]

    @property
    def recap_url(self):
        return self._recap_url

    @property
    def timestamp(self) -> int:
        """
        This is just a helper property to access the game_start as
        a timestamp for formation of discord timestamps
        """
        return int(self.game_start.timestamp())

    def game_type_str(self):
        game_types = {"PR": _("Pre Season"), "R": _("Regular Season"), "P": _("Post Season")}
        return game_types.get(self.game_type, _("Unknown"))

    def to_json(self) -> dict:
        return {
            "game_state": self.game_state,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_shots": self.home_shots,
            "away_shots": self.away_shots,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "goals": [goal.to_json() for goal in self.goals],
            "home_abr": self.home_abr,
            "away_abr": self.away_abr,
            "period": self.period,
            "period_ord": self.period_ord,
            "period_time_left": self.period_time_left,
            "plays": self.plays,
            "game_start": self.game_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "home_logo": self.home_logo,
            "away_logo": self.away_logo,
            "home_emoji": self.home_emoji,
            "away_emoji": self.away_emoji,
            "first_star": self.first_star,
            "second_star": self.second_star,
            "third_star": self.third_star,
            "game_type": self.game_type,
            "link": self.link,
        }

    @staticmethod
    async def get_games(
        team: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> List[Game]:
        """
        Get a specified days games, defaults to the current day
        requires a datetime object
        returns a list of game objects
        if a start date and an end date are not provided to the url
        it returns only todays games

        returns a list of game objects
        """
        games_list = await Game.get_games_list(team, start_date, end_date, session)
        return_games_list = []
        if games_list != []:
            for games in games_list:
                try:
                    if session is None:
                        async with aiohttp.ClientSession() as new_session:
                            async with new_session.get(BASE_URL + games["link"]) as resp:
                                data = await resp.json()
                    else:
                        async with session.get(BASE_URL + games["link"]) as resp:
                            data = await resp.json()
                    log.verbose("get_games, url: %s%s", BASE_URL, games["link"])
                    return_games_list.append(await Game.from_json(data))
                except Exception:
                    log.error("Error grabbing game data:", exc_info=True)
                    continue
        return return_games_list

    @staticmethod
    async def get_game_content(
        game_id: int, session: Optional[aiohttp.ClientSession] = None
    ) -> dict:
        data = {}
        if session is None:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(CONTENT_URL.format(game_id)) as resp:
                        data = await resp.json()
            except Exception:
                log.exception("error pulling game content")
                pass
        else:
            try:
                async with session.get(CONTENT_URL.format(game_id)) as resp:
                    data = await resp.json()
            except Exception:
                log.exception("error pulling game content")
                pass
        return data

    @staticmethod
    async def get_game_recap_from_content(content: dict) -> Optional[str]:
        recap_url = None
        for _item in (
            content.get("editorial", {"recap": {}}).get("recap", {"items": []}).get("items", [])
        ):
            if "playbacks" not in _item["media"]:
                continue
            for _playback in _item["media"]["playbacks"]:
                if _playback["name"] == "FLASH_1800K_896x504":
                    recap_url = _playback["url"]
        return recap_url

    @staticmethod
    async def get_game_recap(
        game_id: int, session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[str]:
        content = await Game.get_game_content(game_id)
        return await Game.get_game_recap_from_content(content)

    @staticmethod
    async def get_games_list(
        team: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> List[dict]:
        """
        Get a specified days games, defaults to the current day
        requires a datetime object
        returns a list of game objects
        if a start date and an end date are not provided to the url
        it returns only todays games

        returns a list of games
        """
        start_date_str = start_date.strftime("%Y-%m-%d") if start_date is not None else None
        end_date_str = end_date.strftime("%Y-%m-%d") if end_date is not None else None
        params = {}
        url = BASE_URL + "/api/v1/schedule"
        if start_date is None and end_date is not None:
            # if no start date is provided start with today
            params["startDate"] = datetime.now().strftime("%Y-%m-%d")
            params["endDate"] = end_date_str
            # url = f"{BASE_URL}/api/v1/schedule?startDate={start_date_str}&endDate={end_date_str}"
        elif start_date is not None and end_date is None:
            # if no end date is provided carry through to the following year
            params["endDate"] = str(start_date.year + 1) + start_date.strftime("-%m-%d")
            params["startDate"] = start_date_str
            # url = f"{BASE_URL}/api/v1/schedule?startDate={start_date_str}&endDate={end_date_str}"
        if start_date_str is not None:
            params["startDate"] = start_date_str
        if end_date_str is not None:
            params["endDate"] = end_date_str
        if team not in ["all", None]:
            # if a team is provided get just that TEAMS data
            # url += "&teamId={}".format(TEAMS[team]["id"])
            params["teamId"] = TEAMS[team]["id"]
        if session is None:
            async with aiohttp.ClientSession() as new_session:
                async with new_session.get(url, params=params) as resp:
                    data = await resp.json()
        else:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
        game_list = [game for date in data["dates"] for game in date["games"]]
        return game_list

    def nst_url(self):
        return f"https://www.naturalstattrick.com/game.php?season={self.season}&game={str(self.game_id)[5:]}&view=limited#gameflow"

    def heatmap_url(self, style: Literal["all", "ev", "5v5", "sva", "home5v4", "away5v4"] = "all"):
        base_url = "https://www.naturalstattrick.com/heatmaps/games/"
        if style == "home5v4":
            home = TEAMS[self.home_team]["tri_code"]
            return f"{base_url}{self.season}/{self.season}-{str(self.game_id)[5:]}-{home}-5v4.png"
        elif style == "away5v4":
            away = TEAMS[self.away_team]["tri_code"]
            return f"{base_url}{self.season}/{self.season}-{str(self.game_id)[5:]}-{away}-5v4.png"
        else:
            return f"{base_url}{self.season}/{self.season}-{str(self.game_id)[5:]}-{style}.png"

    def gameflow_url(
        self, corsi: bool = True, strength: Literal["all", "ev", "5v5", "sva"] = "all"
    ):
        base_url = "https://www.naturalstattrick.com/graphs/"
        diff = "cfdiff" if corsi else "xgdiff"
        return f"{base_url}{self.season}-{str(self.game_id)[5:]}-{diff}-{strength}.png"

    async def make_game_embed(
        self,
        include_plays: bool = False,
        period_goals: Optional[Literal["1st", "2nd", "3rd"]] = None,
        include_heatmap: bool = False,
        include_gameflow: bool = False,
        include_goals: bool = True,
    ) -> discord.Embed:
        """
        Builds the game embed when the command is called
        provides as much data as possible
        """
        team_url = (
            TEAMS[self.home_team]["team_url"] if self.home_team in TEAMS else "https://nhl.com"
        )
        # timestamp = datetime.strptime(self.game_start, "%Y-%m-%dT%H:%M:%SZ")
        title = "{away} @ {home} {state}".format(
            away=self.away_team, home=self.home_team, state=self.game_state
        )
        colour = (
            int(TEAMS[self.home_team]["home"].replace("#", ""), 16)
            if self.home_team in TEAMS
            else None
        )

        em = discord.Embed(timestamp=self.game_start)
        if colour is not None:
            em.colour = colour
        em.set_author(name=title, url=team_url, icon_url=self.home_logo)
        em.set_thumbnail(url=self.home_logo)
        em.set_footer(
            text=_("{game_type} Game start ").format(game_type=self.game_type_str()),
            icon_url=self.away_logo,
        )
        if self.game_state == "Preview":
            home_str, away_str, desc = await self.get_stats_msg()
            if desc is not None and em.description is None:
                em.description = desc
            em.add_field(
                name=f"{self.away_emoji} {self.away_team} {self.away_emoji}", value=away_str
            )
            em.add_field(
                name=f"{self.home_emoji} {self.home_team} {self.home_emoji}", value=home_str
            )
        if include_heatmap:
            em.set_image(url=self.heatmap_url())
            em.description = f"[Natural Stat Trick]({self.nst_url()})"
        if include_gameflow:
            em.set_image(url=self.gameflow_url())
            em.description = f"[Natural Stat Trick]({self.nst_url()})"

        if self.game_state != "Preview":
            home_msg = _("Goals: **{home_score}**\nShots: **{home_shots}**").format(
                home_score=self.home_score, home_shots=self.home_shots
            )
            away_msg = _("Goals: **{away_score}**\nShots: **{away_shots}**").format(
                away_score=self.away_score, away_shots=self.away_shots
            )
            em.add_field(
                name=f"{self.away_emoji} {self.away_team} {self.away_emoji}", value=away_msg
            )
            em.add_field(
                name=f"{self.home_emoji} {self.home_team} {self.home_emoji}", value=home_msg
            )

            if self.goals != [] and include_goals:
                goal_msg = ""
                first_goals = [goal for goal in self.goals if goal.period_ord == "1st"]
                second_goals = [goal for goal in self.goals if goal.period_ord == "2nd"]
                third_goals = [goal for goal in self.goals if goal.period_ord == "3rd"]
                ot_goals = [goal for goal in self.goals if "OT" in goal.period_ord]
                so_goals = [goal for goal in self.goals if goal.period_ord == "SO"]
                list_goals = {
                    "1st": first_goals,
                    "2nd": second_goals,
                    "3rd": third_goals,
                    "OT": ot_goals,
                }
                if period_goals:
                    list_goals = {period_goals: list_goals[period_goals]}
                for goals in list_goals:
                    ordinal = goals
                    goal_msg = ""

                    period_start_str = ""
                    period_start = self.period_starts.get(ordinal)
                    if period_start:
                        period_start_ts = int(period_start.timestamp())
                        period_start_str = f"(<t:{period_start_ts}:t>)"

                    for goal in list_goals[ordinal]:
                        try:
                            emoji = f"<:{TEAMS[goal.team_name]['emoji']}>"
                        except KeyError:
                            emoji = ""
                        left = ""
                        if goal.time_remaining:
                            left = _("\n{time} left in the {ord} period").format(
                                time=goal.time_remaining, ord=goal.period_ord
                            )
                        goal_msg += _(
                            "{emoji} [{team} {empty_net}{strength} Goal By {description} {left}]({link})\n\n"
                        ).format(
                            emoji=emoji,
                            team=goal.team_name,
                            empty_net="EN " if goal.empty_net else "",
                            strength=goal.strength_code,
                            description=goal.description,
                            link=goal.link,
                            left=left,
                        )

                    count = 0
                    continued = _("(Continued)")
                    for page in pagify(
                        goal_msg, delims=["\n\n", "\n"], page_length=1024, priority=True
                    ):
                        em.add_field(
                            name=_("{ordinal} Period {time} Goals {continued}").format(
                                ordinal=ordinal,
                                time=period_start_str,
                                continued="" if count == 0 else continued,
                            ),
                            value=page,
                            inline=False,
                        )
                        count += 1
                if len(so_goals) != 0:
                    home_msg, away_msg = await self.goals[-1].get_shootout_display(self)
                    # get the last goal so that we always post the full current
                    # shootout display here
                    em.add_field(
                        name=_("{team} Shootout").format(team=self.home_team), value=home_msg
                    )
                    em.add_field(
                        name=_("{team} Shootout").format(team=self.away_team), value=away_msg
                    )
                if self.recap_url is not None:
                    em.description = f"[Recap]({self.recap_url})"
            if self.first_star is not None:
                stars = f"⭐ {self.first_star}\n⭐⭐ {self.second_star}\n⭐⭐⭐ {self.third_star}"
                em.add_field(name=_("Stars of the game"), value=stars, inline=False)
            if self.game_state == "Live":
                period = self.period_ord
                if self.period_time_left[0].isdigit():
                    msg = _("{time} Left in the {ordinal} period").format(
                        time=self.period_time_left, ordinal=period
                    )
                else:
                    msg = _("{time} Left of the {ordinal} period").format(
                        time=self.period_time_left, ordinal=period
                    )
                if include_plays:
                    em.description = _("Last Play: {play}").format(
                        play=self.plays[-1]["result"]["description"]
                    )
                em.add_field(name="Period", value=msg)
        return em

    async def game_state_embed(self) -> discord.Embed:
        """
        Makes the game state embed based on the game self provided
        """
        # post_state = ["all", self.home_team, self.away_team]
        # timestamp = datetime.strptime(self.game_start, "%Y-%m-%dT%H:%M:%SZ")
        title = f"{self.away_team} @ {self.home_team} {self.game_state}"
        em = discord.Embed(timestamp=self.game_start)
        home_field = "{0} {1} {0}".format(self.home_emoji, self.home_team)
        away_field = "{0} {1} {0}".format(self.away_emoji, self.away_team)
        if self.game_state != "Preview":
            home_str = _("Goals: **{home_score}**\nShots: **{home_shots}**").format(
                home_score=self.home_score, home_shots=self.home_shots
            )
            away_str = _("Goals: **{away_score}**\nShots: **{away_shots}**").format(
                away_score=self.away_score, away_shots=self.away_shots
            )
        else:
            home_str, away_str, desc = await self.get_stats_msg()
            if desc is not None:
                em.description = desc
        em.add_field(name=home_field, value=home_str, inline=False)
        em.add_field(name=away_field, value=away_str, inline=True)
        colour = (
            int(TEAMS[self.home_team]["home"].replace("#", ""), 16)
            if self.home_team in TEAMS
            else None
        )
        if colour is not None:
            em.colour = colour
        home_url = (
            TEAMS[self.home_team]["team_url"] if self.home_team in TEAMS else "https://nhl.com"
        )
        if self.first_star is not None:
            stars = f"⭐ {self.first_star}\n⭐⭐ {self.second_star}\n⭐⭐⭐ {self.third_star}"
            em.add_field(name=_("Stars of the game"), value=stars)
        em.set_author(name=title, url=home_url, icon_url=self.home_logo)
        em.set_thumbnail(url=self.home_logo)
        em.set_footer(text=_("Game start "), icon_url=self.away_logo)
        if self.recap_url is not None:
            em.description = f"[Recap]({self.recap_url})"
        return em

    async def game_state_text(self) -> str:
        # post_state = ["all", self.home_team, self.away_team]
        # timestamp =  datetime.strptime(self.game_start, "%Y-%m-%dT%H:%M:%SZ")
        time_string = f"<t:{self.timestamp}>"
        em = (
            f"{self.away_emoji}{self.away_team} @ {self.home_emoji}{self.home_team} "
            f"{self.game_state}\n({time_string})"
        )
        if self.game_state != "Preview":
            em = (
                _("**__Current Score__**\n")
                + f"{self.home_emoji} {self.home_team}: {self.home_score}\n"
                + f"{self.away_emoji} {self.away_team}: {self.away_score}"
            )
        return em

    async def get_stats_msg(self) -> Tuple[str, str, Optional[str]]:
        """
        returns team stats on the season from standings object
        """
        home_str = _("GP:**0** W:**0** L:**0\n**OT:**0** PTS:**0** S:**0**\n")
        away_str = _("GP:**0** W:**0** L:**0\n**OT:**0** PTS:**0** S:**0**\n")
        desc = None
        if self.game_type != "P":
            msg = _(
                "GP:**{gp}** W:**{wins}** L:**{losses}\n**OT:**{ot}** PTS:**{pts}** S:**{streak}**\n"
            )
            try:
                standings = await Standings.get_team_standings()
                for name, record in standings.all_records.items():
                    if record.team.name == self.away_team:
                        away_str = msg.format(
                            wins=record.league_record.wins,
                            losses=record.league_record.losses,
                            ot=record.league_record.ot,
                            pts=record.points,
                            gp=record.games_played,
                            streak=record.streak,
                        )
                    if record.team.name == self.home_team:
                        home_str = msg.format(
                            wins=record.league_record.wins,
                            losses=record.league_record.losses,
                            ot=record.league_record.ot,
                            pts=record.points,
                            gp=record.games_played,
                            streak=record.streak,
                        )
            except Exception:
                log.exception("Error pulling stats")
                pass
        else:
            try:
                desc_str = _("{round_name}:\n{series_status}")
                msg = _("GP:**{gp}** W:**{wins}** L:**{losses}**")
                playoffs = await Playoffs.get_playoffs()
                for rounds in playoffs.rounds:
                    for series in rounds.series:
                        for matchup in series.matchupTeams:
                            if matchup.team.name == self.away_team:
                                away_str = msg.format(
                                    gp=series.currentGame.seriesSummary.gameNumber - 1,
                                    wins=matchup.seriesRecord.wins,
                                    losses=matchup.seriesRecord.losses,
                                )
                            if matchup.team.name == self.home_team:
                                home_str = msg.format(
                                    gp=series.currentGame.seriesSummary.gameNumber - 1,
                                    wins=matchup.seriesRecord.wins,
                                    losses=matchup.seriesRecord.losses,
                                )
                            if (
                                matchup.team.name == self.away_team
                                or matchup.team.name == self.home_team
                            ):
                                desc = desc_str.format(
                                    round_name=rounds.names.name,
                                    series_status=series.currentGame.seriesSummary.seriesStatus,
                                )
            except Exception:
                log.exception("Error pulling playoffs stats")
                pass
        return home_str, away_str, desc

    async def check_game_state(self, bot: Red, count: int = 0) -> bool:
        # post_state = ["all", self.home_team, self.away_team]
        home = await get_team(bot, self.home_team, self.game_start_str, self.game_id)
        # away = await get_team(self.away_team)
        # team_list = await self.config.teams()
        # Home team checking
        end_first = self.period_time_left == "END" and self.period == 1
        end_second = self.period_time_left == "END" and self.period == 2
        end_third = self.period_time_left == "END" and self.period == 3
        if self.game_state == "Preview":
            """Checks if the the game state has changes from Final to Preview
            Could be unnecessary since after Game Final it will check for next game
            """
            time_now = datetime.now(tz=timezone.utc)
            # game_time = datetime.strptime(data.game_start, "%Y-%m-%dT%H:%M:%SZ")
            game_start = (self.game_start - time_now).total_seconds() / 60
            if "Preview" not in home["game_state"]:
                await self.post_game_state(bot)
                await self.save_game_state(bot)
                bot.dispatch("hockey_preview", self)
            if game_start < 60 and game_start > 30 and home["game_state"] != "Preview60":
                # Post 60 minutes until game start
                await self.post_time_to_game_start(bot, "60")
                await self.save_game_state(bot, "60")
                bot.dispatch("hockey_preview", self)
            if game_start < 30 and game_start > 10 and home["game_state"] != "Preview30":
                # Post 30 minutes until game start
                await self.post_time_to_game_start(bot, "30")
                await self.save_game_state(bot, "30")
                bot.dispatch("hockey_preview", self)
            if game_start < 10 and game_start > 0 and home["game_state"] != "Preview10":
                # Post 10 minutes until game start
                await self.post_time_to_game_start(bot, "10")
                await self.save_game_state(bot, "10")
                bot.dispatch("hockey_preview", self)

                # Create channel and look for game day thread

        if self.game_state == "Live":
            # Checks what the period is and posts the game is starting in the appropriate channel

            if home["period"] != self.period or "Preview" in home["game_state"]:
                log.debug(
                    "**%s Period starting %s at %s**",
                    self.period_ord,
                    self.away_team,
                    self.home_team,
                )
                await self.post_game_state(bot)
                await self.save_game_state(bot)
                bot.dispatch("hockey_period_start", self)

            if (self.home_score + self.away_score) != 0:
                # Check if there's goals only if there are goals
                await self.check_team_goals(bot)
            if end_first and home["game_state"] != "LiveEND1st":
                log.debug("End of the first period")
                await self.period_recap(bot, "1st")
                await self.save_game_state(bot, "END1st")
            if end_second and home["game_state"] != "LiveEND2nd":
                log.debug("End of the second period")
                await self.period_recap(bot, "2nd")
                await self.save_game_state(bot, "END2nd")
            if end_third and home["game_state"] not in ["LiveEND3rd", "FinalEND3rd"]:
                log.debug("End of the third period")
                await self.period_recap(bot, "3rd")
                await self.save_game_state(bot, "END3rd")

        if self.game_state == "Final":
            if (self.home_score + self.away_score) != 0:
                # Check if there's goals only if there are goals
                await self.check_team_goals(bot)
            if end_third and home["game_state"] not in ["LiveEND3rd", "FinalEND3rd"]:
                log.debug("End of the third period")
                await self.period_recap(bot, "3rd")
                await self.save_game_state(bot, "END3rd")

            if (
                self.first_star is not None
                and self.second_star is not None
                and self.third_star is not None
                and len(self.home_goals) == self.home_score
                and len(self.away_goals) == self.away_score
            ) or count >= 20:
                """Final game state checks"""
                if home["game_state"] != self.game_state and home["game_state"] != "Null":
                    # Post game final data and check for next game
                    log.debug("Game Final %s @ %s", self.away_team, self.home_team)
                    await self.post_game_state(bot)
                    await self.save_game_state(bot)
                    bot.dispatch("hockey_final", self)
                    log.debug("Saving final")
                    return True
        return False

    async def period_recap(self, bot: Red, period: Literal["1st", "2nd", "3rd"]) -> None:
        """
        Builds the period recap
        """
        em = await self.make_game_embed(False, None)
        tasks = []
        post_state = ["all", self.home_team, self.away_team]
        config = bot.get_cog("Hockey").config
        all_channels = await bot.get_cog("Hockey").config.all_channels()
        async for channel_id, data in AsyncIter(all_channels.items(), steps=100):
            await self.maybe_edit_gamedaythread_message(bot, channel_id, data)
            channel = await get_channel_obj(bot, channel_id, data)
            if not channel:
                continue

            should_post = await check_to_post(bot, channel, data, post_state, self.game_state)
            should_post &= "Periodrecap" in await config.channel(channel).game_states()
            publish = "Periodrecap" in await config.channel(channel).publish_states()
            if should_post:
                asyncio.create_task(self.post_period_recap(channel, em, publish))

    async def post_period_recap(
        self, channel: discord.TextChannel, embed: discord.Embed, publish: bool
    ) -> None:
        """
        Posts the period recap in designated channels
        """
        if not channel.permissions_for(channel.guild.me).send_messages:
            log.debug("No permission to send messages in %s", repr(channel))
            return
        if channel.guild.me.is_timed_out():
            return
        try:
            msg = await channel.send(embed=embed)
            if publish and channel.is_news():
                pass
                # await msg.publish()
        except Exception:
            log.exception("Could not post goal in %s", repr(channel))

    async def maybe_edit_gamedaythread_message(
        self, bot: Red, channel_id: int, data: dict
    ) -> None:
        post_state = ["all", self.home_team, self.away_team]
        if data["parent"] and any([i in data["team"] for i in post_state]) and data["update"]:
            try:
                em = await self.make_game_embed(False, None)
                parent = await get_channel_obj(bot, data["parent"], data)
                msg = parent.get_partial_message(channel_id)
                asyncio.create_task(msg.edit(embed=em))
            except Exception:
                log.exception("Error editing thread start message.")

    async def post_game_state(self, bot: Red) -> None:
        """
        When a game state has changed this is called to create the embed
        and post in all channels
        """
        post_state = ["all", self.home_team, self.away_team]
        state_embed = await self.game_state_embed()
        state_text = await self.game_state_text()
        tasks = []
        all_channels = await bot.get_cog("Hockey").config.all_channels()
        async for channel_id, data in AsyncIter(all_channels.items(), steps=100):
            await self.maybe_edit_gamedaythread_message(bot, channel_id, data)
            channel = await get_channel_obj(bot, channel_id, data)
            if not channel:
                continue
            if channel.guild.me.is_timed_out():
                continue
            should_post = await check_to_post(bot, channel, data, post_state, self.game_state)
            if should_post:
                asyncio.create_task(
                    self.actually_post_state(bot, channel, state_embed, state_text)
                )
        # previews = await bounded_gather(*tasks)

    async def actually_post_state(
        self,
        bot: Red,
        channel: Union[discord.TextChannel, discord.Thread],
        state_embed: discord.Embed,
        state_text: str,
    ) -> Optional[Tuple[discord.TextChannel, discord.Message]]:
        guild = channel.guild
        if not channel.permissions_for(guild.me).send_messages:
            log.debug("No permission to send messages in %s", repr(channel))
            return None
        config = bot.get_cog("Hockey").config
        guild_settings = await config.guild(guild).all()
        channel_settings = await config.channel(channel).all()
        game_day_channels = guild_settings["gdc"]
        can_embed = channel.permissions_for(guild.me).embed_links
        publish_states = []  # await config.channel(channel).publish_states()
        # can_manage_webhooks = False  # channel.permissions_for(guild.me).manage_webhooks

        if self.game_state == "Live":
            guild_notifications = guild_settings["game_state_notifications"]
            channel_notifications = channel_settings["game_state_notifications"]
            state_notifications = guild_notifications or channel_notifications
            # TODO: Something with these I can't remember what now
            # guild_start = guild_settings["start_notifications"]
            # channel_start = channel_settings["start_notifications"]
            # start_notifications = guild_start or channel_start
            # heh inclusive or
            allowed_mentions = {}
            home_role, away_role = await get_team_role(guild, self.home_team, self.away_team)
            if state_notifications:
                allowed_mentions = {"allowed_mentions": discord.AllowedMentions(roles=True)}
            else:
                allowed_mentions = {"allowed_mentions": discord.AllowedMentions(roles=False)}
            if self.game_type == "R" and "OT" in self.period_ord:
                if not guild_settings["ot_notifications"]:
                    allowed_mentions = {"allowed_mentions": discord.AllowedMentions(roles=False)}
            if "SO" in self.period_ord:
                if not guild_settings["so_notifications"]:
                    allowed_mentions = {"allowed_mentions": discord.AllowedMentions(roles=False)}
            if game_day_channels is not None:
                # We don't want to ping people in the game day channels twice
                if channel.id in game_day_channels:
                    home_role, away_role = self.home_team, self.away_team
            msg = _("**{period} Period starting {away_role} at {home_role}**").format(
                period=self.period_ord, away_role=away_role, home_role=home_role
            )
            try:
                if not can_embed:
                    msg = await channel.send(msg + "\n{}".format(state_text), **allowed_mentions)
                else:
                    msg = await channel.send(msg, embed=state_embed, **allowed_mentions)
                if self.game_state in publish_states:
                    try:
                        if channel.is_news():
                            # allows backwards compatibility still
                            # await msg.publish()
                            pass
                    except Exception:
                        pass
            except Exception:
                log.exception("Could not post goal in %s", repr(channel))

        else:
            if self.game_state == "Preview":
                if game_day_channels is not None:
                    # Don't post the preview message twice in the channel
                    if channel.id in game_day_channels:
                        return None
            try:
                if not can_embed:
                    preview_msg = await channel.send(state_text)
                else:
                    preview_msg = await channel.send(embed=state_embed)

                if self.game_state in publish_states:
                    try:
                        if channel.is_news():
                            # allows backwards compatibility still
                            # await preview_msg.publish()
                            pass
                    except Exception:
                        pass

                # Create new pickems object for the game
                if self.game_state == "Preview":
                    bot.dispatch("hockey_preview_message", channel, preview_msg, self)
                    return channel, preview_msg
            except Exception:
                log.exception("Could not post goal in %s", repr(channel))
        return None

    async def check_team_goals(self, bot: Red) -> None:
        """
        Checks to see if a goal needs to be posted
        """
        team_data = {
            self.home_team: await get_team(bot, self.home_team, self.game_start_str, self.game_id),
            self.away_team: await get_team(bot, self.away_team, self.game_start_str, self.game_id),
        }
        # home_team_data = await get_team(bot, self.home_team)
        # away_team_data = await get_team(bot, self.away_team)
        # all_data = await get_team("all")
        team_list = await bot.get_cog("Hockey").config.teams()
        # post_state = ["all", self.home_team, self.away_team]

        # home_goal_ids = [goal.goal_id for goal in self.home_goals]
        # away_goal_ids = [goal.goal_id for goal in self.away_goals]

        home_goal_list = list(team_data[self.home_team]["goal_id"])
        away_goal_list = list(team_data[self.away_team]["goal_id"])

        for goal in self.goals:
            # goal_id = str(goal["result"]["eventCode"])
            # team = goal["team"]["name"]
            # team_data = await get_team(bot, goal.team_name)
            if goal.goal_id not in team_data[goal.team_name]["goal_id"]:
                # attempts to post the goal if there is a new goal
                bot.dispatch("hockey_goal", self, goal)
                goal.home_shots = self.home_shots
                goal.away_shots = self.away_shots
                msg_list = await goal.post_team_goal(bot, self)
                team_list.remove(team_data[goal.team_name])
                team_data[goal.team_name]["goal_id"][goal.goal_id] = {
                    "goal": goal.to_json(),
                    "messages": msg_list,
                }
                team_list.append(team_data[goal.team_name])
                await bot.get_cog("Hockey").config.teams.set(team_list)
                continue
            if goal.goal_id in team_data[goal.team_name]["goal_id"]:
                # attempts to edit the goal if the scorers have changed
                old_goal = Goal(**team_data[goal.team_name]["goal_id"][goal.goal_id]["goal"])
                if goal.description != old_goal.description or goal.link != old_goal.link:
                    goal.home_shots = old_goal.home_shots
                    goal.away_shots = old_goal.away_shots
                    # This is to keep shots consistent between edits
                    # Shots should not update as the game continues
                    bot.dispatch("hockey_goal_edit", self, goal)
                    old_msgs = team_data[goal.team_name]["goal_id"][goal.goal_id]["messages"]
                    team_list.remove(team_data[goal.team_name])
                    team_data[goal.team_name]["goal_id"][goal.goal_id]["goal"] = goal.to_json()
                    team_list.append(team_data[goal.team_name])
                    await bot.get_cog("Hockey").config.teams.set(team_list)
                    if old_msgs:
                        asyncio.create_task(goal.edit_team_goal(bot, self, old_msgs))
        # attempts to delete the goal if it was called back
        home_diff = abs(len(home_goal_list) - len(self.home_goals))
        away_diff = abs(len(away_goal_list) - len(self.away_goals))
        if 1 < home_diff <= 2:
            for goal_str in home_goal_list:
                await Goal.remove_goal_post(bot, goal_str, self.home_team, self)
        if 1 < away_diff <= 2:
            for goal_str in away_goal_list:
                await Goal.remove_goal_post(bot, goal_str, self.away_team, self)

    async def save_game_state(self, bot: Red, time_to_game_start: str = "0") -> None:
        """
        Saves the data do the config to compare against new data
        """
        home = await get_team(bot, self.home_team, self.game_start_str, self.game_id)
        away = await get_team(bot, self.away_team, self.game_start_str, self.game_id)
        team_list = await bot.get_cog("Hockey").config.teams()
        team_list.remove(home)
        team_list.remove(away)
        if self.game_state != "Final":
            if self.game_state == "Preview" and time_to_game_start != "0":
                home["game_state"] = self.game_state + time_to_game_start
                away["game_state"] = self.game_state + time_to_game_start
            elif self.game_state == "Live" and time_to_game_start != "0":
                home["game_state"] = self.game_state + time_to_game_start
                away["game_state"] = self.game_state + time_to_game_start
            else:
                home["game_state"] = self.game_state
                away["game_state"] = self.game_state
            home["period"] = self.period
            away["period"] = self.period
            home["game_start"] = self.game_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            away["game_start"] = self.game_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            if time_to_game_start == "0":
                home["game_state"] = "Null"
                away["game_state"] = "Null"
                home["period"] = 0
                away["period"] = 0
                home["goal_id"] = {}
                away["goal_id"] = {}
                home["game_start"] = ""
                away["game_start"] = ""
            elif self.game_state == "Final" and time_to_game_start != "0":
                home["game_state"] = self.game_state + time_to_game_start
                away["game_state"] = self.game_state + time_to_game_start
        team_list.append(home)
        team_list.append(away)
        await bot.get_cog("Hockey").config.teams.set(team_list)

    async def post_time_to_game_start(self, bot: Red, time_left: str) -> None:
        """
        Post when there is 60, 30, and 10 minutes until the game starts in all channels
        """
        post_state = ["all", self.home_team, self.away_team]
        time_str = f"<t:{self.timestamp}:R>"
        msg = _("{away_emoji} {away} @ {home_emoji} {home} game starts {time}!").format(
            time=time_str,
            away_emoji=self.away_emoji,
            away=self.away_team,
            home_emoji=self.home_emoji,
            home=self.home_team,
        )
        tasks = []
        all_channels = await bot.get_cog("Hockey").config.all_channels()
        async for channel_id, data in AsyncIter(all_channels.items(), steps=100):
            channel = await get_channel_obj(bot, channel_id, data)
            if not channel:
                continue

            should_post = await check_to_post(bot, channel, data, post_state, self.game_state)
            team_to_post = await bot.get_cog("Hockey").config.channel(channel).team()
            if should_post and "all" not in team_to_post:
                asyncio.create_task(self.post_game_start(channel, msg))
        # await bounded_gather(*tasks)

    async def post_game_start(self, channel: discord.TextChannel, msg: str) -> None:
        if not channel.permissions_for(channel.guild.me).send_messages:
            log.debug("No permission to send messages in %s", repr(channel))
            return
        try:
            await channel.send(msg)
        except Exception:
            log.exception("Could not post goal in %s", repr(channel))

    @classmethod
    async def from_gamepk(
        cls, gamepk: int, session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[Game]:
        url = f"{BASE_URL}/api/v1/game/{gamepk}/feed/live"
        return await cls.from_url(url, session)

    @classmethod
    async def from_url(
        cls, url: str, session: Optional[aiohttp.ClientSession] = None
    ) -> Optional[Game]:
        url = url.replace(BASE_URL, "")  # strip the base url incase we already have it
        try:
            if session is None:
                # this should only happen in pickems objects
                # since pickems don't have access to the full
                # cogs session
                async with aiohttp.ClientSession() as new_session:
                    async with new_session.get(BASE_URL + url) as resp:
                        data = await resp.json()
            else:
                async with session.get(BASE_URL + url) as resp:
                    data = await resp.json()
            return await cls.from_json(data)
        except Exception:
            log.exception("Error grabbing game data: ")
            return None

    @classmethod
    async def from_json(cls, data: dict) -> Game:
        event = data["liveData"]["plays"]["allPlays"]
        home_team = data["gameData"]["teams"]["home"]["name"]
        away_team = data["gameData"]["teams"]["away"]["name"]
        away_roster = data["liveData"]["boxscore"]["teams"]["away"]["players"]
        home_roster = data["liveData"]["boxscore"]["teams"]["home"]["players"]
        players = {}
        players.update(away_roster)
        players.update(home_roster)
        game_id = data["gameData"]["game"]["pk"]
        season = data["gameData"]["game"]["season"]
        period_starts = {}
        for play in data["liveData"]["plays"]["allPlays"]:
            if play["result"]["eventTypeId"] == "PERIOD_START":
                dt = datetime.strptime(play["about"]["dateTime"], "%Y-%m-%dT%H:%M:%SZ")
                dt = dt.replace(tzinfo=timezone.utc)
                period_starts[play["about"]["ordinalNum"]] = dt

        content = await Game.get_game_content(game_id)
        try:
            recap_url = await Game.get_game_recap_from_content(content)
        except Exception:
            log.error("Cannot get game recap url.")
            recap_url = None
        goals = [
            await Goal.from_json(goal, players, content)
            for goal in event
            if goal["result"]["eventTypeId"] == "GOAL"
            or (
                goal["result"]["eventTypeId"] in ["SHOT", "MISSED_SHOT"]
                and goal["about"]["ordinalNum"] == "SO"
            )
        ]
        link = f"{BASE_URL}{data['link']}"
        if "currentPeriodOrdinal" in data["liveData"]["linescore"]:
            period_ord = data["liveData"]["linescore"]["currentPeriodOrdinal"]
            period_time_left = data["liveData"]["linescore"]["currentPeriodTimeRemaining"]
            events = data["liveData"]["plays"]["allPlays"]
        else:
            period_ord = "0"
            period_time_left = "0"
            events = ["."]
        decisions = data["liveData"]["decisions"]
        first_star = decisions.get("firstStar", {}).get("fullName")
        second_star = decisions.get("secondStar", {}).get("fullName")
        third_star = decisions.get("thirdStar", {}).get("fullName")
        game_type = data["gameData"]["game"]["type"]
        game_state = (
            data["gameData"]["status"]["abstractGameState"]
            if data["gameData"]["status"]["detailedState"] != "Postponed"
            else data["gameData"]["status"]["detailedState"]
        )
        return cls(
            game_id=game_id,
            game_state=game_state,
            home_team=home_team,
            away_team=away_team,
            period=data["liveData"]["linescore"]["currentPeriod"],
            home_shots=data["liveData"]["linescore"]["teams"]["home"]["shotsOnGoal"],
            away_shots=data["liveData"]["linescore"]["teams"]["away"]["shotsOnGoal"],
            home_score=data["liveData"]["linescore"]["teams"]["home"]["goals"],
            away_score=data["liveData"]["linescore"]["teams"]["away"]["goals"],
            game_start=data["gameData"]["datetime"]["dateTime"],
            goals=goals,
            home_abr=data["gameData"]["teams"]["home"]["abbreviation"],
            away_abr=data["gameData"]["teams"]["away"]["abbreviation"],
            period_ord=period_ord,
            period_time_left=period_time_left,
            period_starts=period_starts,
            plays=events,
            first_star=first_star,
            second_star=second_star,
            third_star=third_star,
            away_roster=away_roster,
            home_roster=home_roster,
            link=link,
            game_type=game_type,
            season=season,
            recap_url=recap_url,
            # data=data,
        )
