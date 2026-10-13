from __future__ import annotations

import asyncio
from functools import wraps
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
from dotenv import load_dotenv

from store import TicketStore


def install_ephemeral_expiry() -> None:
    """Expire every private interaction response after one minute."""
    if getattr(discord.InteractionResponse, "_rival_expiry_installed", False):
        return

    original_send_message = discord.InteractionResponse.send_message

    @wraps(original_send_message)
    async def send_message(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("ephemeral"):
            kwargs.setdefault("delete_after", 60)
        return await original_send_message(self, *args, **kwargs)

    discord.InteractionResponse.send_message = send_message  # type: ignore[method-assign]

    original_defer = discord.InteractionResponse.defer

    @wraps(original_defer)
    async def defer(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        result = await original_defer(self, *args, **kwargs)
        if kwargs.get("ephemeral"):
            interaction = self._parent

            async def expire_deferred_response() -> None:
                await asyncio.sleep(60)
                try:
                    await interaction.delete_original_response()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            asyncio.create_task(expire_deferred_response())
        return result

    discord.InteractionResponse.defer = defer  # type: ignore[method-assign]

    original_webhook_send = discord.Webhook.send

    @wraps(original_webhook_send)
    async def webhook_send(self: discord.Webhook, *args: Any, **kwargs: Any) -> Any:
        # Webhook.send (used by Interaction.followup) has no delete_after
        # argument, unlike InteractionResponse.send_message. Delete the
        # returned ephemeral WebhookMessage ourselves instead.
        expire = bool(kwargs.get("ephemeral"))
        message = await original_webhook_send(self, *args, **kwargs)
        if expire and isinstance(message, discord.WebhookMessage):
            async def delete_ephemeral_followup() -> None:
                await asyncio.sleep(60)
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            asyncio.create_task(delete_ephemeral_followup())
        return message

    discord.Webhook.send = webhook_send  # type: ignore[method-assign]
    discord.InteractionResponse._rival_expiry_installed = True  # type: ignore[attr-defined]


install_ephemeral_expiry()


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("rivalbot")
store = TicketStore(ROOT / "data" / "rivalbot.sqlite3")

RED = discord.Color.from_rgb(210, 35, 45)
MOSCOW_TZ = timezone(timedelta(hours=3))
PURPLE = RED
BLUE = RED
GREEN = RED
YELLOW = RED

def env_id(name: str, fallback: str) -> int:
    value = os.getenv(name, fallback).strip()
    return int(value) if value.isdigit() else int(fallback)


PROMOTION_CHANNEL_ID = env_id("promotion_channel_id", "1558523155306254436")
PROMOTION_REVIEW_CHANNEL_ID = env_id("promotion_review_channel_id", "1558859590580576427")
TRAINEE_ROLE_ID = env_id("trainee_role_id", "1558552816388407357")
MAIN_ROLE_ID = env_id("main_role_id", "1558482456246489129")
CLAN_ROLE_ID = env_id("clan_role_id", "1558524889965535352")
RECRUITER_PING_ROLE_ID = env_id("recruiter_ping_role_id", "1558483532932718602")
CURATOR_ROLE_ID = env_id("curator_role_id", "1558483015804391504")
INTERVIEW_VOICE_CHANNEL_ID = env_id("interview_voice_channel_id", "1558487678448570378")
SERVER_INVITE_URL = "https://discord.gg/PTW3ZtuK9S"
SERVER_APPLICATION_URL = "https://discord.com/channels/1458213632511971390/1558489626899914923"
PROMOTION_ADMIN_ROLE_IDS = {
    int(value.strip())
    for value in os.getenv("promotion_admin_role_ids", "1458217075762921731,1558483015804391504").split(",")
    if value.strip().isdigit()
}

APPLICATION_INSTRUCTIONS = (
    "Привет! Спасибо за создание тикета. Заполни анкету ниже и ожидай ответа от рекрутеров "
    "(рассмотрение занимает до 24 часов):\n\n"
    "Заполни поля формы:\n\n"
    "**1. Твой ник, имя, возраст и ТГ (@username)**\n"
    "**2. Твой софт, сервера и часовой пояс (разница с МСК)**\n"
    "**3. Опыт в хвх/пвп**\n"
    "**4. Готов ли сам закупать сеты и делиться ресурсами с тиммейтами**\n"
    "**5. Ссылка на канал (если есть) и статус медиа в каких-либо читах**"
)

CLAN_QUESTION_LABELS: tuple[tuple[str, str], ...] = (
    ("identity", "1. Ник, имя, возраст и ТГ (@username)"),
    ("timezone", "2. Софт, сервера и часовой пояс (разница с МСК)"),
    ("experience", "3. Опыт в хвх/пвп"),
    ("sets", "4. Покупка сетов и делёжка ресурсами"),
    ("media", "5. Ссылка на канал и статус медиа в читах"),
)
RECRUITER_QUESTION_LABELS: tuple[tuple[str, str], ...] = (
    ("nickname", "Ник в Minecraft / Discord"),
    ("experience", "Опыт в рекрутинге"),
    ("about", "Немного о себе"),
)


def ticket_label(number: int) -> str:
    return f"#{number:03d}"


def support_member(member: discord.Member, settings: dict[str, Any], ticket_type: str = "clan") -> bool:
    configured_ids = settings["support_role_ids"]
    if ticket_type == "recruiter":
        configured_ids = [settings.get("curator_role_id")]
    support_ids = {int(role_id) for role_id in configured_ids if str(role_id).isdigit()}
    return member.guild_permissions.administrator or any(role.id in support_ids for role in member.roles)


def server_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator


def has_role_id(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)


class ClanLinksView(discord.ui.View):
    """Reusable clickable links for the clan and its application channel."""

    def __init__(self, *, include_application: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Дискорд-сервер RIval", emoji="🔗", style=discord.ButtonStyle.link,
            url=SERVER_INVITE_URL,
        ))
        if include_application:
            self.add_item(discord.ui.Button(
                label="Канал заявок", emoji="📨", style=discord.ButtonStyle.link,
                url=SERVER_APPLICATION_URL,
            ))


def logo_file() -> discord.File | None:
    image = ROOT / "img" / "icon.jpg"
    return discord.File(image, filename="icon.jpg") if image.is_file() else None


async def send_private_notice(
    user: discord.abc.User,
    title: str,
    description: str,
    *,
    include_application: bool = False,
) -> bool:
    """Send a single best-effort DM; do not retry and spam if DMs are closed."""
    embed = discord.Embed(title=title, description=description, color=RED, timestamp=discord.utils.utcnow())
    embed.set_footer(text="RIval • уведомление по твоему аккаунту")
    attachment = logo_file()
    if attachment is not None:
        embed.set_thumbnail(url="attachment://icon.jpg")
    try:
        await user.send(
            embed=embed,
            view=ClanLinksView(include_application=include_application),
            file=attachment,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.Forbidden, discord.HTTPException):
        log.info("Could not DM user %s for notification '%s' (DMs may be closed)", user.id, title)
        return False


async def notify_once(
    guild_id: int,
    user: discord.abc.User,
    notification_key: str,
    title: str,
    description: str,
    *,
    include_application: bool = False,
) -> bool:
    if not store.reserve_dm_notification(guild_id, user.id, notification_key):
        return False
    return await send_private_notice(
        user, title, description, include_application=include_application
    )


def has_clan_role(member: discord.Member) -> bool:
    return any(role.id == CLAN_ROLE_ID for role in member.roles)


def code_block(value: str, limit: int = 1010) -> str:
    # Stop form input from breaking the embed's markdown fence or creating mentions.
    safe = (value or "—").replace("`", "ˋ")
    if len(safe) > limit:
        safe = safe[: limit - 3] + "..."
    return f"```{safe}```"


def make_panel_embed(settings: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=settings["panel_title"],
        description=settings["panel_description"],
        color=PURPLE,
    )
    embed.add_field(
        name="Как подать заявку",
        value="Нажми кнопку ниже, открой ветку заявки и ответь на анкету одним сообщением.",
        inline=False,
    )
    is_open = bool(settings.get("applications_open", True))
    embed.add_field(
        name="Набор в клан",
        value="🟢 Открыт" if is_open else "🔴 Сейчас закрыт. Следи за объявлениями о следующем наборе.",
        inline=False,
    )
    embed.set_footer(text="RIval • система заявок")
    image = ROOT / "img" / "icon.jpg"
    if image.is_file():
        embed.set_thumbnail(url="attachment://icon.jpg")
    return embed


def make_ticket_embed(ticket: dict[str, Any], guild: discord.Guild) -> discord.Embed:
    claimer = f"<@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "никто"
    invited_by = f" (<@{ticket['interview_invited_by']}>)" if ticket.get("interview_invited_by") else ""
    completed_by = f" (<@{ticket['interview_completed_by']}>)" if ticket.get("interview_completed_by") else ""
    accepted_by = f" (<@{ticket['accepted_by']}>)" if ticket.get("accepted_by") else ""
    answers = ticket.get("answers", {})
    ticket_type = ticket.get("ticket_type", "clan")
    is_recruiter_application = ticket_type == "recruiter"
    submitted_answer = str(answers.get("response", "")).strip()
    pending_text = (
        "Ожидайте, пока заявку рассмотрит куратор.\n\n"
        if is_recruiter_application else "Ожидайте, пока вашу заявку возьмёт рекрут.\n\n"
    )
    description = (
        pending_text +
        f"**Открыл:** <@{ticket['user_id']}>\n"
        f"**Взял:** {claimer}\n"
        f"**Обзвон:** {'приглашён' if ticket.get('interview_invited_at') else 'не назначен'}{invited_by}\n"
        f"**Обзвон проведён:** {'да' if ticket.get('interview_completed_at') else 'нет'}{completed_by}\n"
        f"**Принят в клан:** {'да' if ticket.get('accepted_at') else 'нет'}{accepted_by}\n"
        f"**Статус:** {ticket['status_text']}"
    )
    if not answers:
        description = APPLICATION_INSTRUCTIONS + "\n\n---\n\n" + description
    embed = discord.Embed(
        title=(
            f"Заявка на рекрутера {ticket_label(ticket['number'])}"
            if is_recruiter_application else f"Заявка {ticket_label(ticket['number'])} в клан RIval"
        ),
        description=description,
        color=GREEN if ticket["status"] == "claimed" else PURPLE,
    )
    if submitted_answer:
        # Embed fields are limited to 1024 chars each. Split long one-message
        # applications across fields without dropping the latter half.
        chunks = [submitted_answer[index:index + 1000] for index in range(0, min(len(submitted_answer), 5000), 1000)]
        if len(chunks) == 1:
            embed.add_field(name="Ответы кандидата", value=code_block(chunks[0]), inline=False)
        else:
            for index, chunk in enumerate(chunks, start=1):
                embed.add_field(
                    name=f"Ответы кандидата ({index}/{len(chunks)})",
                    value=code_block(chunk),
                    inline=False,
                )
    else:
        # Keep displaying applications created before the one-message flow.
        labels = RECRUITER_QUESTION_LABELS if is_recruiter_application else CLAN_QUESTION_LABELS
        for key, label in labels:
            if key in answers:
                value = str(answers.get(key, "—")).strip() or "—"
                embed.add_field(name=label, value=code_block(value), inline=False)
    embed.set_footer(text=f"{guild.name} • RIval applications")
    try:
        embed.timestamp = datetime.fromisoformat(ticket["opened_at"])
    except (TypeError, ValueError):
        embed.timestamp = discord.utils.utcnow()
    return embed


def make_support_panel_embed(ticket: dict[str, Any], guild: discord.Guild) -> discord.Embed:
    ticket = dict(ticket)
    ticket["status_text"] = (
        f"взял <@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "ожидает рассмотрения"
    )
    embed = make_ticket_embed(ticket, guild)
    kind = "на рекрутера" if ticket.get("ticket_type") == "recruiter" else "в клан"
    embed.title = f"Панель поддержки • заявка {kind} {ticket_label(ticket['number'])}"
    return embed


def promotion_instruction_embed(settings: dict[str, Any] | None = None) -> discord.Embed:
    main_role_id = (settings or {}).get("main_role_id") or MAIN_ROLE_ID
    embed = discord.Embed(
        title="Повышения в клане RIval",
        description=(
            f"После вступления в клан участнику выдаётся роль <@&{TRAINEE_ROLE_ID}>.\n\n"
            "Во время стажировки активно играйте вместе с кланом и оставайтесь в составе не менее недели. "
            f"После этого администрация рассмотрит активность и сможет заменить её на <@&{main_role_id}> "
            "(второй ранг)."
        ),
        color=RED,
    )
    embed.add_field(name="Этап 1 · Стажировка", value=f"Оставайся в клане не менее 7 дней с ролью <@&{TRAINEE_ROLE_ID}>.", inline=False)
    embed.add_field(name="Этап 2 · Проверка", value="Администрация оценит участие в игре и активность в Discord.", inline=False)
    embed.add_field(
        name="Этап 3 · Второй ранг",
        value=f"После одобрения стажёрская роль заменяется на <@&{main_role_id}>.",
        inline=False,
    )
    embed.add_field(
        name="Следующий шаг · Рекрутер",
        value=(
            f"После получения <@&{main_role_id}> можно дополнительно подать заявку на роль рекрутера "
            "в канале <#1559169856929005628>. В заявке расскажи о своём опыте принятия участников."
        ),
        inline=False,
    )
    embed.set_footer(text="RIval • повышения")
    return embed


def promotion_review_embed(
    member: discord.Member, trainee_since: str, main_role_id: int = MAIN_ROLE_ID
) -> discord.Embed:
    since = datetime.fromisoformat(trainee_since)
    embed = discord.Embed(
        title="Проверка стажировки",
        description=f"Участник {member.mention} состоит на стажировке не менее 7 дней.",
        color=RED,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Роль", value=f"<@&{TRAINEE_ROLE_ID}>", inline=True)
    embed.add_field(name="Стажировка началась", value=discord.utils.format_dt(since, style="R"), inline=True)
    embed.add_field(
        name="Что сделать",
        value=f"Открой **Статистика**, затем одобри выдачу роли <@&{main_role_id}> или отклони повышение.",
        inline=False,
    )
    return embed


def format_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


def make_profile_embed(
    user: discord.abc.User,
    member: discord.Member | None,
    guild: discord.Guild | None,
    settings: dict[str, Any] | None,
) -> discord.Embed:
    """Build the caller's personal profile for either a guild or DM command."""
    now = discord.utils.utcnow()
    embed = discord.Embed(
        title=f"Профиль • {user.display_name}",
        description=f"Discord: {user.mention}\nID: `{user.id}`",
        color=RED,
        timestamp=now,
    )
    embed.set_author(name="RIval Clan", icon_url=user.display_avatar.url)
    if (ROOT / "img" / "icon.jpg").is_file():
        embed.set_thumbnail(url="attachment://icon.jpg")
    embed.add_field(
        name="Аккаунт создан",
        value=discord.utils.format_dt(user.created_at, style="D"),
        inline=True,
    )

    if member is None or guild is None or settings is None:
        embed.add_field(name="Статус на сервере", value="Не найден участник сервера RIval.", inline=False)
        embed.set_footer(text="RIval • личный профиль")
        return embed

    roles: list[discord.Role] = []
    role_ids = {
        CLAN_ROLE_ID,
        TRAINEE_ROLE_ID,
        int(settings.get("main_role_id") or MAIN_ROLE_ID),
    }
    recruiter_role_id = settings.get("recruiter_role_id")
    if str(recruiter_role_id or "").isdigit():
        role_ids.add(int(recruiter_role_id))
    roles = [role for role in member.roles if role.id in role_ids]
    accepted = store.get_acceptance(guild.id, member.id)
    if accepted is None:
        accepted_ticket = store.find_last_accepted_ticket(guild.id, member.id)
        if accepted_ticket:
            accepted = {
                "accepted_at": accepted_ticket.get("accepted_at"),
                "accepted_by": accepted_ticket.get("accepted_by"),
                "ticket_number": accepted_ticket.get("number"),
            }

    clan_status = "🟢 Состоит в клане" if has_clan_role(member) else "⚪ Не состоит в клане"
    if roles:
        clan_status += "\nРоли: " + ", ".join(role.mention for role in roles)
    embed.add_field(name="Статус RIval", value=clan_status, inline=False)
    embed.add_field(
        name="На сервере с",
        value=discord.utils.format_dt(member.joined_at, style="f") if member.joined_at else "нет данных",
        inline=False,
    )

    if accepted:
        try:
            accepted_at = discord.utils.format_dt(datetime.fromisoformat(accepted["accepted_at"]), style="f")
        except (TypeError, ValueError, KeyError):
            accepted_at = "дата не сохранена"
        accepted_by = f"<@{accepted['accepted_by']}>" if accepted.get("accepted_by") else "неизвестно"
        ticket_number = accepted.get("ticket_number")
        accepted_detail = f"**Кто принял:** {accepted_by}\n**Когда:** {accepted_at}"
        if ticket_number:
            accepted_detail += f"\n**Заявка:** {ticket_label(int(ticket_number))}"
        if not has_clan_role(member):
            accepted_detail += "\n**Сейчас:** роль Clan отсутствует"
        embed.add_field(name="История вступления", value=accepted_detail, inline=False)
    elif has_clan_role(member):
        embed.add_field(name="История вступления", value="Роль Clan есть, но запись о принятии не найдена.", inline=False)

    trainee = store.get_trainee(guild.id, member.id)
    if trainee and has_role_id(member, TRAINEE_ROLE_ID):
        try:
            trainee_since = discord.utils.format_dt(datetime.fromisoformat(trainee["trainee_since"]), style="f")
        except (TypeError, ValueError, KeyError):
            trainee_since = "дата не сохранена"
        review_status = {
            "waiting": "идёт стажировка",
            "review_pending": "ожидает решения по повышению",
            "approved": "повышение одобрено",
            "declined": "повышение отклонено",
            "removed": "стажировка завершена",
        }.get(trainee.get("review_status", ""), "неизвестен")
        embed.add_field(
            name="Стажировка",
            value=f"Начало: {trainee_since}\nСтатус: {review_status}",
            inline=False,
        )

    week_start = now - timedelta(days=7)
    if member.joined_at is not None:
        week_start = max(week_start, member.joined_at)
    activity = store.get_member_activity(guild.id, member.id, week_start.isoformat())
    last_message = activity["last_message_at"]
    try:
        last_message_text = discord.utils.format_dt(datetime.fromisoformat(last_message), style="R") if last_message else "нет данных"
    except (TypeError, ValueError):
        last_message_text = "нет данных"
    embed.add_field(
        name="Активность за 7 дней",
        value=(
            f"Сообщений: **{activity['messages']}** • активных дней: **{activity['active_days']}**\n"
            f"В голосовых: **{format_duration(activity['voice_seconds'])}** • "
            f"средняя сессия: **{format_duration(activity['average_session_seconds'])}**\n"
            f"Последнее сообщение: {last_message_text}"
        ),
        inline=False,
    )
    current_voice = member.voice.channel.mention if member.voice and member.voice.channel else "не в голосовом канале"
    embed.add_field(name="Сейчас в войсе", value=current_voice, inline=True)
    embed.set_footer(text=f"{guild.name} • RIval • учёт активности включён после запуска бота")
    return embed


def is_promotion_admin(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(
        role.id in PROMOTION_ADMIN_ROLE_IDS for role in member.roles
    )


def parse_promotion_date(value: str) -> datetime | None:
    """Parse an admin-entered Moscow date/time into an aware UTC datetime."""
    raw = value.strip()
    candidates = ("%d.%m.%Y", "%d.%m.%Y %H:%M", "%Y-%m-%d", "%Y-%m-%d %H:%M")
    parsed: datetime | None = None
    for pattern in candidates:
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW_TZ)
    return parsed.astimezone(timezone.utc)


def member_has_trainee(member: discord.Member) -> bool:
    return any(role.id == TRAINEE_ROLE_ID for role in member.roles)


def setup_embed(settings: dict[str, Any]) -> discord.Embed:
    log_channel = f"`{settings['log_channel_id']}`" if settings.get("log_channel_id") else "не задан"
    panel_channel = f"`{settings['panel_channel_id']}`" if settings.get("panel_channel_id") else "не задан"
    support = ", ".join(f"`{role_id}`" for role_id in settings["support_role_ids"]) or "не заданы"
    recruitment = "🟢 Открыт" if settings.get("applications_open", True) else "🔴 Закрыт"
    recruit_channel = f"<#{settings['recruit_panel_channel_id']}>" if settings.get("recruit_panel_channel_id") else "не задан"
    review_channel = f"<#{settings['rank_review_channel_id']}>" if settings.get("rank_review_channel_id") else "не задан"
    recruit_role = f"<@&{settings['recruiter_role_id']}>" if settings.get("recruiter_role_id") else "не выбрана"
    curator_role = f"<@&{settings['curator_role_id']}>" if settings.get("curator_role_id") else "не задана"
    embed = discord.Embed(
        title="Настройка RIval Tickets",
        description=(
            f"Выбери раздел ниже. Настройки сохраняются сразу.\n**Набор в клан:** {recruitment}\n"
            f"**Набор рекрутеров:** {'🟢 Открыт' if settings.get('recruit_applications_open', True) else '🔴 Закрыт'}"
        ),
        color=BLUE,
    )
    embed.add_field(name="Панель", value=f"**{discord.utils.escape_markdown(settings['panel_title'])}**\nКанал: {panel_channel}", inline=False)
    embed.add_field(name="Ветки", value=f"Создаются прямо под каналом панели: {panel_channel}\nКатегория берётся у канала панели", inline=True)
    embed.add_field(name="Доступ", value=f"Подача: `{settings['applicant_role_id']}`\nПоддержка: {support}", inline=True)
    embed.add_field(name="Логи", value=log_channel, inline=False)
    embed.add_field(
        name="Набор рекрутеров",
        value=f"Панель: {recruit_channel}\nВыдаваемая роль: {recruit_role}\nКуратор: {curator_role}",
        inline=True,
    )
    embed.add_field(name="Запросы на выдачу ролей", value=review_channel, inline=True)
    return embed


async def send_log(
    guild: discord.Guild,
    settings: dict[str, Any],
    action: str,
    actor: discord.abc.User,
    thread: discord.Thread,
    details: str | None = None,
    mention_role_ids: set[int] | None = None,
) -> None:
    channel_id = settings.get("log_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        log.warning("Log channel is unavailable in guild %s", guild.id)
        return
    embed = discord.Embed(title=f"Действие в заявке: {action}", color=BLUE, timestamp=discord.utils.utcnow())
    embed.add_field(name="Кто сделал", value=f"{actor.mention} (`{actor.id}`)", inline=False)
    embed.add_field(name="Ветка", value=f"{thread.mention} (`{thread.name}`)", inline=False)
    if details:
        embed.add_field(name="Детали", value=details[:1024], inline=False)
    roles = [guild.get_role(role_id) for role_id in (mention_role_ids or set())]
    roles = [role for role in roles if role is not None]
    content = " ".join(role.mention for role in roles) or None
    try:
        await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=(
                discord.AllowedMentions(users=False, roles=roles, everyone=False, replied_user=False)
                if roles else discord.AllowedMentions.none()
            ),
        )
    except discord.HTTPException:
        log.exception("Could not send a ticket log in guild %s", guild.id)


async def send_rank_request_log(
    guild: discord.Guild,
    settings: dict[str, Any],
    action: str,
    actor: discord.abc.User,
    thread: discord.Thread,
    details: str | None = None,
) -> None:
    channel_id = settings.get("rank_review_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
    if channel is None and channel_id and str(channel_id).isdigit():
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if not isinstance(channel, discord.TextChannel):
        log.warning("Rank request channel is unavailable in guild %s", guild.id)
        return
    embed = discord.Embed(title=action, color=RED, timestamp=discord.utils.utcnow())
    embed.add_field(name="Кто сделал", value=f"{actor.mention} (`{actor.id}`)", inline=False)
    embed.add_field(name="Ветка заявки", value=f"{thread.mention} (`{thread.name}`)", inline=False)
    if details:
        embed.add_field(name="Детали", value=details[:1024], inline=False)
    curator_id = settings.get("curator_role_id") or CURATOR_ROLE_ID
    curator = guild.get_role(int(curator_id)) if str(curator_id).isdigit() else None
    try:
        await channel.send(
            content=curator.mention if curator else None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=[curator] if curator else False, users=False),
        )
    except discord.HTTPException:
        log.exception("Could not send a rank request log in guild %s", guild.id)


async def ensure_ticket_parent(guild: discord.Guild, settings: dict[str, Any]) -> discord.TextChannel:
    # Новые заявки всегда создаются под каналом, где опубликована панель.
    # Это устраняет зависимость от устаревшей/удалённой категории из старой настройки.
    panel_id = settings.get("panel_channel_id")
    parent = guild.get_channel(int(panel_id)) if panel_id and str(panel_id).isdigit() else None
    if not isinstance(parent, discord.TextChannel):
        raise ValueError("Не найден канал панели. Сначала выбери канал и нажми «Опубликовать панель».")

    me = guild.me
    if me is None:
        raise ValueError("Бот ещё не получил участника сервера. Попробуй повторить через несколько секунд.")
    permissions = parent.permissions_for(me)
    missing = [name for name, allowed in (
        ("Просмотр канала", permissions.view_channel),
        ("Создание приватных веток", permissions.create_private_threads),
        ("Отправка сообщений в ветках", permissions.send_messages_in_threads),
    ) if not allowed]
    if missing:
        raise ValueError("В канале панели нет прав бота: " + ", ".join(missing) + ".")

    # Сохраняем фактический родительский канал для совместимости со старыми настройками.
    store.update_settings(guild.id, ticket_parent_channel_id=parent.id)
    return parent


async def sync_ticket_thread_access(
    guild: discord.Guild,
    thread: discord.Thread,
    settings: dict[str, Any],
    *,
    ticket_type: str = "clan",
) -> int:
    """Add every current support/admin member to a private application thread.

    Role overwrites on the parent channel do not reliably make private-thread
    members appear in the Discord client, so private threads need explicit
    membership. This is also used on startup to repair older tickets.
    """
    role_values = settings.get("support_role_ids", [])
    if ticket_type == "recruiter":
        role_values = [settings.get("curator_role_id") or CURATOR_ROLE_ID]
    support_role_ids = {
        int(role_id) for role_id in role_values
        if str(role_id).isdigit()
    }
    targets = [
        member for member in guild.members
        if member.guild_permissions.administrator
        or any(role.id in support_role_ids for role in member.roles)
    ]
    existing_ids = {member.id for member in thread.members}
    added = 0
    for member in targets:
        if guild.me is not None and member.id == guild.me.id:
            continue
        if member.id in existing_ids:
            continue
        try:
            await thread.add_user(member)
            added += 1
            existing_ids.add(member.id)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("Could not add %s to ticket thread %s", member.id, thread.id)
    return added


async def get_ticket_thread(guild: discord.Guild, thread_id: int) -> discord.Thread | None:
    """Resolve a ticket thread even when it is not present in the local cache."""
    thread = guild.get_thread(thread_id)
    if isinstance(thread, discord.Thread):
        return thread
    try:
        channel = await guild.fetch_channel(thread_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
    return channel if isinstance(channel, discord.Thread) else None


async def create_application_ticket(
    interaction: discord.Interaction,
    answers: dict[str, str],
) -> None:
    """Create the clan application after the applicant submits the modal."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Заявки можно подавать только на сервере.", ephemeral=True)

    guild = interaction.guild
    applicant = interaction.user
    settings = store.ensure_guild(guild.id)
    if not settings.get("applications_open", True):
        return await interaction.response.send_message(
            "В данный момент набор в клан закрыт. Следи за объявлениями о следующем наборе.",
            ephemeral=True,
            delete_after=60,
        )
    role_id = settings.get("applicant_role_id")
    role = guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
    if role is None or role not in applicant.roles:
        return await interaction.response.send_message("Подать заявку можно только с нужной ролью.", ephemeral=True)

    acceptance = store.get_acceptance(guild.id, applicant.id) or store.find_last_accepted_ticket(guild.id, applicant.id)
    if has_clan_role(applicant) and acceptance:
        return await interaction.response.send_message(
            "Ты уже принят в клан и состоишь в нём. Повторная заявка не нужна.",
            ephemeral=True,
            delete_after=60,
        )

    current = store.find_open_ticket(guild.id, applicant.id, ticket_type="clan")
    if current:
        return await interaction.response.send_message(
            f"У тебя уже есть открытая заявка: <#{current['thread_id']}>.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True, thinking=True)
    ticket_saved = False
    thread: discord.Thread | None = None
    try:
        parent = await ensure_ticket_parent(guild, settings)
        next_number = store.list_tickets(guild.id, limit=1)
        number_hint = (next_number[0]["number"] + 1) if next_number else 1
        thread = await parent.create_thread(
            name=f"заявка-{number_hint:03d}",
            type=discord.ChannelType.private_thread,
            invitable=False,
            reason=f"Заявка RIval от {applicant} ({applicant.id})",
        )
        await thread.add_user(applicant)
        ticket = store.create_ticket(
            guild.id, thread.id, applicant.id, answers, datetime.now(timezone.utc).isoformat(),
            ticket_type="clan",
        )
        ticket_saved = True
        await thread.edit(name=f"заявка-{ticket['number']:03d}")
        await sync_ticket_thread_access(guild, thread, settings)
        ticket["status_text"] = "ожидает рассмотрения рекрутерами"
        ping_role_id = settings.get("recruiter_ping_role_id") or RECRUITER_PING_ROLE_ID
        recruiter_ping_role = guild.get_role(int(ping_role_id)) if str(ping_role_id).isdigit() else None
        first_message = await thread.send(
            content=recruiter_ping_role.mention if recruiter_ping_role else None,
            embed=make_ticket_embed(ticket, guild),
            view=TicketView(),
            allowed_mentions=discord.AllowedMentions(
                roles=[recruiter_ping_role] if recruiter_ping_role else False,
                users=False,
                everyone=False,
            ),
        )
        store.update_ticket(guild.id, thread.id, ticket_message_id=first_message.id)
        await send_log(guild, settings, "заявка создана", applicant, thread)
        await notify_once(
            guild.id,
            applicant,
            f"ticket-created:{thread.id}",
            "Заявка отправлена рекрутерам",
            f"Заявка {ticket_label(ticket['number'])} создана. Рассмотрение может занять до 24 часов.\n\n"
            f"[Открыть ветку заявки]({thread.jump_url})",
        )
        await interaction.followup.send(
            f"Заявка {ticket_label(ticket['number'])} отправлена: {thread.mention}. "
            "Ожидай ответа рекрутеров — рассмотрение занимает до 24 часов.",
            ephemeral=True,
        )
    except (ValueError, discord.Forbidden, discord.HTTPException) as error:
        if thread is not None and not ticket_saved:
            try:
                await thread.delete(reason="Не удалось сохранить заявку")
            except discord.HTTPException:
                pass
        log.exception("Could not create ticket in guild %s", guild.id)
        if ticket_saved and thread is not None:
            await interaction.followup.send(
                f"Ветка {thread.mention} создана, но не удалось отправить одно из сообщений. Сообщи поддержке.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(f"Не получилось создать заявку: {error}", ephemeral=True)


class ApplicationModal(discord.ui.Modal, title="Заявка в клан RIval"):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.inputs: dict[str, discord.ui.TextInput] = {}
        fields = (
            ("identity", "Ник, имя, возраст и ТГ (@username)", "Например: Steve, Алексей, 17, @username", True),
            ("timezone", "Софт, сервера и разница с МСК", "Укажи софт, сервера и часовой пояс", True),
            ("experience", "Опыт в хвх/пвп", "Расскажи о своём опыте в хвх и пвп", True),
            ("sets", "Сеты и делёжка ресурсами", "Готов ли закупать сеты и делиться ресурсами?", True),
            ("media", "Канал и медиа-статус в читах", "Ссылка на канал и статус медиа, если есть", False),
        )
        for key, label, placeholder, required in fields:
            field = discord.ui.TextInput(
                custom_id=f"clan_{key}",
                label=label,
                placeholder=placeholder,
                style=discord.TextStyle.paragraph,
                required=required,
                max_length=800,
            )
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_application_ticket(
            interaction,
            {key: field.value.strip() for key, field in self.inputs.items()},
        )


def make_recruit_panel_embed(settings: dict[str, Any]) -> discord.Embed:
    open_for_applications = bool(settings.get("recruit_applications_open", True))
    embed = discord.Embed(
        title="Набор рекрутеров RIval",
        description=(
            "Есть роль <@&{main_role}> (2 ранг) и хочешь помогать клану с набором? "
            "Подай заявку — куратор рассмотрит её и пригласит на собеседование."
        ).format(main_role=settings.get("main_role_id") or MAIN_ROLE_ID),
        color=RED,
    )
    embed.add_field(
        name="В анкете спросят",
        value="1. Твой ник\n2. Опыт в рекрутинге и принятии участников\n3. Немного о себе",
        inline=False,
    )
    embed.add_field(
        name="Набор рекрутеров",
        value="🟢 Открыт" if open_for_applications else "🔴 Временно закрыт",
        inline=False,
    )
    if not settings.get("recruiter_role_id"):
        embed.add_field(
            name="Настройка",
            value="Администратору нужно выбрать выдаваемую роль рекрутера в `/setup`.",
            inline=False,
        )
    embed.set_footer(text="RIval • набор рекрутеров")
    return embed


class RecruitApplicationModal(discord.ui.Modal, title="Заявка на рекрутера"):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.inputs: dict[str, discord.ui.TextInput] = {}
        for key, label, placeholder in (
            ("nickname", "Твой ник", "Ник в Minecraft и/или Discord"),
            ("experience", "Опыт в рекрутинге", "Расскажи об опыте набора и принятия людей"),
            ("about", "Немного о себе", "Почему хочешь стать рекрутером?"),
        ):
            field = discord.ui.TextInput(
                custom_id=f"recruit_{key}", label=label, placeholder=placeholder,
                style=discord.TextStyle.paragraph, required=True, max_length=1000,
            )
            self.inputs[key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_recruit_application(
            interaction,
            {key: field.value.strip() for key, field in self.inputs.items()},
        )


class RecruitPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Подать заявку на рекрутера", emoji="📝", style=discord.ButtonStyle.danger,
        custom_id="rival:recruit-apply",
    )
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Кнопка доступна только на сервере.", ephemeral=True)
        settings = store.ensure_guild(interaction.guild.id)
        if not settings.get("recruit_applications_open", True):
            return await interaction.response.send_message("Набор рекрутеров сейчас закрыт.", ephemeral=True, delete_after=60)
        main_role_id = settings.get("main_role_id") or MAIN_ROLE_ID
        main_role = interaction.guild.get_role(int(main_role_id)) if str(main_role_id).isdigit() else None
        if main_role is None or main_role not in interaction.user.roles:
            return await interaction.response.send_message(
                f"Подать заявку на рекрутера можно только после получения роли Main (<@&{main_role_id}>).",
                ephemeral=True,
            )
        current = store.find_open_ticket(interaction.guild.id, interaction.user.id, ticket_type="recruiter")
        if current:
            return await interaction.response.send_message(
                f"У тебя уже есть открытая заявка на рекрутера: <#{current['thread_id']}>.", ephemeral=True
            )
        await interaction.response.send_modal(RecruitApplicationModal())


async def create_recruit_application(interaction: discord.Interaction, answers: dict[str, str]) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Заявка доступна только на сервере.", ephemeral=True)
    guild = interaction.guild
    applicant = interaction.user
    settings = store.ensure_guild(guild.id)
    if not settings.get("recruit_applications_open", True):
        return await interaction.response.send_message("Набор рекрутеров сейчас закрыт.", ephemeral=True, delete_after=60)
    main_role_id = settings.get("main_role_id") or MAIN_ROLE_ID
    main_role = guild.get_role(int(main_role_id)) if str(main_role_id).isdigit() else None
    if main_role is None or main_role not in applicant.roles:
        return await interaction.response.send_message("Для заявки на рекрутера нужна роль Main.", ephemeral=True)
    if store.find_open_ticket(guild.id, applicant.id, ticket_type="recruiter"):
        return await interaction.response.send_message("У тебя уже есть открытая заявка на рекрутера.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    thread: discord.Thread | None = None
    ticket_saved = False
    try:
        parent_id = settings.get("recruit_panel_channel_id")
        parent = guild.get_channel(int(parent_id)) if parent_id and str(parent_id).isdigit() else None
        if not isinstance(parent, discord.TextChannel):
            raise ValueError("Не найден канал панели заявок на рекрутера. Настрой его в `/setup`.")
        me = guild.me
        if me is None:
            raise ValueError("Бот ещё не получил доступ к участникам сервера.")
        permissions = parent.permissions_for(me)
        if not (permissions.view_channel and permissions.create_private_threads and permissions.send_messages_in_threads):
            raise ValueError("Боту нужны права просмотра канала, создания приватных веток и сообщений в ветках.")
        next_ticket = store.list_tickets(guild.id, limit=1)
        number_hint = (next_ticket[0]["number"] + 1) if next_ticket else 1
        thread = await parent.create_thread(
            name=f"рекрут-{number_hint:03d}", type=discord.ChannelType.private_thread, invitable=False,
            reason=f"Заявка на рекрутера от {applicant} ({applicant.id})",
        )
        await thread.add_user(applicant)
        ticket = store.create_ticket(
            guild.id, thread.id, applicant.id, answers, datetime.now(timezone.utc).isoformat(),
            ticket_type="recruiter",
        )
        ticket_saved = True
        await thread.edit(name=f"рекрут-{ticket['number']:03d}")
        await sync_ticket_thread_access(guild, thread, settings, ticket_type="recruiter")
        ticket["status_text"] = "ожидает рассмотрения куратором"
        curator_id = settings.get("curator_role_id") or CURATOR_ROLE_ID
        curator = guild.get_role(int(curator_id)) if str(curator_id).isdigit() else None
        first_message = await thread.send(
            content=f"<@&{curator_id}>" if curator else None,
            embed=make_ticket_embed(ticket, guild),
            view=TicketView(),
            allowed_mentions=discord.AllowedMentions(roles=[curator] if curator else False, users=False),
        )
        store.update_ticket(guild.id, thread.id, ticket_message_id=first_message.id)
        await send_log(
            guild, settings, "заявка на роль рекрутера создана", applicant, thread,
            f"Кандидат: {applicant.mention}",
            mention_role_ids={int(curator_id)} if str(curator_id).isdigit() else None,
        )
        await send_rank_request_log(
            guild, settings, "Новая заявка на роль рекрутера", applicant, thread,
            f"Кандидат имеет роль {main_role.mention}. Куратор: {curator.mention if curator else 'не настроен'}.",
        )
        await notify_once(
            guild.id,
            applicant,
            f"recruit-ticket-created:{thread.id}",
            "Заявка на роль рекрутера отправлена",
            f"Заявка {ticket_label(ticket['number'])} отправлена куратору.\n\n"
            f"[Открыть ветку заявки]({thread.jump_url})",
        )
        await interaction.followup.send(
            f"Заявка на рекрутера {ticket_label(ticket['number'])} отправлена: {thread.mention}.", ephemeral=True
        )
    except (ValueError, discord.Forbidden, discord.HTTPException) as error:
        if thread is not None and not ticket_saved:
            try:
                await thread.delete(reason="Не удалось сохранить заявку на рекрутера")
            except discord.HTTPException:
                pass
        log.exception("Could not create recruiter application in guild %s", guild.id)
        await interaction.followup.send(f"Не удалось создать заявку: {error}", ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Войти на сервер RIval",
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=SERVER_INVITE_URL,
        ))

    @discord.ui.button(label="Подать заявку", emoji="📨", style=discord.ButtonStyle.danger, custom_id="rival:apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Кнопка работает только на сервере.", ephemeral=True)
        settings = store.ensure_guild(interaction.guild.id)
        if not settings.get("applications_open", True):
            return await interaction.response.send_message("Сейчас набор в клан закрыт.", ephemeral=True, delete_after=60)
        applicant_role = interaction.guild.get_role(int(settings["applicant_role_id"])) if str(settings.get("applicant_role_id", "")).isdigit() else None
        if applicant_role is None or applicant_role not in interaction.user.roles:
            return await interaction.response.send_message("Подать заявку можно только с нужной ролью.", ephemeral=True)
        accepted = store.get_acceptance(interaction.guild.id, interaction.user.id) or store.find_last_accepted_ticket(
            interaction.guild.id, interaction.user.id
        )
        if has_clan_role(interaction.user) and accepted:
            return await interaction.response.send_message("Ты уже принят в клан.", ephemeral=True, delete_after=60)
        current = store.find_open_ticket(interaction.guild.id, interaction.user.id, ticket_type="clan")
        if current:
            return await interaction.response.send_message(
                f"У тебя уже есть открытая заявка: <#{current['thread_id']}>.", ephemeral=True
            )
        await interaction.response.send_modal(ApplicationModal())


class TicketView(discord.ui.View):
    def __init__(
        self, *, claimed: bool = False, closing: bool = False, awaiting_application: bool = False
    ) -> None:
        super().__init__(timeout=None)
        take = discord.ui.Button(
            label="Взять заявку", emoji="🙋", style=discord.ButtonStyle.success,
            custom_id="rival:take", disabled=claimed or closing or awaiting_application,
        )
        close = discord.ui.Button(
            label="Закрыть", emoji="🔒", style=discord.ButtonStyle.danger,
            custom_id="rival:close_support", disabled=closing,
        )
        close_user = discord.ui.Button(
            label="Закрыть (для юзера)", emoji="🚪", style=discord.ButtonStyle.secondary,
            custom_id="rival:close_user", disabled=closing,
        )
        support_panel = discord.ui.Button(
            label="Панель поддержки", emoji="🛠️", style=discord.ButtonStyle.secondary,
            custom_id="rival:support_panel", disabled=closing,
        )
        take.callback = self.take_ticket
        close.callback = self.close_as_support
        close_user.callback = self.close_as_user
        support_panel.callback = self.open_support_panel
        self.add_item(take)
        self.add_item(close)
        self.add_item(close_user)
        self.add_item(support_panel)

    async def _get_ticket(self, interaction: discord.Interaction) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Заявка доступна только на сервере.", ephemeral=True)
            return None
        ticket = store.get_ticket(interaction.guild.id, interaction.channel.id)
        if ticket is None:
            await interaction.response.send_message("Эта ветка не является заявкой.", ephemeral=True)
            return None
        return ticket, store.get_settings(interaction.guild.id)

    async def take_ticket(self, interaction: discord.Interaction) -> None:
        info = await self._get_ticket(interaction)
        if info is None:
            return
        ticket, settings = info
        if not isinstance(interaction.user, discord.Member) or not support_member(interaction.user, settings, ticket.get("ticket_type", "clan")):
            return await interaction.response.send_message("Только поддержка может взять заявку.", ephemeral=True)
        if interaction.user.id == int(ticket["user_id"]):
            return await interaction.response.send_message("Нельзя взять собственную заявку.", ephemeral=True)
        if not ticket.get("answers"):
            return await interaction.response.send_message("Автор заявки ещё не заполнил анкету.", ephemeral=True)
        ticket = store.claim_ticket(interaction.guild.id, interaction.channel.id, interaction.user.id)
        if ticket is None:
            ticket = store.get_ticket(interaction.guild.id, interaction.channel.id) or info[0]
            return await interaction.response.send_message(
                f"Эту заявку уже взяли <@{ticket['claimed_by']}>." if ticket.get("claimed_by") else "Эта заявка уже закрывается.",
                ephemeral=True,
            )
        assert isinstance(interaction.channel, discord.Thread)
        ticket["status_text"] = f"взял <@{interaction.user.id}>"
        # Сначала подтверждаем нажатие кнопки обновлением исходного сообщения.
        # Это не даёт Discord истечь interaction, пока отправляется уведомление в ветку.
        await interaction.response.edit_message(
            embed=make_ticket_embed(ticket, interaction.guild), view=TicketView(claimed=True)
        )
        claim_embed = discord.Embed(
            title="Заявка взята в работу",
            description=f"Заявку {ticket_label(ticket['number'])} взял рекрут {interaction.user.mention}.\n\nОжидайте дальнейших сообщений в этой ветке.",
            color=GREEN,
            timestamp=discord.utils.utcnow(),
        )
        try:
            await interaction.channel.send(
                embed=claim_embed,
                allowed_mentions=discord.AllowedMentions(users=[interaction.user], roles=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not announce ticket claim in thread %s", interaction.channel.id)
        applicant = interaction.guild.get_member(int(ticket["user_id"]))
        if applicant is None:
            try:
                applicant = await interaction.guild.fetch_member(int(ticket["user_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                applicant = None
        if applicant is not None:
            await notify_once(
                interaction.guild.id,
                applicant,
                f"ticket-claimed:{interaction.channel.id}",
                "Твою заявку взяли в работу",
                f"Заявку {ticket_label(ticket['number'])} рассматривает рекрут.\n\n"
                f"[Открыть ветку заявки]({interaction.channel.jump_url})",
            )
        await send_log(interaction.guild, settings, "заявка принята", interaction.user, interaction.channel)

    async def close_as_support(self, interaction: discord.Interaction) -> None:
        info = await self._get_ticket(interaction)
        if info is None:
            return
        ticket, settings = info
        if not isinstance(interaction.user, discord.Member) or not support_member(interaction.user, settings, ticket.get("ticket_type", "clan")):
            return await interaction.response.send_message("Закрыть заявку может только поддержка.", ephemeral=True)
        await request_close(interaction, ticket, settings)

    async def close_as_user(self, interaction: discord.Interaction) -> None:
        info = await self._get_ticket(interaction)
        if info is None:
            return
        ticket, settings = info
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Не удалось проверить участника.", ephemeral=True)
        if interaction.user.id != int(ticket["user_id"]) and not support_member(interaction.user, settings, ticket.get("ticket_type", "clan")):
            return await interaction.response.send_message("Закрыть заявку может её автор или поддержка.", ephemeral=True)
        await request_close(interaction, ticket, settings)

    async def open_support_panel(self, interaction: discord.Interaction) -> None:
        info = await self._get_ticket(interaction)
        if info is None:
            return
        ticket, settings = info
        if not isinstance(interaction.user, discord.Member) or not support_member(interaction.user, settings, ticket.get("ticket_type", "clan")):
            return await interaction.response.send_message("Панель доступна только поддержке.", ephemeral=True)
        await interaction.response.send_message(
            embed=make_support_panel_embed(ticket, interaction.guild),
            view=SupportPanelView(ticket),
            ephemeral=True,
            delete_after=60,
        )


class SupportPanelView(discord.ui.View):
    def __init__(self, ticket: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.ticket_type = ticket.get("ticket_type", "clan")
        invite = discord.ui.Button(
            label="Позвать на обзвон", emoji="🎙️", style=discord.ButtonStyle.primary,
            custom_id=f"rival:interview:{ticket['thread_id']}",
            disabled=bool(ticket.get("interview_invited_at") or ticket.get("accepted_at")),
        )
        invite.callback = self.invite_to_interview
        self.add_item(invite)
        completed = discord.ui.Button(
            label="Обзвон проведён", emoji="📞", style=discord.ButtonStyle.secondary,
            custom_id=f"rival:interview-complete:{ticket['thread_id']}",
            disabled=not bool(ticket.get("interview_invited_at"))
            or bool(ticket.get("interview_completed_at"))
            or bool(ticket.get("accepted_at")),
        )
        completed.callback = self.complete_interview
        self.add_item(completed)
        accept = discord.ui.Button(
            label="Принять", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"rival:accept:{ticket['thread_id']}",
            disabled=not bool(ticket.get("interview_completed_at")) or bool(ticket.get("accepted_at")),
        )
        accept.callback = self.accept_applicant
        self.add_item(accept)
        reject = discord.ui.Button(
            label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"rival:reject:{ticket['thread_id']}",
            disabled=bool(ticket.get("accepted_at") or ticket.get("rejected_at")),
        )
        reject.callback = self.reject_application
        self.add_item(reject)
        self.thread_id = int(ticket["thread_id"])
        self.applicant_id = int(ticket["user_id"])

    async def invite_to_interview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        if not support_member(interaction.user, settings, self.ticket_type):
            return await interaction.response.send_message("Позвать на обзвон может только куратор.", ephemeral=True)
        # До запросов к Discord обязательно подтверждаем component interaction.
        # Иначе после нескольких send/fetch Discord отвечает 10062 Unknown interaction.
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket = store.get_ticket(interaction.guild.id, self.thread_id)
        if ticket is None:
            return await interaction.followup.send("Заявка уже недоступна.", ephemeral=True)
        if ticket.get("accepted_at"):
            return await interaction.followup.send("Заявка уже принята в клан.", ephemeral=True)
        if not ticket.get("answers"):
            return await interaction.followup.send("Автор заявки ещё не заполнил анкету.", ephemeral=True)
        if ticket["status"] in {"closing", "closed"}:
            return await interaction.followup.send("Нельзя пригласить на обзвон по закрытой заявке.", ephemeral=True)
        if ticket.get("interview_invited_at"):
            return await interaction.followup.send("На эту заявку уже отправляли приглашение на обзвон.", ephemeral=True)
        try:
            applicant = interaction.guild.get_member(self.applicant_id) or await interaction.guild.fetch_member(self.applicant_id)
        except discord.NotFound:
            return await interaction.followup.send("Автор заявки больше не состоит на сервере.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send("Не удалось получить автора заявки с сервера.", ephemeral=True)
        thread = await get_ticket_thread(interaction.guild, self.thread_id)
        if thread is None:
            return await interaction.followup.send("Ветка заявки недоступна. Проверь, что она не удалена.", ephemeral=True)
        invited_at = discord.utils.utcnow().isoformat()
        ticket = store.mark_interview_invited(
            interaction.guild.id, self.thread_id, invited_at, interaction.user.id
        )
        if ticket is None:
            return await interaction.followup.send("Приглашение уже было отправлено или заявка закрывается.", ephemeral=True)
        deadline = discord.utils.utcnow() + timedelta(minutes=10)
        embed = discord.Embed(
            title="Приглашение на обзвон RIval",
            description=(
                f"{applicant.mention}, тебя приглашает на обзвон {interaction.user.mention}.\n\n"
                f"Зайди в голосовой канал <#{INTERVIEW_VOICE_CHANNEL_ID}>. "
                f"У тебя есть **10 минут** — до {discord.utils.format_dt(deadline, style='t')}."
            ),
            color=RED,
        )
        embed.set_footer(text=f"Заявка {ticket_label(ticket['number'])}")
        try:
            # Уведомление участнику находится именно в его тикете, а не в канале повышений.
            await thread.send(
                content=applicant.mention,
                embed=embed,
                # The recruiter may also be the applicant; duplicate IDs in
                # AllowedMentions are rejected by Discord with error 50035.
                allowed_mentions=discord.AllowedMentions(
                    users=list({member.id: member for member in (applicant, interaction.user)}.values()),
                    roles=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not send interview invitation in ticket %s", self.thread_id)
            store.update_ticket(
                interaction.guild.id,
                self.thread_id,
                interview_invited_at=None,
                interview_invited_by=None,
            )
            return await interaction.followup.send("Не удалось отправить приглашение в ветку. Проверь права бота.", ephemeral=True)
        await notify_once(
            interaction.guild.id,
            applicant,
            f"ticket-interview:{self.thread_id}",
            "Тебя пригласили на обзвон RIval",
            f"На обзвон тебя приглашает {interaction.user.display_name}.\n"
            f"Зайди в голосовой канал <#{INTERVIEW_VOICE_CHANNEL_ID}> в течение 10 минут — до "
            f"{discord.utils.format_dt(deadline, style='t')}.\n\n"
            f"[Открыть ветку заявки]({thread.jump_url})",
        )
        await send_log(
            interaction.guild,
            settings,
            "приглашение на обзвон",
            interaction.user,
            thread,
            f"Кого позвали: {applicant.mention}\nСрок: {discord.utils.format_dt(deadline, style='F')}\n"
            f"Канал обзвона: <#{INTERVIEW_VOICE_CHANNEL_ID}>",
            mention_role_ids=(
                {int(settings.get("curator_role_id") or CURATOR_ROLE_ID)}
                if self.ticket_type == "recruiter" else PROMOTION_ADMIN_ROLE_IDS
            ),
        )
        if self.ticket_type == "recruiter":
            await send_rank_request_log(
                interaction.guild, settings, "Куратор пригласил кандидата на собеседование",
                interaction.user, thread,
                f"Кандидат: {applicant.mention}. Срок: {discord.utils.format_dt(deadline, style='F')}.",
            )
        await interaction.edit_original_response(
            content=f"Приглашение отправлено в ветку {thread.mention}. После обзвона нажми «Обзвон проведён», затем «Принять».",
            embed=make_support_panel_embed(ticket, interaction.guild),
            view=SupportPanelView(ticket),
        )

    async def complete_interview(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        if not support_member(interaction.user, settings, self.ticket_type):
            return await interaction.response.send_message("Отметить обзвон может только куратор.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket = store.get_ticket(interaction.guild.id, self.thread_id)
        if ticket is None:
            return await interaction.followup.send("Заявка уже недоступна.", ephemeral=True)
        if ticket["status"] in {"closing", "closed"}:
            return await interaction.followup.send("Нельзя изменить этап обзвона у закрытой заявки.", ephemeral=True)
        if not ticket.get("interview_invited_at"):
            return await interaction.followup.send("Сначала позови участника на обзвон.", ephemeral=True)
        if ticket.get("interview_completed_at"):
            return await interaction.followup.send("Обзвон уже отмечен как проведённый.", ephemeral=True)
        ticket = store.mark_interview_completed(
            interaction.guild.id,
            self.thread_id,
            discord.utils.utcnow().isoformat(),
            interaction.user.id,
        )
        if ticket is None:
            return await interaction.followup.send("Обзвон уже отмечен или заявка закрывается.", ephemeral=True)
        thread = await get_ticket_thread(interaction.guild, self.thread_id)
        if thread is not None:
            try:
                await thread.send(
                    embed=discord.Embed(
                        title="Обзвон проведён",
                        description=f"{interaction.user.mention} отметил, что обзвон участника проведён. Теперь поддержку можно нажать «Принять».",
                        color=GREEN,
                        timestamp=discord.utils.utcnow(),
                    ),
                    allowed_mentions=discord.AllowedMentions(users=[interaction.user], roles=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not announce completed interview in ticket %s", self.thread_id)
            await send_log(
                interaction.guild,
                settings,
                "обзвон проведён",
                interaction.user,
                thread,
                mention_role_ids=(
                    {int(settings.get("curator_role_id") or CURATOR_ROLE_ID)}
                    if self.ticket_type == "recruiter" else PROMOTION_ADMIN_ROLE_IDS
                ),
            )
            if self.ticket_type == "recruiter":
                await send_rank_request_log(
                    interaction.guild, settings, "Собеседование кандидата в рекрутеры проведено",
                    interaction.user, thread,
                )
        await interaction.edit_original_response(
            content="Обзвон отмечен как проведённый. Теперь можно принять участника.",
            embed=make_support_panel_embed(ticket, interaction.guild),
            view=SupportPanelView(ticket),
        )

    async def accept_applicant(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        if not support_member(interaction.user, settings, self.ticket_type):
            return await interaction.response.send_message("Принять заявку может только куратор.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket = store.get_ticket(interaction.guild.id, self.thread_id)
        if ticket is None:
            return await interaction.followup.send("Заявка уже недоступна.", ephemeral=True)
        if ticket["status"] in {"closing", "closed"}:
            return await interaction.followup.send("Нельзя принять участника по закрытой заявке.", ephemeral=True)
        if ticket.get("accepted_at"):
            return await interaction.followup.send("Эта заявка уже принята.", ephemeral=True)
        if not ticket.get("interview_completed_at"):
            return await interaction.followup.send(
                "Сначала пригласи участника на обзвон и отметь его проведённым.", ephemeral=True
            )
        try:
            applicant = interaction.guild.get_member(self.applicant_id) or await interaction.guild.fetch_member(self.applicant_id)
        except discord.NotFound:
            return await interaction.followup.send("Автор заявки больше не состоит на сервере.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.followup.send("Не удалось получить автора заявки с сервера.", ephemeral=True)
        if ticket.get("ticket_type") == "recruiter":
            role_id = settings.get("recruiter_role_id")
            recruiter_role = interaction.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            bot_member = interaction.guild.me
            if recruiter_role is None:
                return await interaction.followup.send(
                    "Роль рекрутера не настроена. Выбери её в `/setup` → «Рекрутинг» → «Роли».", ephemeral=True
                )
            if bot_member is None or bot_member.top_role <= recruiter_role:
                return await interaction.followup.send("Подними роль бота выше роли рекрутера.", ephemeral=True)
            accepted_at = discord.utils.utcnow()
            try:
                await applicant.add_roles(
                    recruiter_role, reason=f"Заявка на рекрутера {ticket_label(ticket['number'])} принята"
                )
            except discord.HTTPException:
                log.exception("Could not assign recruiter role to %s", applicant.id)
                return await interaction.followup.send("Не удалось выдать роль рекрутера. Проверь права и иерархию ролей.", ephemeral=True)
            ticket = store.mark_ticket_accepted(
                interaction.guild.id, self.thread_id,
                accepted_at=accepted_at.isoformat(), accepted_by=interaction.user.id,
            )
            if ticket is None:
                return await interaction.followup.send("Заявку уже обработал другой куратор.", ephemeral=True)
            thread = await get_ticket_thread(interaction.guild, self.thread_id)
            if thread is not None:
                accepted_embed = discord.Embed(
                    title="Заявка на рекрутера принята",
                    description=(
                        f"{applicant.mention} получил роль {recruiter_role.mention}.\n"
                        f"Принял куратор: {interaction.user.mention}\n"
                        f"Заявка: {ticket_label(ticket['number'])}"
                    ),
                    color=RED,
                    timestamp=accepted_at,
                )
                try:
                    await thread.send(
                        embed=accepted_embed,
                        allowed_mentions=discord.AllowedMentions(users=[applicant, interaction.user], roles=[recruiter_role]),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not announce recruiter acceptance in ticket %s", self.thread_id)
                await send_log(
                    interaction.guild, settings, "заявка на рекрутера принята", interaction.user, thread,
                    f"Роль выдана: {recruiter_role.mention}. Кандидат: {applicant.mention}",
                    mention_role_ids={recruiter_role.id},
                )
                await send_rank_request_log(
                    interaction.guild, settings, "Запрос на выдачу роли рекрут одобрен", interaction.user, thread,
                    f"Участнику {applicant.mention} выдана роль {recruiter_role.mention}.",
                )
            await notify_once(
                interaction.guild.id,
                applicant,
                f"recruiter-accepted:{self.thread_id}",
                "Заявка на роль рекрутера принята",
                f"Тебе выдали роль {recruiter_role.name}. Поздравляем!",
            )
            await interaction.edit_original_response(
                content=f"Роль {recruiter_role.mention} выдана, заявка закрыта.",
                embed=make_support_panel_embed(ticket, interaction.guild),
                view=SupportPanelView(ticket),
            )
            return
        trainee_role = interaction.guild.get_role(TRAINEE_ROLE_ID)
        clan_role = interaction.guild.get_role(CLAN_ROLE_ID)
        bot_member = interaction.guild.me
        if trainee_role is None or clan_role is None:
            return await interaction.followup.send("Не найдены роли Trainee или Clan. Проверь ID ролей.", ephemeral=True)
        if bot_member is None or bot_member.top_role <= max(trainee_role, clan_role):
            return await interaction.followup.send(
                "Подними роль бота выше ролей Trainee и Clan в настройках сервера.", ephemeral=True
            )
        accepted_at = discord.utils.utcnow()
        try:
            await applicant.add_roles(trainee_role, clan_role, reason=f"Заявка RIval {ticket_label(ticket['number'])} принята")
            ticket = store.mark_ticket_accepted(
                interaction.guild.id,
                self.thread_id,
                accepted_at=accepted_at.isoformat(),
                accepted_by=interaction.user.id,
            )
            if ticket is None:
                return await interaction.followup.send("Заявку уже принял другой сотрудник.", ephemeral=True)
            store.record_acceptance(
                interaction.guild.id,
                applicant.id,
                self.thread_id,
                ticket["number"],
                accepted_at.isoformat(),
                interaction.user.id,
            )
            store.set_trainee_start(interaction.guild.id, applicant.id, accepted_at.isoformat())
        except discord.HTTPException:
            log.exception("Could not assign trainee/clan roles to %s", applicant.id)
            return await interaction.followup.send("Не удалось выдать роли. Проверь права и иерархию ролей бота.", ephemeral=True)

        accepted_embed = discord.Embed(
            title="Заявка принята в клан RIval",
            description=(
                f"{applicant.mention} принят в клан. Выданы роли <@&{TRAINEE_ROLE_ID}> и <@&{CLAN_ROLE_ID}>.\n"
                f"Принял: {interaction.user.mention}\n"
                f"Тикет: {ticket_label(ticket['number'])}"
            ),
            color=RED,
            timestamp=accepted_at,
        )
        thread = await get_ticket_thread(interaction.guild, self.thread_id)
        if thread is not None:
            try:
                await thread.send(
                    embed=accepted_embed,
                    allowed_mentions=discord.AllowedMentions(users=[applicant], roles=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not announce accepted application in ticket %s", self.thread_id)
            await send_log(
                interaction.guild,
                settings,
                "заявка принята в клан",
                interaction.user,
                thread,
                f"Участник: {applicant.mention}\nВыданы роли: Trainee и Clan (<@&{CLAN_ROLE_ID}>).",
                mention_role_ids=PROMOTION_ADMIN_ROLE_IDS,
            )
        await interaction.edit_original_response(
            content="Заявка принята: роли Trainee и Clan выданы.",
            embed=make_support_panel_embed(ticket, interaction.guild),
            view=SupportPanelView(ticket),
        )

    async def reject_application(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Эта кнопка работает только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        ticket = store.get_ticket(interaction.guild.id, self.thread_id)
        if ticket is None:
            return await interaction.response.send_message("Заявка уже недоступна.", ephemeral=True)
        if not support_member(interaction.user, settings, ticket.get("ticket_type", "clan")):
            return await interaction.response.send_message("Отклонить заявку может только поддержка.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        rejected_at = discord.utils.utcnow()
        ticket = store.mark_ticket_rejected(
            interaction.guild.id, self.thread_id, rejected_at.isoformat(), interaction.user.id
        )
        if ticket is None:
            return await interaction.followup.send("Заявка уже принята, отклонена или закрывается.", ephemeral=True)
        thread = await get_ticket_thread(interaction.guild, self.thread_id)
        applicant = interaction.guild.get_member(self.applicant_id)
        if applicant is None:
            try:
                applicant = await interaction.guild.fetch_member(self.applicant_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                applicant = None
        if thread is not None:
            try:
                await thread.send(
                    embed=discord.Embed(
                        title="Заявка отклонена",
                        description=f"Заявка {ticket_label(ticket['number'])} отклонена поддержкой.",
                        color=RED,
                        timestamp=rejected_at,
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not announce rejected ticket %s", self.thread_id)
            await send_log(interaction.guild, settings, "заявка отклонена", interaction.user, thread)
            if applicant is not None:
                await notify_once(
                    interaction.guild.id,
                    applicant,
                    f"ticket-rejected:{self.thread_id}",
                    "Заявка в клан RIval отклонена",
                    f"Заявка {ticket_label(ticket['number'])} отклонена. Если набор открыт, ты сможешь подать новую заявку позже.",
                )
            try:
                await thread.edit(locked=True, archived=True, reason="Заявка отклонена поддержкой")
            except discord.HTTPException:
                log.exception("Could not archive rejected ticket thread %s", self.thread_id)
        await interaction.edit_original_response(content="Заявка отклонена и закрыта.", embed=None, view=None)


async def request_close(
    interaction: discord.Interaction,
    ticket: dict[str, Any],
    settings: dict[str, Any],
) -> None:
    if interaction.guild is None or not isinstance(interaction.channel, discord.Thread):
        return await interaction.response.send_message("Это не ветка заявки.", ephemeral=True)
    if ticket["status"] in {"closing", "closed"}:
        return await interaction.response.send_message("Эта заявка уже закрывается или закрыта.", ephemeral=True)

    transitioned = store.begin_close_ticket(interaction.guild.id, interaction.channel.id)
    if transitioned is None:
        return await interaction.response.send_message("Заявка уже закрывается или была закрыта другим действием.", ephemeral=True)
    ticket = transitioned
    support_role_ids = (
        [settings.get("curator_role_id") or CURATOR_ROLE_ID]
        if ticket.get("ticket_type") == "recruiter" else settings["support_role_ids"]
    )
    support_roles = " ".join(f"<@&{role_id}>" for role_id in support_role_ids if str(role_id).isdigit())
    staff = f" Поддержка: <@{ticket['claimed_by']}>." if ticket.get("claimed_by") else f" Поддержка: {support_roles}."
    embed = discord.Embed(
        title=f"Оцените поддержку — заявка {ticket_label(ticket['number'])}",
        description=f"Заявка закрывается по запросу {interaction.user.mention}.{staff}\n\nВыберите оценку ниже. После оценки ветка закроется через 5 секунд.",
        color=YELLOW,
    )
    embed.set_footer(text=f"Оценку может оставить автор заявки • {ticket_label(ticket['number'])}")
    await interaction.response.send_message(
        content=f"<@{ticket['user_id']}>", embed=embed, view=RatingView(), allowed_mentions=discord.AllowedMentions(users=True, roles=True)
    )
    if interaction.message is not None:
        await interaction.message.edit(view=TicketView(claimed=bool(ticket.get("claimed_by")), closing=True))
    await send_log(interaction.guild, settings, "запрошено закрытие", interaction.user, interaction.channel)


class RatingView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for rating in range(1, 6):
            button = discord.ui.Button(
                label="⭐" * rating,
                style=discord.ButtonStyle.secondary,
                custom_id=f"rival:rate:{rating}",
            )
            button.callback = self.make_callback(rating)
            self.add_item(button)

    def make_callback(self, rating: int):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.guild is None or interaction.channel is None:
                return await interaction.response.send_message("Оценка доступна только на сервере.", ephemeral=True)
            ticket = store.get_ticket(interaction.guild.id, interaction.channel.id)
            if ticket is None or ticket["status"] != "closing":
                return await interaction.response.send_message("Эта заявка сейчас не ожидает оценки.", ephemeral=True)
            if interaction.user.id != int(ticket["user_id"]):
                return await interaction.response.send_message("Оценить заявку может только её автор.", ephemeral=True)

            updated = store.finish_ticket_rating(
                interaction.guild.id,
                interaction.channel.id,
                rating,
                interaction.user.id,
                datetime.now(timezone.utc).isoformat(),
            )
            if updated is None:
                return await interaction.response.send_message("Оценка уже была выбрана другим нажатием.", ephemeral=True)
            assert isinstance(interaction.channel, discord.Thread)
            await interaction.response.edit_message(
                content=f"{interaction.user.mention} оставил оценку {'⭐' * rating}. Ветка закроется через 5 секунд.",
                embed=None,
                view=None,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            settings = store.get_settings(interaction.guild.id)
            await send_log(
                interaction.guild, settings, "заявка закрыта", interaction.user, interaction.channel,
                f"Оценка: {rating}/5",
            )
            await asyncio.sleep(5)
            try:
                await interaction.channel.edit(locked=True, archived=True, reason="Заявка закрыта после оценки")
            except discord.HTTPException:
                log.exception("Could not archive ticket thread %s", interaction.channel.id)

        return callback


class PromotionReviewView(discord.ui.View):
    def __init__(self, target_user_id: int) -> None:
        super().__init__(timeout=None)
        self.target_user_id = target_user_id
        stats = discord.ui.Button(
            label="Статистика", emoji="📊", style=discord.ButtonStyle.secondary,
            custom_id=f"rival:promotion:stats:{target_user_id}",
        )
        approve = discord.ui.Button(
            label="Принять", emoji="✅", style=discord.ButtonStyle.success,
            custom_id=f"rival:promotion:approve:{target_user_id}",
        )
        reject = discord.ui.Button(
            label="Отклонить", emoji="❌", style=discord.ButtonStyle.danger,
            custom_id=f"rival:promotion:reject:{target_user_id}",
        )
        stats.callback = self.show_statistics
        approve.callback = self.approve
        reject.callback = self.reject
        self.add_item(stats)
        self.add_item(approve)
        self.add_item(reject)

    async def _get_review(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Это действие доступно только администраторам сервера.", ephemeral=True)
            return None
        if not is_promotion_admin(interaction.user):
            await interaction.response.send_message("Недостаточно прав для управления повышениями.", ephemeral=True)
            return None
        record = store.get_trainee(interaction.guild.id, self.target_user_id)
        if record is None or record["review_status"] != "review_pending":
            await interaction.response.send_message("Эта проверка уже обработана или больше не активна.", ephemeral=True)
            return None
        return record

    async def show_statistics(self, interaction: discord.Interaction) -> None:
        record = await self._get_review(interaction)
        if record is None or interaction.guild is None:
            return
        trainee_since = datetime.fromisoformat(record["trainee_since"])
        now = discord.utils.utcnow()
        week_start = max(trainee_since, now - timedelta(days=7))
        activity = store.get_member_activity(interaction.guild.id, self.target_user_id, week_start.isoformat())
        embed = discord.Embed(
            title="Статистика стажёра",
            description=(
                f"Участник: <@{self.target_user_id}>\n"
                f"Период: {discord.utils.format_dt(week_start, style='d')} — {discord.utils.format_dt(now, style='d')}"
            ),
            color=RED,
        )
        embed.add_field(name="Сообщения за неделю", value=str(activity["messages"]), inline=True)
        embed.add_field(name="Активных дней в чате", value=str(activity["active_days"]), inline=True)
        embed.add_field(name="Последнее сообщение", value=(discord.utils.format_dt(datetime.fromisoformat(activity["last_message_at"]), style="R") if activity["last_message_at"] else "нет"), inline=True)
        embed.add_field(name="В голосовых за неделю", value=format_duration(activity["voice_seconds"]), inline=True)
        embed.add_field(name="Голосовых сессий", value=str(activity["voice_sessions"]), inline=True)
        embed.add_field(name="Средняя длительность сессии", value=format_duration(activity["average_session_seconds"]), inline=True)
        embed.add_field(name="Сейчас в голосовом", value="да" if activity["active_sessions"] else "нет", inline=True)
        embed.set_footer(text="Сохраняются только время и количество сообщений, не их текст. Учёт начался после включения бота.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def approve(self, interaction: discord.Interaction) -> None:
        record = await self._get_review(interaction)
        if record is None or interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return
        member = interaction.guild.get_member(self.target_user_id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(self.target_user_id)
            except discord.NotFound:
                store.update_trainee(interaction.guild.id, self.target_user_id, review_status="removed")
                return await interaction.response.send_message("Участник покинул сервер.", ephemeral=True)
        trainee_role = interaction.guild.get_role(TRAINEE_ROLE_ID)
        settings = store.get_settings(interaction.guild.id)
        main_role_id = settings.get("main_role_id") or MAIN_ROLE_ID
        main_role = interaction.guild.get_role(int(main_role_id)) if str(main_role_id).isdigit() else None
        bot_member = interaction.guild.me
        if trainee_role is None or main_role is None:
            return await interaction.response.send_message("Не найдены роли Trainee или Main. Проверь настройки ID ролей.", ephemeral=True)
        if bot_member is None or bot_member.top_role <= max(trainee_role, main_role):
            return await interaction.response.send_message("Подними роль бота выше ролей Trainee и Main в списке ролей сервера.", ephemeral=True)
        if trainee_role not in member.roles:
            store.update_trainee(interaction.guild.id, member.id, review_status="removed")
            return await interaction.response.send_message("У участника уже нет роли Trainee.", ephemeral=True)

        await interaction.response.defer()
        try:
            store.update_trainee(
                interaction.guild.id,
                member.id,
                review_status="approved",
                reviewed_by=str(interaction.user.id),
                reviewed_at=discord.utils.utcnow().isoformat(),
            )
            await member.add_roles(main_role, reason="Стажировка RIval одобрена")
            await member.remove_roles(trainee_role, reason="Выдана роль Main после стажировки RIval")
        except discord.HTTPException:
            store.update_trainee(interaction.guild.id, member.id, review_status="review_pending")
            log.exception("Could not promote member %s", member.id)
            return await interaction.followup.send("Не удалось изменить роли. Проверь права бота и порядок ролей.", ephemeral=True)

        approved = discord.Embed(
            title="Стажировка пройдена",
            description=f"Поздравляем, {member.mention} завершил стажировку и получил роль <@&{main_role_id}>.",
            color=RED,
            timestamp=discord.utils.utcnow(),
        )
        await interaction.edit_original_response(embed=approved, view=None)
        await interaction.channel.send(embed=approved, allowed_mentions=discord.AllowedMentions(users=True, roles=False))

    async def reject(self, interaction: discord.Interaction) -> None:
        record = await self._get_review(interaction)
        if record is None or interaction.guild is None:
            return
        await interaction.response.send_modal(PromotionDeclineModal(self.target_user_id))


class PromotionDeclineModal(discord.ui.Modal, title="Отклонить повышение"):
    def __init__(self, target_user_id: int) -> None:
        super().__init__(timeout=300)
        self.target_user_id = target_user_id
        self.reason = discord.ui.TextInput(
            label="Причина / что улучшить (приватно)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=800,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_promotion_admin(interaction.user):
            return await interaction.response.send_message("Недостаточно прав для управления повышениями.", ephemeral=True)
        record = store.get_trainee(interaction.guild.id, self.target_user_id)
        if record is None or record["review_status"] != "review_pending":
            return await interaction.response.send_message("Эта проверка уже обработана.", ephemeral=True)
        now = discord.utils.utcnow()
        store.update_trainee(
            interaction.guild.id,
            self.target_user_id,
            review_status="waiting",
            eligible_at=(now + timedelta(days=7)).isoformat(),
            review_message_id=None,
            reviewed_by=str(interaction.user.id),
            reviewed_at=now.isoformat(),
            review_note=self.reason.value.strip(),
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = store.get_settings(interaction.guild.id)
        review_channel_id = settings.get("rank_review_channel_id") or PROMOTION_REVIEW_CHANNEL_ID
        review_channel = interaction.guild.get_channel(int(review_channel_id))
        if isinstance(review_channel, discord.TextChannel) and record.get("review_message_id"):
            try:
                message = await review_channel.fetch_message(int(record["review_message_id"]))
                result = discord.Embed(
                    title="Повышение пока не одобрено",
                    description=f"Участник <@{self.target_user_id}> остаётся на стажировке. Повторная проверка доступна после следующей недели.",
                    color=RED,
                )
                await message.edit(embed=result, view=None)
            except discord.HTTPException:
                log.exception("Could not update declined trainee review message")
        await interaction.followup.send("Повышение отклонено. Причина сохранена приватно; стажировка начнётся заново.", ephemeral=True)


class AdminOnlyView(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not server_admin(interaction.user):
            await interaction.response.send_message("Эта панель доступна только администраторам сервера.", ephemeral=True)
            return False
        return True


class ChannelSettingsView(AdminOnlyView):
    """Discord-native channel pickers; ticket threads inherit the panel channel category."""

    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.category_id = int(settings["category_id"]) if str(settings.get("category_id", "")).isdigit() else None
        self.log_channel_id = int(settings["log_channel_id"]) if str(settings.get("log_channel_id", "")).isdigit() else None
        self.panel_channel_id = int(settings["panel_channel_id"]) if str(settings.get("panel_channel_id", "")).isdigit() else None
        self.category_select = discord.ui.ChannelSelect(
            placeholder="Резервная категория (не обязательна)",
            channel_types=[discord.ChannelType.category],
            default_values=self._default_channel(settings.get("category_id")),
            row=0,
        )
        self.log_select = discord.ui.ChannelSelect(
            placeholder="Канал логов действий в тикетах",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=self._default_channel(settings.get("log_channel_id")),
            row=1,
        )
        self.panel_select = discord.ui.ChannelSelect(
            placeholder="Канал для панели заявок",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=self._default_channel(settings.get("panel_channel_id")),
            row=2,
        )
        self.category_select.callback = self._category_changed
        self.log_select.callback = self._log_changed
        self.panel_select.callback = self._panel_changed
        self.add_item(self.category_select)
        self.add_item(self.log_select)
        self.add_item(self.panel_select)
        save = discord.ui.Button(label="Сохранить каналы", style=discord.ButtonStyle.success, row=3)
        save.callback = self.save
        self.add_item(save)

    @staticmethod
    def _default_channel(channel_id: Any) -> list[discord.SelectDefaultValue]:
        if channel_id and str(channel_id).isdigit():
            return [discord.SelectDefaultValue(id=int(channel_id), type=discord.SelectDefaultValueType.channel)]
        return []

    async def _category_changed(self, interaction: discord.Interaction) -> None:
        if self.category_select.values:
            self.category_id = self.category_select.values[0].id
        await interaction.response.defer(ephemeral=True)

    async def _log_changed(self, interaction: discord.Interaction) -> None:
        if self.log_select.values:
            self.log_channel_id = self.log_select.values[0].id
        await interaction.response.defer(ephemeral=True)

    async def _panel_changed(self, interaction: discord.Interaction) -> None:
        if self.panel_select.values:
            self.panel_channel_id = self.panel_select.values[0].id
        await interaction.response.defer(ephemeral=True)

    async def save(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        category = interaction.guild.get_channel(self.category_id) if self.category_id else None
        log_channel = interaction.guild.get_channel(self.log_channel_id) if self.log_channel_id else None
        panel_channel = interaction.guild.get_channel(self.panel_channel_id) if self.panel_channel_id else None
        if not isinstance(log_channel, discord.TextChannel):
            return await interaction.response.send_message("Выбери текстовый канал логов.", ephemeral=True)
        if not isinstance(panel_channel, discord.TextChannel):
            return await interaction.response.send_message("Выбери текстовый канал панели.", ephemeral=True)
        changes: dict[str, Any] = {
            "log_channel_id": log_channel.id,
            "panel_channel_id": panel_channel.id,
        }
        if isinstance(category, discord.CategoryChannel):
            changes["category_id"] = category.id
        settings = store.update_settings(interaction.guild.id, **changes)
        await interaction.response.edit_message(
            content="Настройки каналов сохранены.",
            embed=setup_embed(settings),
            view=SetupView(),
        )


class RoleSettingsView(AdminOnlyView):
    """Discord-native role pickers for applicants and support staff."""

    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.applicant_role_id = int(settings["applicant_role_id"]) if str(settings.get("applicant_role_id", "")).isdigit() else None
        self.support_role_ids = {
            int(role_id) for role_id in settings.get("support_role_ids", []) if str(role_id).isdigit()
        }
        self.recruiter_ping_role_id = (
            int(settings.get("recruiter_ping_role_id") or RECRUITER_PING_ROLE_ID)
            if str(settings.get("recruiter_ping_role_id") or RECRUITER_PING_ROLE_ID).isdigit() else None
        )
        self.applicant_select = discord.ui.RoleSelect(
            placeholder="Роль, которой можно подать заявку",
            default_values=self._default_roles([settings.get("applicant_role_id")]),
            row=0,
        )
        self.support_select = discord.ui.RoleSelect(
            placeholder="Роли поддержки (можно выбрать несколько)",
            min_values=1,
            max_values=25,
            default_values=self._default_roles(settings.get("support_role_ids", [])),
            row=1,
        )
        self.recruiter_ping_select = discord.ui.RoleSelect(
            placeholder="Роль рекрутеров для упоминания при новой заявке",
            default_values=self._default_roles([self.recruiter_ping_role_id]),
            row=2,
        )
        self.applicant_select.callback = self._applicant_changed
        self.support_select.callback = self._support_changed
        self.recruiter_ping_select.callback = self._recruiter_ping_changed
        self.add_item(self.applicant_select)
        self.add_item(self.support_select)
        self.add_item(self.recruiter_ping_select)
        save = discord.ui.Button(label="Сохранить роли", style=discord.ButtonStyle.success, row=3)
        save.callback = self.save
        self.add_item(save)

    @staticmethod
    def _default_roles(role_ids: list[Any]) -> list[discord.SelectDefaultValue]:
        return [
            discord.SelectDefaultValue(id=int(role_id), type=discord.SelectDefaultValueType.role)
            for role_id in role_ids
            if role_id and str(role_id).isdigit()
        ]

    async def _applicant_changed(self, interaction: discord.Interaction) -> None:
        if self.applicant_select.values:
            self.applicant_role_id = self.applicant_select.values[0].id
        await interaction.response.defer(ephemeral=True)

    async def _support_changed(self, interaction: discord.Interaction) -> None:
        self.support_role_ids = {role.id for role in self.support_select.values}
        await interaction.response.defer(ephemeral=True)

    async def _recruiter_ping_changed(self, interaction: discord.Interaction) -> None:
        self.recruiter_ping_role_id = self.recruiter_ping_select.values[0].id if self.recruiter_ping_select.values else None
        await interaction.response.defer(ephemeral=True)

    async def save(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        applicant = interaction.guild.get_role(self.applicant_role_id) if self.applicant_role_id else None
        support_roles = [interaction.guild.get_role(role_id) for role_id in self.support_role_ids]
        support_roles = [role for role in support_roles if role is not None]
        recruiter_ping_role = interaction.guild.get_role(self.recruiter_ping_role_id) if self.recruiter_ping_role_id else None
        if not isinstance(applicant, discord.Role):
            return await interaction.response.send_message("Выбери роль для подачи заявки.", ephemeral=True)
        if not support_roles or not all(isinstance(role, discord.Role) for role in support_roles):
            return await interaction.response.send_message("Выбери хотя бы одну роль поддержки.", ephemeral=True)
        if not isinstance(recruiter_ping_role, discord.Role):
            return await interaction.response.send_message("Выбери роль рекрутеров для уведомления о заявке.", ephemeral=True)
        settings = store.update_settings(
            interaction.guild.id,
            applicant_role_id=applicant.id,
            support_role_ids=[role.id for role in support_roles],
            recruiter_ping_role_id=recruiter_ping_role.id,
        )
        await interaction.response.edit_message(
            content="Настройки ролей сохранены.",
            embed=setup_embed(settings),
            view=SetupView(),
        )


class SetupView(AdminOnlyView):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        toggle = discord.ui.Button(
            label="Открыть / закрыть набор",
            emoji="🔄",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        toggle.callback = self.toggle_recruitment
        self.add_item(toggle)

    async def toggle_recruitment(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Действие доступно только на сервере.", ephemeral=True, delete_after=60)
        if not server_admin(interaction.user):
            return await interaction.response.send_message("Для управления набором нужны права администратора сервера.", ephemeral=True, delete_after=60)
        settings = store.get_settings(interaction.guild.id)
        settings = store.update_settings(
            interaction.guild.id,
            applications_open=not settings.get("applications_open", True),
        )
        panel_updated = await refresh_published_panel(interaction.guild, settings)
        status = "Набор открыт." if settings["applications_open"] else "Набор закрыт. Новые заявки создавать нельзя."
        if panel_updated:
            status += " Панель в сервере тоже обновлена."
        await interaction.response.edit_message(content=status, embed=setup_embed(settings), view=SetupView())

    @discord.ui.button(label="Панель", style=discord.ButtonStyle.primary, row=0)
    async def panel_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        settings = store.get_settings(interaction.guild.id) if interaction.guild else {}
        await interaction.response.send_modal(PanelSettingsModal(settings))

    @discord.ui.button(label="Каналы и роли", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def channel_role_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        await interaction.response.send_message(
            "Настройка всех каналов и ролей разделена по понятным категориям:",
            view=ChannelRoleHubView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.success, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        await interaction.response.edit_message(embed=setup_embed(store.get_settings(interaction.guild.id)), view=self)

    @discord.ui.button(label="Опубликовать панель", style=discord.ButtonStyle.success, row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        channel = interaction.guild.get_channel(int(settings["panel_channel_id"])) if settings.get("panel_channel_id") else interaction.channel
        await publish_panel(interaction, channel)

    @discord.ui.button(label="Рекрутинг", style=discord.ButtonStyle.primary, row=2)
    async def recruiter_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        await interaction.response.send_message(
            "Настройки подачи заявок на роль рекрутера:", view=RecruitSetupView(), ephemeral=True
        )


class ChannelRoleHubView(AdminOnlyView):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Основные каналы", style=discord.ButtonStyle.secondary, row=0)
    async def main_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        for key, channel_type in (("category_id", discord.CategoryChannel), ("log_channel_id", discord.TextChannel), ("panel_channel_id", discord.TextChannel)):
            value = settings.get(key)
            selected = interaction.guild.get_channel(int(value)) if value and str(value).isdigit() else None
            if not isinstance(selected, channel_type):
                settings[key] = None
        await interaction.response.send_message(
            "Выбери канал логов и канал панели. Ветки будут создаваться под каналом панели.",
            view=ChannelSettingsView(settings), ephemeral=True,
        )

    @discord.ui.button(label="Основные роли", style=discord.ButtonStyle.secondary, row=0)
    async def main_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        await interaction.response.send_message(
            "Выбери роль для подачи заявки, роли поддержки и роль рекрутеров для уведомлений.",
            view=RoleSettingsView(store.get_settings(interaction.guild.id)), ephemeral=True,
        )

    @discord.ui.button(label="Каналы рекрутинга и повышений", style=discord.ButtonStyle.secondary, row=1)
    async def recruit_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        for key in ("recruit_panel_channel_id", "rank_review_channel_id"):
            value = settings.get(key)
            if value and not isinstance(interaction.guild.get_channel(int(value)), discord.TextChannel):
                settings[key] = None
        await interaction.response.send_message(
            "Выбери канал панели рекрутера и канал заявок на выдачу ролей.",
            view=RecruitChannelSettingsView(settings), ephemeral=True,
        )

    @discord.ui.button(label="Роли рекрутинга и повышений", style=discord.ButtonStyle.secondary, row=1)
    async def recruit_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        for key in ("main_role_id", "recruiter_role_id", "curator_role_id"):
            value = settings.get(key)
            if value and interaction.guild.get_role(int(value)) is None:
                settings[key] = None
        await interaction.response.send_message(
            "Выбери роли Main, рекрутера и куратора.",
            view=RecruitRoleSettingsView(settings), ephemeral=True,
        )


class RecruitSetupView(AdminOnlyView):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Каналы рекрутинга", style=discord.ButtonStyle.secondary, row=0)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        for key in ("recruit_panel_channel_id", "rank_review_channel_id"):
            value = settings.get(key)
            if value and not isinstance(interaction.guild.get_channel(int(value)), discord.TextChannel):
                settings[key] = None
        await interaction.response.send_message(
            "Выбери канал панели рекрутеров и отдельный канал, куда отправлять запросы на выдачу ролей:",
            view=RecruitChannelSettingsView(settings), ephemeral=True,
        )

    @discord.ui.button(label="Роли рекрутинга", style=discord.ButtonStyle.secondary, row=0)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        for key in ("main_role_id", "recruiter_role_id", "curator_role_id"):
            value = settings.get(key)
            if value and interaction.guild.get_role(int(value)) is None:
                settings[key] = None
        await interaction.response.send_message(
            "Выбери роль Main для допуска к заявке, выдаваемую роль рекрутера и роль куратора:",
            view=RecruitRoleSettingsView(settings), ephemeral=True,
        )

    @discord.ui.button(label="Опубликовать панель рекрутера", style=discord.ButtonStyle.success, row=1)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        channel_id = settings.get("recruit_panel_channel_id")
        channel = interaction.guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
        await publish_recruit_panel(interaction, channel)

    @discord.ui.button(label="Открыть / закрыть набор рекрутеров", style=discord.ButtonStyle.danger, row=1)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Действие доступно только на сервере.", ephemeral=True)
        if not (interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator):
            return await interaction.response.send_message("Нужно право управления сервером.", ephemeral=True)
        settings = store.get_settings(interaction.guild.id)
        settings = store.update_settings(
            interaction.guild.id,
            recruit_applications_open=not settings.get("recruit_applications_open", True),
        )
        updated = await refresh_published_recruit_panel(interaction.guild, settings)
        status = "Набор рекрутеров открыт." if settings["recruit_applications_open"] else "Набор рекрутеров закрыт."
        if updated:
            status += " Панель обновлена."
        await interaction.response.edit_message(content=status, embed=setup_embed(settings), view=SetupView())


class RecruitChannelSettingsView(AdminOnlyView):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.panel_channel_id = int(settings["recruit_panel_channel_id"]) if str(settings.get("recruit_panel_channel_id", "")).isdigit() else None
        self.review_channel_id = int(settings["rank_review_channel_id"]) if str(settings.get("rank_review_channel_id", "")).isdigit() else None
        self.panel_select = discord.ui.ChannelSelect(
            placeholder="Канал панели заявок на рекрутера",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=ChannelSettingsView._default_channel(self.panel_channel_id), row=0,
        )
        self.review_select = discord.ui.ChannelSelect(
            placeholder="Канал запросов на выдачу рангов и ролей",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=ChannelSettingsView._default_channel(self.review_channel_id), row=1,
        )
        self.panel_select.callback = self._panel_changed
        self.review_select.callback = self._review_changed
        self.add_item(self.panel_select)
        self.add_item(self.review_select)
        save = discord.ui.Button(label="Сохранить каналы рекрутинга", style=discord.ButtonStyle.success, row=2)
        save.callback = self.save
        self.add_item(save)

    async def _panel_changed(self, interaction: discord.Interaction) -> None:
        self.panel_channel_id = self.panel_select.values[0].id if self.panel_select.values else None
        await interaction.response.defer(ephemeral=True)

    async def _review_changed(self, interaction: discord.Interaction) -> None:
        self.review_channel_id = self.review_select.values[0].id if self.review_select.values else None
        await interaction.response.defer(ephemeral=True)

    async def save(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        panel = interaction.guild.get_channel(self.panel_channel_id) if self.panel_channel_id else None
        review = interaction.guild.get_channel(self.review_channel_id) if self.review_channel_id else None
        if not isinstance(panel, discord.TextChannel) or not isinstance(review, discord.TextChannel):
            return await interaction.response.send_message("Выбери оба текстовых канала.", ephemeral=True)
        settings = store.update_settings(
            interaction.guild.id,
            recruit_panel_channel_id=panel.id,
            rank_review_channel_id=review.id,
        )
        await refresh_published_recruit_panel(interaction.guild, settings)
        await interaction.response.edit_message(
            content="Каналы рекрутинга и выдачи ролей сохранены.", embed=setup_embed(settings), view=SetupView()
        )


class RecruitRoleSettingsView(AdminOnlyView):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.role_ids: dict[str, int | None] = {}
        for index, (key, placeholder) in enumerate((
            ("main_role_id", "Роль Main — обязательное условие подачи"),
            ("recruiter_role_id", "Роль, выдаваемая принятому рекрутеру"),
            ("curator_role_id", "Роль куратора заявок"),
        )):
            value = settings.get(key)
            self.role_ids[key] = int(value) if value and str(value).isdigit() else None
            select = discord.ui.RoleSelect(
                placeholder=placeholder,
                min_values=0 if key == "recruiter_role_id" else 1,
                max_values=1,
                default_values=RoleSettingsView._default_roles([value]),
                row=index,
            )
            select.callback = self._role_changed
            select.custom_id = f"rival:recruit-settings:{key}"
            self.add_item(select)
        save = discord.ui.Button(label="Сохранить роли рекрутинга", style=discord.ButtonStyle.success, row=3)
        save.callback = self.save
        self.add_item(save)

    async def _role_changed(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get("custom_id", "") if interaction.data else ""
        key = custom_id.rsplit(":", 1)[-1]
        if key in self.role_ids:
            values = next((child.values for child in self.children if isinstance(child, discord.ui.RoleSelect) and child.custom_id == custom_id), [])
            self.role_ids[key] = values[0].id if values else None
        await interaction.response.defer(ephemeral=True)

    async def save(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        resolved = {
            key: interaction.guild.get_role(role_id) if role_id else None
            for key, role_id in self.role_ids.items()
        }
        if not isinstance(resolved["main_role_id"], discord.Role):
            return await interaction.response.send_message("Выбери роль Main.", ephemeral=True)
        if not isinstance(resolved["curator_role_id"], discord.Role):
            return await interaction.response.send_message("Выбери роль куратора.", ephemeral=True)
        changes: dict[str, Any] = {
            "main_role_id": resolved["main_role_id"].id,
            "curator_role_id": resolved["curator_role_id"].id,
            "recruiter_role_id": resolved["recruiter_role_id"].id if resolved["recruiter_role_id"] else None,
        }
        settings = store.update_settings(interaction.guild.id, **changes)
        await refresh_published_recruit_panel(interaction.guild, settings)
        await interaction.response.edit_message(
            content="Роли рекрутинга сохранены.", embed=setup_embed(settings), view=SetupView()
        )


async def refresh_published_panel(guild: discord.Guild, settings: dict[str, Any]) -> bool:
    channel_id = settings.get("panel_channel_id")
    message_id = settings.get("panel_message_id")
    if not channel_id or not message_id:
        return False
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            embed=make_panel_embed(settings),
            view=PanelView(),
            attachments=message.attachments,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.exception("Could not update the published panel in guild %s", guild.id)
        return False


class PanelSettingsModal(discord.ui.Modal, title="Настройка панели"):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.panel_title = discord.ui.TextInput(
            label="Название панели", default=settings.get("panel_title", "Заявка в клан RIval"), max_length=256
        )
        self.panel_description = discord.ui.TextInput(
            label="Описание панели", style=discord.TextStyle.paragraph,
            default=settings.get("panel_description", "Подай заявку в клан. Нажми кнопку ниже и заполни форму."), max_length=1000,
        )
        self.add_item(self.panel_title)
        self.add_item(self.panel_description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        settings = store.update_settings(
            interaction.guild.id,
            panel_title=self.panel_title.value.strip(),
            panel_description=self.panel_description.value.strip(),
        )
        updated_panel = await refresh_published_panel(interaction.guild, settings)
        status = "Настройки сохранены и опубликованная панель обновлена." if updated_panel else "Настройки сохранены. Опубликуй панель командой `/panel`."
        await interaction.response.send_message(status, embed=setup_embed(settings), view=SetupView(), ephemeral=True)


class ChannelSettingsModal(discord.ui.Modal, title="Настройка каналов"):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.category_id = discord.ui.TextInput(
            label="ID категории заявок", default=str(settings.get("category_id") or "1558484503578673193"), max_length=25
        )
        self.log_channel_id = discord.ui.TextInput(
            label="ID канала логов", default=str(settings.get("log_channel_id") or "1558844108171382945"), max_length=25
        )
        self.add_item(self.category_id)
        self.add_item(self.log_channel_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        category_id = self.category_id.value.strip()
        log_channel_id = self.log_channel_id.value.strip()
        if not category_id.isdigit() or not log_channel_id.isdigit():
            return await interaction.response.send_message("ID каналов должны состоять из цифр.", ephemeral=True)
        category = interaction.guild.get_channel(int(category_id))
        log_channel = interaction.guild.get_channel(int(log_channel_id))
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("Категория с таким ID не найдена.", ephemeral=True)
        if not isinstance(log_channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("Текстовый канал логов с таким ID не найден.", ephemeral=True)
        settings = store.update_settings(interaction.guild.id, category_id=category_id, log_channel_id=log_channel_id)
        await interaction.response.send_message("Настройки каналов сохранены. Категория и права родительского канала обновятся при следующей заявке.", embed=setup_embed(settings), view=SetupView(), ephemeral=True)


class RoleSettingsModal(discord.ui.Modal, title="Настройка ролей"):
    def __init__(self, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        self.applicant_role_id = discord.ui.TextInput(
            label="ID роли, которой можно подать заявку",
            default=str(settings.get("applicant_role_id") or "1503725660231700620"), max_length=25,
        )
        self.support_role_ids = discord.ui.TextInput(
            label="ID ролей поддержки через запятую",
            default=", ".join(settings.get("support_role_ids", [])) or "1558523889687199905", max_length=500,
        )
        self.add_item(self.applicant_role_id)
        self.add_item(self.support_role_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
        applicant = self.applicant_role_id.value.strip()
        role_ids = list(dict.fromkeys(part.strip() for part in re.split(r"[,\s]+", self.support_role_ids.value) if part.strip()))
        if not applicant.isdigit() or not role_ids or not all(role_id.isdigit() for role_id in role_ids):
            return await interaction.response.send_message("Укажи числовые ID роли заявки и хотя бы одной роли поддержки.", ephemeral=True)
        missing = [role_id for role_id in [applicant, *role_ids] if interaction.guild.get_role(int(role_id)) is None]
        if missing:
            return await interaction.response.send_message(f"На сервере не найдены роли: {', '.join(missing)}.", ephemeral=True)
        settings = store.update_settings(interaction.guild.id, applicant_role_id=applicant, support_role_ids=role_ids)
        await interaction.response.send_message("Настройки ролей сохранены. Права родительского канала обновятся при следующей заявке.", embed=setup_embed(settings), view=SetupView(), ephemeral=True)


async def publish_panel(interaction: discord.Interaction, channel: discord.abc.GuildChannel | None) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("Выбери текстовый канал для панели.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    bot_member = interaction.guild.me
    if bot_member is None or not channel.permissions_for(bot_member).send_messages:
        return await interaction.response.send_message("У бота нет права писать в выбранный канал.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    image_path = ROOT / "img" / "icon.jpg"
    file = discord.File(image_path, filename="icon.jpg") if image_path.is_file() else None
    try:
        message = await channel.send(
            embed=make_panel_embed(settings),
            view=PanelView(),
            file=file,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as error:
        return await interaction.followup.send(f"Не удалось опубликовать панель: {error}", ephemeral=True)
    store.update_settings(interaction.guild.id, panel_channel_id=channel.id, panel_message_id=message.id)
    await interaction.edit_original_response(content=f"Панель заявок опубликована в {channel.mention}.")


async def publish_recruit_panel(
    interaction: discord.Interaction, channel: discord.abc.GuildChannel | None
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("Выбери текстовый канал для панели рекрутеров.", ephemeral=True)
    bot_member = interaction.guild.me
    permissions = channel.permissions_for(bot_member) if bot_member is not None else None
    if permissions is None or not permissions.send_messages or not permissions.embed_links:
        return await interaction.response.send_message("Боту нужны права отправки сообщений и embed в выбранном канале.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    settings = store.get_settings(interaction.guild.id)
    try:
        message = await channel.send(
            embed=make_recruit_panel_embed(settings),
            view=RecruitPanelView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as error:
        return await interaction.followup.send(f"Не удалось опубликовать панель рекрутеров: {error}", ephemeral=True)
    store.update_settings(
        interaction.guild.id,
        recruit_panel_channel_id=channel.id,
        recruit_panel_message_id=message.id,
    )
    await interaction.edit_original_response(content=f"Панель рекрутеров опубликована в {channel.mention}.")


async def refresh_published_recruit_panel(guild: discord.Guild, settings: dict[str, Any]) -> bool:
    channel_id = settings.get("recruit_panel_channel_id")
    message_id = settings.get("recruit_panel_message_id")
    if not channel_id or not message_id:
        return False
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            embed=make_recruit_panel_embed(settings),
            view=RecruitPanelView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        log.exception("Could not update the recruiter panel in guild %s", guild.id)
        return False


class RivalBot(commands.Bot):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._promotion_views_registered: set[int] = set()
        self._voice_recovered_guilds: set[int] = set()

    async def setup_hook(self) -> None:
        self.add_view(PanelView())
        self.add_view(RecruitPanelView())
        self.add_view(TicketView())
        self.add_view(RatingView())

        guild_id = os.getenv("GUILD_ID", "").strip()
        if guild_id.isdigit():
            target = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=target)
            synced = await self.tree.sync(guild=target)
            log.info("Synced %s slash commands to guild %s", len(synced), guild_id)
            # /profile is intentionally available in DMs too. Discord only
            # exposes DM-capable application commands from the global tree;
            # keep the guild sync above so server commands still update fast.
            try:
                global_synced = await self.tree.sync()
                log.info("Synced %s global slash commands (including /profile)", len(global_synced))
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not sync global slash commands; /profile in DMs may not appear yet")
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global slash commands", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Bot online as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="в клане Rival Clan"),
        )
        now = discord.utils.utcnow()
        for guild in self.guilds:
            store.ensure_guild(guild.id)
            if guild.id not in self._voice_recovered_guilds:
                members_fetched = True
                try:
                    await guild.chunk(cache=True)
                except (discord.Forbidden, discord.HTTPException):
                    members_fetched = False
                    log.warning("Could not fetch the complete member list for guild %s", guild.id)
                heartbeat = store.get_heartbeat(guild.id)
                recovery_time = heartbeat or now.isoformat()
                store.recover_voice_sessions(guild.id, recovery_time)
                for member in guild.members:
                    if member.voice and member.voice.channel:
                        store.start_voice_session(guild.id, member.id, member.voice.channel.id, now.isoformat())
                store.set_heartbeat(guild.id, now.isoformat())
                for member in guild.members:
                    if member_has_trainee(member) and store.get_trainee(guild.id, member.id) is None:
                        # Discord does not expose historical role assignment timestamps.
                        # This records the first date the bot observed the role; admins can backfill it.
                        store.register_trainee(guild.id, member.id, now.isoformat())
                # Establish a no-notification baseline so a restart never
                # looks like a fresh join or removal from the clan.
                if members_fetched:
                    store.sync_member_role_states(
                        guild.id,
                        CLAN_ROLE_ID,
                        {member.id for member in guild.members if has_clan_role(member)},
                    )
                self._voice_recovered_guilds.add(guild.id)
            settings = store.get_settings(guild.id)
            await self._repair_deleted_ticket_records(guild, settings)
            await refresh_published_panel(guild, settings)
            await refresh_published_recruit_panel(guild, settings)
            for ticket in store.list_tickets(guild.id, open_only=True):
                thread = guild.get_thread(int(ticket["thread_id"]))
                if thread is None:
                    try:
                        fetched = await guild.fetch_channel(int(ticket["thread_id"]))
                        thread = fetched if isinstance(fetched, discord.Thread) else None
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        thread = None
                if thread is not None:
                    await sync_ticket_thread_access(
                        guild, thread, settings, ticket_type=ticket.get("ticket_type", "clan")
                    )
            for record in store.list_trainees(guild.id, status="review_pending"):
                message_id = record.get("review_message_id")
                if message_id and int(message_id) not in self._promotion_views_registered:
                    self.add_view(PromotionReviewView(int(record["user_id"])), message_id=int(message_id))
                    self._promotion_views_registered.add(int(message_id))
        if not self.promotion_monitor.is_running():
            self.promotion_monitor.start()

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        had_clan = has_clan_role(before)
        has_clan = has_clan_role(after)
        if had_clan != has_clan and store.record_member_role_transition(
            after.guild.id, after.id, CLAN_ROLE_ID, had_clan, has_clan
        ):
            if has_clan:
                await notify_once(
                    after.guild.id,
                    after,
                    f"clan-joined:{discord.utils.utcnow().isoformat()}",
                    "Добро пожаловать в клан RIval!",
                    "Тебя приняли в клан. Рады видеть тебя в команде! Канал заявок и важная информация доступны здесь:",
                    include_application=True,
                )
            else:
                await notify_once(
                    after.guild.id,
                    after,
                    f"clan-role-removed:{discord.utils.utcnow().isoformat()}",
                    "Изменение статуса в клане RIval",
                    "С тебя сняли роль Clan. Если это произошло по ошибке, свяжись с администрацией сервера.",
                )
        had_trainee = any(role.id == TRAINEE_ROLE_ID for role in before.roles)
        has_trainee = member_has_trainee(after)
        now = discord.utils.utcnow().isoformat()
        if has_trainee and not had_trainee:
            store.set_trainee_start(after.guild.id, after.id, now)
            log.info("Started trainee tracking for %s in guild %s", after.id, after.guild.id)
        elif had_trainee and not has_trainee:
            record = store.get_trainee(after.guild.id, after.id)
            if record and record["review_status"] in {"waiting", "review_pending"}:
                store.update_trainee(after.guild.id, after.id, review_status="removed")

    async def on_member_join(self, member: discord.Member) -> None:
        if has_clan_role(member):
            store.sync_member_role_presence(member.guild.id, member.id, CLAN_ROLE_ID, True)
        if member_has_trainee(member):
            store.set_trainee_start(member.guild.id, member.id, discord.utils.utcnow().isoformat())

    async def on_member_remove(self, member: discord.Member) -> None:
        # Kicking a member does not produce on_member_update; handle the
        # cached roles from the member-removal event as the same role loss.
        if has_clan_role(member) and store.record_member_role_transition(
            member.guild.id, member.id, CLAN_ROLE_ID, True, False
        ):
            await notify_once(
                member.guild.id,
                member,
                f"clan-member-removed:{discord.utils.utcnow().isoformat()}",
                "Изменение статуса в клане RIval",
                "Ты больше не состоишь на сервере RIval с ролью Clan. Если это произошло по ошибке, свяжись с администрацией.",
            )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        store.ensure_guild(guild.id)

    async def on_thread_delete(self, thread: discord.Thread) -> None:
        """Keep SQLite consistent when a moderator deletes a ticket thread."""
        await self._handle_deleted_thread(thread.guild, thread.id, thread.name)

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        """Raw event fallback for deleted threads that were not cached by discord.py."""
        guild = self.get_guild(payload.guild_id)
        if guild is None:
            return
        cached = guild.get_thread(payload.thread_id)
        thread_name = cached.name if cached is not None else f"thread-{payload.thread_id}"
        await self._handle_deleted_thread(guild, payload.thread_id, thread_name)

    async def _handle_deleted_thread(self, guild: discord.Guild, thread_id: int, thread_name: str) -> None:
        ticket = store.get_ticket(guild.id, thread_id)
        if ticket is None:
            return
        settings = store.get_settings(guild.id)
        deleted = store.delete_ticket(guild.id, thread_id)
        if not deleted:
            return
        # The thread no longer exists, so send the audit entry without a thread object.
        channel_id = settings.get("log_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id and str(channel_id).isdigit() else None
        if channel is None and channel_id and str(channel_id).isdigit():
            try:
                fetched = await guild.fetch_channel(int(channel_id))
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="Действие в заявке: ветка удалена",
                color=RED,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Кто сделал", value="Администратор Discord / действие модерации", inline=False)
            embed.add_field(name="Ветка", value=f"`{thread_name}` (`{thread_id}`)", inline=False)
            embed.add_field(name="Заявка", value=ticket_label(ticket["number"]), inline=True)
            embed.add_field(name="Автор", value=f"<@{ticket['user_id']}> (`{ticket['user_id']}`)", inline=True)
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not log deleted ticket thread %s", thread_id)

    async def _repair_deleted_ticket_records(self, guild: discord.Guild, settings: dict[str, Any]) -> None:
        """Repair records for threads deleted while the process was offline."""
        for ticket in store.list_tickets(guild.id, open_only=False, limit=1000):
            try:
                channel = await guild.fetch_channel(int(ticket["thread_id"]))
            except discord.NotFound:
                if store.delete_ticket(guild.id, int(ticket["thread_id"])):
                    log.info("Removed stale deleted ticket %s from SQLite", ticket["thread_id"])
                continue
            except (discord.Forbidden, discord.HTTPException):
                continue
            if isinstance(channel, discord.Thread):
                continue
            if store.delete_ticket(guild.id, int(ticket["thread_id"])):
                log.info("Removed stale non-thread ticket %s from SQLite", ticket["thread_id"])

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None and not message.author.bot:
            store.record_message(message.guild.id, message.author.id, discord.utils.utcnow().isoformat())

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if before.channel == after.channel:
            return
        now = discord.utils.utcnow().isoformat()
        if before.channel is not None:
            store.end_voice_session(member.guild.id, member.id, now)
        if after.channel is not None:
            store.start_voice_session(member.guild.id, member.id, after.channel.id, now)

    @tasks.loop(minutes=1)
    async def promotion_monitor(self) -> None:
        now = discord.utils.utcnow()
        for guild in self.guilds:
            store.set_heartbeat(guild.id, now.isoformat())
            settings = store.get_settings(guild.id)
            review_channel_id = settings.get("rank_review_channel_id") or PROMOTION_REVIEW_CHANNEL_ID
            review_channel = guild.get_channel(int(review_channel_id))
            if not isinstance(review_channel, discord.TextChannel):
                continue
            for record in store.list_trainees(guild.id):
                if record["review_status"] not in {"waiting", "review_pending"}:
                    continue
                try:
                    eligible_at = datetime.fromisoformat(record["eligible_at"])
                except (TypeError, ValueError):
                    log.warning("Invalid eligibility date for trainee %s", record["user_id"])
                    continue
                if eligible_at > now:
                    continue
                member = guild.get_member(int(record["user_id"]))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(record["user_id"]))
                    except discord.NotFound:
                        store.update_trainee(guild.id, int(record["user_id"]), review_status="removed")
                        continue
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                if not member_has_trainee(member):
                    store.update_trainee(guild.id, member.id, review_status="removed")
                    continue
                existing_message = None
                message_id = record.get("review_message_id")
                if message_id:
                    try:
                        existing_message = await review_channel.fetch_message(int(message_id))
                    except discord.NotFound:
                        existing_message = None
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                if existing_message is not None:
                    continue
                view = PromotionReviewView(member.id)
                admin_roles = [guild.get_role(role_id) for role_id in PROMOTION_ADMIN_ROLE_IDS]
                allowed_roles = [role for role in admin_roles if role is not None]
                role_mentions = " ".join(role.mention for role in allowed_roles)
                try:
                    message = await review_channel.send(
                        content=f"{role_mentions} • проверка стажёра {member.mention}",
                        embed=promotion_review_embed(
                            member, record["trainee_since"], int(settings.get("main_role_id") or MAIN_ROLE_ID)
                        ),
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=[member], roles=allowed_roles),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not post promotion review for %s", member.id)
                    continue
                store.update_trainee(guild.id, member.id, review_status="review_pending", review_message_id=str(message.id))
                self._promotion_views_registered.add(message.id)

    @promotion_monitor.before_loop
    async def before_promotion_monitor(self) -> None:
        await self.wait_until_ready()


intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
intents.voice_states = True
bot = RivalBot(command_prefix="!", intents=intents)


@bot.tree.command(name="setup", description="Открыть меню настройки системы заявок")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def setup_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    if not server_admin(interaction.user):
        return await interaction.response.send_message("Панель настройки доступна только администраторам сервера.", ephemeral=True)
    settings = store.ensure_guild(interaction.guild.id)
    await interaction.response.send_message(embed=setup_embed(settings), view=SetupView(), ephemeral=True)


@bot.tree.command(name="panel", description="Опубликовать панель подачи заявки")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="Канал, куда отправить панель (по умолчанию текущий)")
async def panel_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    if not isinstance(interaction.user, discord.Member) or not server_admin(interaction.user):
        return await interaction.response.send_message("Публиковать панель могут только администраторы сервера.", ephemeral=True)
    await publish_panel(interaction, channel or interaction.channel)


@bot.tree.command(name="ticket", description="Показать сведения о заявке в текущей ветке")
@app_commands.guild_only()
async def ticket_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None:
        return await interaction.response.send_message("Команда доступна на сервере.", ephemeral=True)
    ticket = store.get_ticket(interaction.guild.id, interaction.channel.id)
    if ticket is None:
        return await interaction.response.send_message("Это не ветка заявки.", ephemeral=True)
    if not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Не удалось проверить участника.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    if interaction.user.id != int(ticket["user_id"]) and not support_member(
        interaction.user, settings, ticket.get("ticket_type", "clan")
    ):
        return await interaction.response.send_message("Недостаточно прав для просмотра заявки.", ephemeral=True)
    ticket["status_text"] = f"взял <@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "ожидает рассмотрения"
    await interaction.response.send_message(embed=make_ticket_embed(ticket, interaction.guild), ephemeral=True)


@bot.tree.command(name="tickets", description="Показать список открытых заявок")
@app_commands.guild_only()
async def tickets_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    tickets = store.list_tickets(interaction.guild.id, open_only=True, limit=100)
    visible_tickets = [
        ticket for ticket in tickets
        if support_member(interaction.user, settings, ticket.get("ticket_type", "clan"))
    ]
    if not visible_tickets and not support_member(interaction.user, settings):
        return await interaction.response.send_message("Список заявок доступен только поддержке.", ephemeral=True)
    embed = discord.Embed(title="Открытые заявки RIval", color=BLUE)
    if visible_tickets:
        embed.description = "\n".join(
            f"{'[Рекрутер] ' if ticket.get('ticket_type') == 'recruiter' else ''}"
            f"{ticket_label(ticket['number'])} • <#{ticket['thread_id']}> • <@{ticket['user_id']}> • "
            f"{'взята <@' + ticket['claimed_by'] + '>' if ticket.get('claimed_by') else 'ожидает модератора'}"
            for ticket in visible_tickets[:20]
        )
    else:
        embed.description = "Открытых заявок пока нет."
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="Показать статистику участника сервера")
@app_commands.guild_only()
@app_commands.describe(member="Участник сервера")
async def stats_command(interaction: discord.Interaction, member: discord.Member) -> None:
    """Show a private seven-day activity summary for a selected member."""
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    can_view_others = (
        support_member(interaction.user, settings)
        or is_promotion_admin(interaction.user)
        or interaction.user.id == member.id
    )
    if not can_view_others:
        return await interaction.response.send_message(
            "Статистику других участников могут смотреть только поддержка и администраторы.",
            ephemeral=True,
        )

    now = discord.utils.utcnow()
    week_start = now - timedelta(days=7)
    if member.joined_at is not None:
        week_start = max(week_start, member.joined_at)
    activity = store.get_member_activity(interaction.guild.id, member.id, week_start.isoformat())
    last_message = activity["last_message_at"]
    try:
        last_message_text = discord.utils.format_dt(datetime.fromisoformat(last_message), style="R") if last_message else "нет данных"
    except (TypeError, ValueError):
        last_message_text = "нет данных"
    voice_channel = member.voice.channel.mention if member.voice and member.voice.channel else "не находится в войсе"
    embed = discord.Embed(
        title=f"Статистика {member.display_name}",
        description=(
            f"Участник: {member.mention} (`{member.id}`)\n"
            f"Период: {discord.utils.format_dt(week_start, style='d')} — {discord.utils.format_dt(now, style='d')}"
        ),
        color=RED,
        timestamp=now,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Сообщения", value=str(activity["messages"]), inline=True)
    embed.add_field(name="Активных дней", value=str(activity["active_days"]), inline=True)
    embed.add_field(name="Последнее сообщение", value=last_message_text, inline=True)
    embed.add_field(name="Время в войсах", value=format_duration(activity["voice_seconds"]), inline=True)
    embed.add_field(name="Голосовых сессий", value=str(activity["voice_sessions"]), inline=True)
    embed.add_field(name="Средняя сессия", value=format_duration(activity["average_session_seconds"]), inline=True)
    embed.add_field(name="Сейчас в голосовом", value=voice_channel, inline=False)
    if member.joined_at is not None:
        embed.add_field(name="На сервере с", value=discord.utils.format_dt(member.joined_at, style="f"), inline=False)
    embed.set_footer(text="Учёт активности начался после запуска этой версии бота; тексты сообщений не сохраняются.")
    if has_clan_role(member):
        acceptance = store.get_acceptance(interaction.guild.id, member.id)
        if acceptance is None:
            accepted_ticket = store.find_last_accepted_ticket(interaction.guild.id, member.id)
            if accepted_ticket:
                acceptance = {
                    "accepted_at": accepted_ticket.get("accepted_at"),
                    "accepted_by": accepted_ticket.get("accepted_by"),
                    "ticket_number": accepted_ticket.get("number"),
                }
        if acceptance:
            try:
                accepted_at = discord.utils.format_dt(datetime.fromisoformat(acceptance["accepted_at"]), style="f")
            except (TypeError, ValueError, KeyError):
                accepted_at = "дата не сохранена"
            accepted_by = f"<@{acceptance['accepted_by']}>" if acceptance.get("accepted_by") else "неизвестно"
            ticket_no = ticket_label(int(acceptance["ticket_number"])) if acceptance.get("ticket_number") else "—"
            embed.add_field(
                name="Принят в Clan",
                value=f"**Кто принял:** {accepted_by}\n**Когда:** {accepted_at}\n**Заявка:** {ticket_no}",
                inline=False,
            )
        else:
            embed.add_field(name="Принят в Clan", value="Роль есть, но история принятия в базе не найдена.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True, delete_after=60)


@bot.tree.command(name="profile", description="Показать свой профиль RIval")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
async def profile_command(interaction: discord.Interaction) -> None:
    """Show a private self-profile on the server or in a DM with the bot."""
    in_guild = interaction.guild is not None
    await interaction.response.defer(ephemeral=in_guild, thinking=True)

    user = interaction.user
    profile_guild = interaction.guild
    member = user if isinstance(user, discord.Member) else None
    if profile_guild is None:
        candidates = list(bot.guilds)
        configured_guild_id = os.getenv("GUILD_ID", "").strip()
        if configured_guild_id.isdigit():
            configured_guild = bot.get_guild(int(configured_guild_id))
            if configured_guild is not None:
                candidates = [configured_guild, *(guild for guild in candidates if guild.id != configured_guild.id)]
        for candidate in candidates:
            cached_member = candidate.get_member(user.id)
            if cached_member is not None:
                profile_guild, member = candidate, cached_member
                break
            try:
                fetched_member = await candidate.fetch_member(user.id)
            except discord.NotFound:
                continue
            except (discord.Forbidden, discord.HTTPException):
                log.info("Could not resolve profile member %s in guild %s", user.id, candidate.id)
                continue
            profile_guild, member = candidate, fetched_member
            break

    settings = store.get_settings(profile_guild.id) if profile_guild is not None else None
    embed = make_profile_embed(user, member, profile_guild, settings)
    response_kwargs: dict[str, Any] = {
        "embed": embed,
        "view": ClanLinksView(include_application=True),
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    attachment = logo_file()
    if attachment is not None:
        response_kwargs["attachments"] = [attachment]
    await interaction.edit_original_response(**response_kwargs)

    # Guild responses are ephemeral and expire through the interaction helper.
    # A DM interaction response is an ordinary private DM, so remove it after
    # the same minute without deleting unrelated messages in the conversation.
    if not in_guild:
        async def expire_profile_dm() -> None:
            await asyncio.sleep(60)
            try:
                await interaction.delete_original_response()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        asyncio.create_task(expire_profile_dm())


@bot.tree.command(name="close", description="Запросить закрытие текущей заявки")
@app_commands.guild_only()
async def close_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or interaction.channel is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна в заявке на сервере.", ephemeral=True)
    ticket = store.get_ticket(interaction.guild.id, interaction.channel.id)
    if ticket is None:
        return await interaction.response.send_message("Это не ветка заявки.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    if interaction.user.id != int(ticket["user_id"]) and not support_member(
        interaction.user, settings, ticket.get("ticket_type", "clan")
    ):
        return await interaction.response.send_message("Закрыть заявку может её автор или поддержка.", ephemeral=True)
    await request_close(interaction, ticket, settings)


@bot.tree.command(name="help", description="Показать справку по боту заявок")
@app_commands.guild_only()
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="RIval Tickets — справка", color=PURPLE)
    embed.description = (
        "`/setup` — настроить роли, каналы и текст панели\n"
        "`/panel` — опубликовать панель в выбранном или текущем канале\n"
        "`/ticket` — сведения о заявке в текущей ветке\n"
        "`/tickets` — открытые заявки (для поддержки)\n"
        "`/stats` — приватная статистика участника за последние 7 дней\n"
        "`/profile` — твой приватный профиль RIval на сервере или в ЛС с ботом\n"
        "`/close` — запросить закрытие и оценить поддержку\n"
        "`/povish_send` — опубликовать инструкцию по повышению\n"
        "`/povish_set_date` — задать дату начала стажировки\n"
        "`/help` — эта справка"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="povish_send", description="Опубликовать инструкцию по повышению")
@app_commands.guild_only()
async def povish_send_command(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    if not is_promotion_admin(interaction.user):
        return await interaction.response.send_message("Команда доступна только администраторам повышений.", ephemeral=True)
    channel = interaction.guild.get_channel(PROMOTION_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.response.send_message("Не найден канал для инструкции по повышению.", ephemeral=True)
    settings = store.get_settings(interaction.guild.id)
    try:
        message = await channel.send(
            embed=promotion_instruction_embed(settings), allowed_mentions=discord.AllowedMentions.none()
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not publish the promotion instructions")
        return await interaction.response.send_message("Не удалось отправить инструкцию. Проверь права бота в канале.", ephemeral=True)
    await interaction.response.send_message(f"Инструкция опубликована в {channel.mention}: {message.jump_url}", ephemeral=True)


@bot.tree.command(name="povish_set_date", description="Задать дату начала стажировки участнику")
@app_commands.guild_only()
@app_commands.describe(
    member="Участник с ролью Trainee",
    date="Дата по Москве: ДД.ММ.ГГГГ [ЧЧ:ММ] или YYYY-MM-DD [HH:MM]",
)
async def povish_set_date_command(
    interaction: discord.Interaction,
    member: discord.Member,
    date: str,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
    if not is_promotion_admin(interaction.user):
        return await interaction.response.send_message("Команда доступна только администраторам повышений.", ephemeral=True)
    if not member_has_trainee(member):
        return await interaction.response.send_message("У участника нет роли Trainee.", ephemeral=True)
    trainee_since = parse_promotion_date(date)
    if trainee_since is None:
        return await interaction.response.send_message(
            "Не распознал дату. Пример: `04.10.2026 18:30` или `2026-10-04 18:30` (время московское).",
            ephemeral=True,
        )
    if trainee_since > discord.utils.utcnow():
        return await interaction.response.send_message("Дата начала стажировки не может быть в будущем.", ephemeral=True)

    previous = store.get_trainee(interaction.guild.id, member.id)
    store.set_trainee_start(interaction.guild.id, member.id, trainee_since.isoformat())
    if previous and previous.get("review_message_id"):
        settings = store.get_settings(interaction.guild.id)
        review_channel_id = settings.get("rank_review_channel_id") or PROMOTION_REVIEW_CHANNEL_ID
        channel = interaction.guild.get_channel(int(review_channel_id))
        if isinstance(channel, discord.TextChannel):
            try:
                old_message = await channel.fetch_message(int(previous["review_message_id"]))
                await old_message.edit(view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
    eligible_at = trainee_since + timedelta(days=7)
    await interaction.response.send_message(
        f"Дата стажировки {member.mention} сохранена: {discord.utils.format_dt(trainee_since, style='f')}. "
        f"Проверка станет доступна {discord.utils.format_dt(eligible_at, style='f')}; дата хранится в SQLite и не сбросится при перезапуске.",
        ephemeral=True,
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error("Slash command error", exc_info=error)
    message = "Команда завершилась с ошибкой. Проверь права бота и настройки сервера."
    if isinstance(error, app_commands.MissingPermissions):
        message = "Для этой команды нужны права администратора сервера."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    token = os.getenv("ds_token", os.getenv("DISCORD_TOKEN", "")).strip()
    if not token or token == "сюда_токен_бота":
        raise SystemExit("Не задан ds_token. Скопируй .env.example в .env и добавь токен бота.")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
