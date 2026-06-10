import json
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import OWNER_ID
from db import crud
from db.database import async_session
from db.models import PlayerLink, PlayerSession
from services.api import fetch_players, fetch_worldguard_regions, fetch_worlds
from services.timezone import format_dt, normalize_tz_input, parse_timezone

router = Router()
logger = logging.getLogger(__name__)

WHITELIST_ACTIONS = {"add", "remove", "list", "on", "off"}


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if chat_id == user_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


def is_owner(user_id: int | None) -> bool:
    return OWNER_ID and user_id == OWNER_ID


def _build_section(lines: list[str], header: str) -> str:
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"{header}\n<blockquote>{body}\n</blockquote>"


def _fmt_name(
    player_name: str,
    link_map: dict[str, PlayerLink],
    name_map: dict[str, str] | None = None,
) -> str:
    lower = player_name.lower()
    link = link_map.get(lower)
    if link:
        display = link.custom_name
    elif name_map:
        display = name_map.get(lower, player_name)
    else:
        display = player_name
    if link:
        if link.telegram_id:
            return f'<a href="tg://user?id={link.telegram_id}">{display}</a>'
        if link.telegram_username:
            return f'<a href="tg://resolve?domain={link.telegram_username}">{display}</a>'
    return display


def _parse_whitelist_args(
    args: str, chat_id: int, session,
) -> tuple[str, str | None, str | None]:
    """Parse /whitelist arguments. Returns (action, territory_name, player_name)."""
    parts = args.strip().split()
    if not parts:
        return "help", None, None

    first = parts[0].lower()

    if first in WHITELIST_ACTIONS or first == "global":
        action = "global" if first == "global" else first
        if action == "global" and len(parts) >= 2:
            action = parts[1].lower()
            rest = parts[2:]
        else:
            rest = parts[1:]

        if action in ("add", "remove"):
            player = " ".join(rest) if rest else None
            return action, None, player
        else:
            return action, None, None
    else:
        territory_name = first
        if len(parts) < 2:
            return "help", territory_name, None
        action = parts[1].lower()
        rest = parts[2:]
        if action in ("add", "remove"):
            player = " ".join(rest) if rest else None
            return action, territory_name, player
        else:
            return action, territory_name, None


def _secs_to_str(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}год")
    if minutes:
        parts.append(f"{minutes}хв")
    parts.append(f"{secs}с")
    return " ".join(parts)


# ── FSM for territory creation ────────────────────────────────────────


class CreateTerritory(StatesGroup):
    waiting_for_world = State()
    waiting_for_coordinates = State()
    waiting_for_region_selection = State()


# ── Parsing helpers ───────────────────────────────────────────────────


def _parse_coords(text: str) -> tuple[str, list | None] | tuple[None, None]:
    """Try to parse text as coordinates. Returns (shape_type, coords) or (None, None)."""
    parts = text.split()
    if len(parts) == 4:
        try:
            x1, z1, x2, z2 = map(float, parts)
            return "rectangle", [x1, z1, x2, z2]
        except ValueError:
            return None, None
    try:
        points = []
        for part in parts:
            x_str, z_str = part.split(",", 1)
            points.append([float(x_str.strip()), float(z_str.strip())])
        if len(points) < 3:
            return None, None
        return "polygon", points
    except (ValueError, IndexError):
        return None, None


async def _create_from_coords(
    message: Message, name: str, world: str, shape_type: str, coords: list,
) -> bool:
    async with async_session() as session:
        await crud.create_territory(
            session, message.chat.id, name,
            shape_type, json.dumps(coords),
            world=world, region_id=None, auto_update=False,
        )
    await message.answer(f"\u2705 Територія <b>{name}</b> створена!")
    return True


# ── /start, /help ─────────────────────────────────────────────────────


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "бот для відстеження хто що і де робить\n\n"
        "команди:\n"
        "/ter <b>create|delete|edit|list</b> — керування територіями\n"
        "/whitelist <b>[територія] add|remove|list|on|off</b> — вайтлист\n"
        "/link <b>add|remove|list</b> — прив'язка гравців\n"
        "/list — загальний статус\n"
        "/online — хто зараз онлайн\n"
        "/history <b>[гравець]</b> — історія заходів\n"
        "/stats — статистика\n"
        "/monitor <b>гравець</b> — моніторинг входу/виходу з сервера\n"
        "/search <b>гравець</b> — пошук гравця\n"
        "/time <b>[часовий_пояс]</b> — часовий пояс\n"
        "/alert <b>подія текст</b> — кастомні сповіщення\n"
        "/cancel — скасувати операцію\n\n"
        "Приклади:\n"
        "<code>/ter create mybase</code> — створити через WorldGuard\n"
        "<code>/ter create mybase world x1,z1 x2,z2</code> — вручну\n"
        "<code>/ter delete mybase</code> — видалити\n"
        "<code>/ter edit mybase radius 30</code> — змінити радіус\n"
        "<code>/whitelist add Player123</code> — глобальний ігнор\n"
        "<code>/whitelist mybase add Player123</code> — ігнор на території\n"
        "<code>/whitelist on</code> — увімкнути вайтлист\n"
        "<code>/whitelist off</code> — вимкнути вайтлист\n"
        "<code>/link add Player123 @username</code> — прив'язати гравця\n"
        "<code>/alert enter ⚠️ {player} на {territory}</code>\n\n"
        "<blockquote>автор бота: @migor1103 <i>(всі питання до нього)</i></blockquote>"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


# ── /cancel ────────────────────────────────────────────────────────────


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("\u2705 Операцію скасовано.")


# ── /ter ────────────────────────────────────────────────────────────────


@router.message(Command("ter"))
async def cmd_ter(message: Message, state: FSMContext, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""

    if sub == "create":
        await _ter_create(message, state, bot, parts[1] if len(parts) > 1 else "")
    elif sub == "delete":
        await _ter_delete(message, bot, parts[1] if len(parts) > 1 else "")
    elif sub == "edit":
        await _ter_edit(message, bot, parts[1] if len(parts) > 1 else "")
    elif sub == "list":
        await _ter_list(message)
    else:
        await message.answer(
            "Використання:\n"
            "/ter <b>create назва</b> — створити\n"
            "/ter <b>delete назва</b> — видалити\n"
            "/ter <b>edit назва поле значення</b> — змінити\n"
            "/ter <b>list</b> — список територій"
        )


async def _ter_create(message: Message, state: FSMContext, bot: Bot, rest: str):
    if not rest:
        await message.answer("Вкажіть назву: /ter create <b>назва</b>\n"
                             "Або скорочено: /ter create <b>назва світ x1,z1 x2,z2 ...</b>")
        return

    parts = rest.split()
    name = parts[0]

    if len(parts) >= 3:
        world_input = parts[1]
        coords_text = " ".join(parts[2:])
        shape_type, coords = _parse_coords(coords_text)
        if shape_type:
            worlds = await fetch_worlds()
            matched_world = None
            if worlds:
                for w in worlds:
                    if w["name"] == world_input or w.get("display_name", "") == world_input:
                        matched_world = w["name"]
                        break
            if matched_world:
                await state.clear()
                await _create_from_coords(message, name, matched_world, shape_type, coords)
                return

    await state.update_data(territory_name=name, chat_id=message.chat.id)
    worlds = await fetch_worlds()
    if not worlds:
        await message.answer("Не вдалося отримати список світів.")
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    for w in worlds:
        label = w.get("display_name", w["name"])
        builder.button(text=label, callback_data=f"world:{w['name']}")
    builder.adjust(1)

    await state.set_state(CreateTerritory.waiting_for_world)
    await message.answer(
        f"Територія: <b>{name}</b>\nОберіть світ:",
        reply_markup=builder.as_markup(),
    )


async def _ter_delete(message: Message, bot: Bot, name: str):
    if not name:
        await message.answer("Вкажіть назву: /ter delete <b>назва</b>")
        return
    async with async_session() as session:
        t = await crud.get_territory_by_name(session, message.chat.id, name)
        if not t:
            await message.answer(f'\u26a0\ufe0f Територію "{name}" не знайдено')
            return
        await crud.delete_territory(session, t.id)
    await message.answer(f"\u2705 Територія <b>{name}</b> видалена")


async def _ter_edit(message: Message, bot: Bot, rest: str):
    parts = rest.split(maxsplit=2) if rest else []
    if len(parts) < 2:
        await message.answer(
            "/ter edit <b>назва поле значення</b>\n"
            "Поля: <code>name</code>, <code>radius</code>, "
            "<code>auto_update</code> (on/off), <code>world</code>"
        )
        return

    territory_name = parts[0]
    field = parts[1].lower()
    value = parts[2] if len(parts) > 2 else ""

    async with async_session() as session:
        t = await crud.get_territory_by_name(session, message.chat.id, territory_name)
        if not t:
            await message.answer(f'\u26a0\ufe0f Територію "{territory_name}" не знайдено')
            return

        if field == "radius":
            try:
                radius = int(value)
            except ValueError:
                await message.answer("Радіус має бути цілим числом.")
                return
            if radius < 0:
                await message.answer("Радіус не може бути від'ємним.")
                return
            await crud.update_territory_radius(session, t.id, radius)
            await message.answer(
                f'\u2705 Для <b>{territory_name}</b> встановлено радіус <b>{radius}</b>'
            )
        elif field == "name":
            if not value:
                await message.answer("Вкажіть нову назву.")
                return
            existing = await crud.get_territory_by_name(session, message.chat.id, value)
            if existing:
                await message.answer(f'\u26a0\ufe0f Територія "{value}" вже існує')
                return
            await crud.update_territory(session, t.id, name=value)
            await message.answer(
                f'\u2705 Територія перейменована: "{territory_name}" → "<b>{value}</b>"'
            )
        elif field == "auto_update":
            if value.lower() in ("on", "true", "1"):
                await crud.update_territory(session, t.id, auto_update=True)
                await message.answer(f'\u2705 Для <b>{territory_name}</b> увімкнено автооновлення')
            elif value.lower() in ("off", "false", "0"):
                await crud.update_territory(session, t.id, auto_update=False)
                await message.answer(f'\u2705 Для <b>{territory_name}</b> вимкнено автооновлення')
            else:
                await message.answer("Використовуйте on/off")
        elif field == "world":
            worlds = await fetch_worlds()
            matched = None
            if worlds:
                for w in worlds:
                    if w["name"] == value or w.get("display_name", "") == value:
                        matched = w["name"]
                        break
            if not matched:
                await message.answer(f'\u26a0\ufe0f Світ "{value}" не знайдено')
                return
            await crud.update_territory(session, t.id, world=matched)
            await message.answer(f'\u2705 Світ для <b>{territory_name}</b> змінено на "{matched}"')
        elif field in ("coords", "coordinates"):
            shape_type, coords = _parse_coords(value)
            if not shape_type:
                await message.answer(
                    "Невірний формат координат.\n"
                    "Прямокутник: <code>x1 z1 x2 z2</code>\n"
                    "Багатокутник: <code>x1,z1 x2,z2 x3,z3 ...</code>"
                )
                return
            await crud.update_territory(
                session, t.id,
                shape_type=shape_type,
                coordinates=json.dumps(coords),
            )
            await message.answer(
                f'\u2705 Координати для <b>{territory_name}</b> оновлені '
                f'(тепер: {shape_type})'
            )
        else:
            await message.answer(f'\u26a0\ufe0f Невідоме поле: {field}')


async def _ter_list(message: Message):
    async with async_session() as session:
        territories = await crud.get_territories(session, message.chat.id)

    if not territories:
        await message.answer("Немає територій. Створіть: /ter create <b>назва</b>")
        return

    lines = []
    for t in territories:
        wg = f" (WG: {t.region_id})" if t.region_id else ""
        au = " \U0001f504" if t.auto_update else ""
        r = f", радіус: {t.proximity_radius}" if t.proximity_radius > 0 else ""
        lines.append(f"- <b>{t.name}</b> — {t.world}{wg}{au}{r}")

    await message.answer(
        _build_section(lines, f"Території <i>({len(lines)})</i>:")
        or "Немає територій."
    )


# ── FSM handlers for /ter create flow ────────────────────────────────


@router.callback_query(CreateTerritory.waiting_for_world, F.data.startswith("world:"))
async def on_world_selected(callback: CallbackQuery, state: FSMContext):
    world = (callback.data or "").split(":", 1)[1]
    await state.update_data(world=world)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return

    regions = await fetch_worldguard_regions(world)
    if not regions:
        await callback.message.edit_text(
            "\u26a0\ufe0f Не знайдено регіонів WorldGuard у цьому світі.\n"
            "Використайте скорочену форму:\n"
            "<code>/ter create назва світ x1,z1 x2,z2 ...</code>"
        )
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    for r in regions:
        builder.button(text=r["name"], callback_data=f"region:{r['name']}")
    builder.adjust(1)

    await state.update_data(wg_regions=regions)
    await state.set_state(CreateTerritory.waiting_for_region_selection)
    await callback.message.edit_text(
        f"Світ: <b>{world}</b>\nОберіть регіон:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(CreateTerritory.waiting_for_region_selection, F.data.startswith("region:"))
async def on_region_selected(callback: CallbackQuery, state: FSMContext):
    region_name = (callback.data or "").split(":", 1)[1]
    data = await state.get_data()
    regions = data.get("wg_regions", [])
    matched = next((r for r in regions if r["name"] == region_name), None)
    if not matched:
        await callback.answer("Помилка: регіон не знайдено")
        return

    async with async_session() as session:
        await crud.create_territory(
            session, data["chat_id"], data["territory_name"],
            matched["shape_type"], matched["coordinates"],
            world=data.get("world", "minecraft_overworld"),
            region_id=region_name, auto_update=True,
        )

    await state.clear()
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        f'\u2705 Територія <b>{data["territory_name"]}</b> створена '
        f'(WorldGuard: {region_name}, автооновлення увімкнено).'
    )


@router.message(CreateTerritory.waiting_for_coordinates, F.text)
async def handle_coordinates(message: Message, state: FSMContext):
    data = await state.get_data()
    if "territory_name" not in data or "chat_id" not in data:
        await state.clear()
        await message.answer(
            "\u26a0\ufe0f Стан створення території втрачено через перезапуск бота.\n"
            "Почніть заново: /ter create <b>назва</b>"
        )
        return

    text = (message.text or "").strip()
    shape_type, coords = None, None
    parts = text.split()

    if len(parts) == 4:
        try:
            x1, z1, x2, z2 = map(float, parts)
            coords = [x1, z1, x2, z2]
            shape_type = "rectangle"
        except ValueError:
            pass
    else:
        try:
            points = []
            for part in parts:
                x_str, z_str = part.split(",", 1)
                points.append([float(x_str.strip()), float(z_str.strip())])
            if len(points) >= 3:
                coords = points
                shape_type = "polygon"
        except (ValueError, IndexError):
            pass

    if not shape_type:
        await message.answer(
            "Невірний формат. Використовуйте:\n"
            "\u2022 Прямокутник: <code>x1 z1 x2 z2</code>\n"
            "\u2022 Багатокутник: <code>x1,z1 x2,z2 x3,z3 ...</code>"
        )
        return

    async with async_session() as session:
        await crud.create_territory(
            session, data["chat_id"], data["territory_name"],
            shape_type, json.dumps(coords),
            world=data.get("world", "minecraft_overworld"),
            region_id=None, auto_update=False,
        )

    await state.clear()
    await message.answer(f'\u2705 Територія <b>{data["territory_name"]}</b> створена!')


@router.message(CreateTerritory.waiting_for_coordinates)
async def handle_coordinates_non_text(message: Message):
    await message.answer("Будь ласка, надішліть координати текстом.")


@router.message(CreateTerritory.waiting_for_world, F.text)
async def handle_unexpected_text_in_creation(message: Message, state: FSMContext):
    await state.clear()
    text = message.text or ""
    shape_type, coords = _parse_coords(text.strip())
    if shape_type and len(text.split()) >= 3:
        await message.answer(
            "Оберіть світ через кнопки вище.\n"
            "Або використайте скорочену форму:\n"
            "<code>/ter create назва світ x1,z1 x2,z2 ...</code>"
        )
    else:
        await message.answer(
            "\u26a0\ufe0f Стан створення території втрачено.\n"
            "Почніть заново: /ter create <b>назва</b>"
        )


# ── /whitelist ─────────────────────────────────────────────────────────


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "/whitelist <b>add|remove|list|on|off</b> — глобально\n"
            "/whitelist <b>територія add|remove|list|on|off</b> — для території\n"
            "Приклад: <code>/whitelist add Player123</code>\n"
            "         <code>/whitelist mybase add Player123</code>"
        )
        return

    async with async_session() as session:
        action, territory_name, player = _parse_whitelist_args(args, message.chat.id, session)

        if action == "help":
            await message.answer("Невірний формат. /whitelist <b>add|remove|list|on|off</b>")
            return

        territory_id = None
        territory_display = "глобально"
        if territory_name:
            t = await crud.get_territory_by_name(session, message.chat.id, territory_name)
            if not t:
                await message.answer(f'\u26a0\ufe0f Територію "{territory_name}" не знайдено')
                return
            territory_id = t.id
            territory_display = f'на території "{t.name}"'

        if action == "add":
            if not player:
                await message.answer("Вкажіть гравця: /whitelist add <b>гравець</b>")
                return
            await crud.add_whitelist(session, message.chat.id, player, territory_id)
            await message.answer(f'\u2705 {player} доданий у вайтлист {territory_display}')

        elif action == "remove":
            if not player:
                await message.answer("Вкажіть гравця: /whitelist remove <b>гравець</b>")
                return
            await crud.remove_whitelist(session, message.chat.id, player, territory_id)
            await message.answer(f'\u2705 {player} видалений з вайтлиста {territory_display}')

        elif action == "list":
            entries = await crud.get_whitelist(session, message.chat.id, territory_id)
            if not entries:
                await message.answer(f"Вайтлист {territory_display} порожній.")
                return
            lines = [f"- {e.player_name}" for e in entries]
            await message.answer(
                _build_section(lines, f"Вайтлист {territory_display} <i>({len(lines)})</i>:")
            )

        elif action in ("on", "off"):
            enabled = action == "on"
            await crud.set_whitelist_config(session, message.chat.id, enabled, territory_id)
            status = "увімкнено" if enabled else "вимкнено"
            await message.answer(f'\u2705 Вайтлист {territory_display} {status}')


# ── /link ──────────────────────────────────────────────────────────────


@router.message(Command("link"))
async def cmd_link(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""

    if sub == "add":
        rest = parts[1] if len(parts) > 1 else ""
        await _link_add(message, rest)
    elif sub == "remove":
        rest = parts[1] if len(parts) > 1 else ""
        await _link_remove(message, rest)
    elif sub == "list":
        await _link_list(message)
    else:
        await message.answer(
            "/link <b>add нік @юзернейм|id [ім'я]</b> — прив'язати\n"
            "/link <b>remove нік</b> — видалити прив'язку\n"
            "/link <b>list</b> — список прив'язок"
        )


async def _link_add(message: Message, rest: str):
    parts = rest.split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "Вкажіть нік гравця та @юзернейм або ID:\n"
            "/link add <b>нік</b> <b>@юзернейм|id</b> [ім'я]"
        )
        return

    minecraft_name = parts[0]
    identifier = parts[1]
    custom_name = parts[2] if len(parts) > 2 else minecraft_name

    telegram_id: int | None = None
    telegram_username: str | None = None

    if identifier.startswith("@"):
        telegram_username = identifier[1:]
    else:
        try:
            telegram_id = int(identifier)
        except ValueError:
            await message.answer("Другий параметр має бути @юзернейм або числовим ID.")
            return

    async with async_session() as session:
        await crud.create_player_link(
            session, minecraft_name=minecraft_name,
            telegram_id=telegram_id, telegram_username=telegram_username,
            custom_name=custom_name,
        )

    target = f"@{telegram_username}" if telegram_username else str(telegram_id)
    suffix = f" як <b>{custom_name}</b>" if custom_name != minecraft_name else ""
    await message.answer(f'\u2705 Гравець <b>{minecraft_name}</b> прив\'язаний до <b>{target}</b>{suffix}')


async def _link_remove(message: Message, name: str):
    if not name:
        await message.answer("Вкажіть нік: /link remove <b>нік</b>")
        return
    async with async_session() as session:
        link = await crud.get_player_link(session, name)
        if not link:
            await message.answer(f'\u26a0\ufe0f Прив\'язку для {name} не знайдено')
            return
        await crud.delete_player_link(session, name)
    await message.answer(f"\u2705 Прив'язка для <b>{name}</b> видалена")


async def _link_list(message: Message):
    async with async_session() as session:
        links = await crud.get_all_player_links(session)
    if not links:
        await message.answer("Немає прив'язок гравців.")
        return
    lines = []
    for l in links:
        target = f"@{l.telegram_username}" if l.telegram_username else str(l.telegram_id)
        lines.append(f"- {l.minecraft_name} → {target} <i>({l.custom_name})</i>")
    await message.answer(
        _build_section(lines, "Прив'язки гравців:") or "Немає прив'язок гравців."
    )


# ── /list ──────────────────────────────────────────────────────────────


@router.message(Command("list"))
async def cmd_list(message: Message):
    chat_id = message.chat.id

    async with async_session() as session:
        territories = await crud.get_territories(session, chat_id)
        sessions = await crud.get_active_sessions(session, chat_id)
        whitelist_entries = await crud.get_whitelist(session, chat_id)
        tracked = await crud.get_tracked_players(session, chat_id)
        links = await crud.get_all_player_links(session)
        chat_config = await crud.get_chat_config(session, chat_id)
        whitelist_enabled = await crud.get_whitelist_config(session, chat_id)
    tz_name = chat_config.timezone

    link_map = {l.minecraft_name: l for l in links}

    players = await fetch_players()
    name_map: dict[str, str] = {}
    if players:
        for p in players:
            original = str(p["name"])
            name_map[original.lower()] = original
    from services.tracker import get_name_casing
    for lower, original in get_name_casing().items():
        name_map.setdefault(lower, original)

    if not territories and not whitelist_entries and not tracked:
        await message.answer("Немає даних. Створіть територію: /ter create <b>назва</b>")
        return

    territory_names = {t.id: t.name for t in territories}

    session_by_tid: dict[int, list[PlayerSession]] = {}
    for s in sessions:
        session_by_tid.setdefault(s.territory_id, []).append(s)

    tracked_lookup = {tp.player_name: tp for tp in tracked}
    on_territory_names = {s.player_name for s in sessions}

    parts: list[str] = []

    territory_lines: list[str] = []
    for tid, ss in session_by_tid.items():
        tname = territory_names.get(tid, "???")
        for s in ss:
            ts = format_dt(s.entered_at, tz_name)
            display = _fmt_name(s.player_name, link_map, name_map)
            territory_lines.append(f"- {display} <i>[з {ts}]</i>  <b>{tname}</b>")
    s = _build_section(
        territory_lines,
        f"на території <i>({len(territory_lines)})</i>:",
    )
    if s:
        parts.append(s)

    whitelist_lines: list[str] = []
    for w in whitelist_entries:
        pn = w.player_name
        display = _fmt_name(pn, link_map, name_map)
        if pn in on_territory_names:
            for s in sessions:
                if s.player_name == pn:
                    ts = format_dt(s.entered_at, tz_name)
                    whitelist_lines.append(f"- {display} <i>(з {ts})</i>")
                    break
        else:
            tp = tracked_lookup.get(pn)
            if tp:
                ts = format_dt(tp.last_seen, tz_name)
                whitelist_lines.append(f"- {display} <i>(був {ts})</i>")
            else:
                whitelist_lines.append(f"- {display}")
    wl_status = " (увімкнено)" if whitelist_enabled else " (вимкнено)"
    s = _build_section(
        whitelist_lines,
        f"у вайтлисті{wl_status} <i>({len(whitelist_lines)})</i>:",
    )
    if s:
        parts.append(s)

    unique_lines: list[str] = []
    for tp in tracked:
        pn = tp.player_name
        display = _fmt_name(pn, link_map, name_map)
        if pn in on_territory_names:
            for s in sessions:
                if s.player_name == pn:
                    ts = format_dt(s.entered_at, tz_name)
                    unique_lines.append(f"- {display} <i>(з {ts})</i>")
                    break
        else:
            ts = format_dt(tp.last_seen, tz_name)
            unique_lines.append(f"- {display} <i>(був {ts})</i>")
    s = _build_section(
        unique_lines,
        f"унікальні гравці <i>({len(unique_lines)})</i>:",
    )
    if s:
        parts.append(s)

    if not parts:
        await message.answer("пустовато на вашій(их) території(ях)...")
        return

    await message.answer("\n\n".join(parts))


# ── /online ────────────────────────────────────────────────────────────


@router.message(Command("online"))
async def cmd_online(message: Message):
    players = await fetch_players()
    if not players:
        await message.answer("Не вдалося отримати список гравців.")
        return

    by_world: dict[str, list[str]] = {}
    for p in players:
        w = str(p.get("world", "unknown"))
        name = str(p["name"])
        by_world.setdefault(w, []).append(name)

    total = len(players)
    lines = []
    for world, names in sorted(by_world.items()):
        lines.append(f"<b>{world}</b>: {', '.join(names)}")

    header = f"\U0001f7e2 Онлайн <i>({total})</i>:"
    await message.answer(_build_section(lines, header) or "\U0001f7e2 Нікого немає онлайн.")


# ── /history ───────────────────────────────────────────────────────────


@router.message(Command("history"))
async def cmd_history(message: Message, command: CommandObject):
    player_filter = (command.args or "").strip() or None

    async with async_session() as session:
        history = await crud.get_session_history(
            session, message.chat.id, player_name=player_filter, limit=30,
        )
        territories = {t.id: t.name for t in await crud.get_territories(session, message.chat.id)}

    if not history:
        msg = "Історія порожня."
        if player_filter:
            msg = f'Історія для гравця "{player_filter}" порожня.'
        await message.answer(msg)
        return

    lines = []
    for h in history:
        tname = territories.get(h.territory_id, "???")
        entered = format_dt(h.entered_at, "Europe/Kyiv")
        duration = f" ({_secs_to_str(h.duration_seconds)})" if h.duration_seconds else ""
        lines.append(f"- <b>{h.player_name}</b> → {tname} [{entered}]{duration}")

    header = f"Історія заходів <i>({len(lines)})</i>:"
    if player_filter:
        header = f'Історія гравця "{player_filter}" <i>({len(lines)})</i>:'

    await message.answer(_build_section(lines, header) or "Історія порожня.")


# ── /stats ─────────────────────────────────────────────────────────────


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    async with async_session() as session:
        data = await crud.get_player_time_on_territories(session, message.chat.id)
        territories = {t.id: t.name for t in await crud.get_territories(session, message.chat.id)}

    if not data:
        await message.answer("Статистика поки що порожня.")
        return

    by_player: dict[str, list[dict]] = {}
    for entry in data:
        by_player.setdefault(entry["player_name"], []).append(entry)

    lines = []
    for player, entries in sorted(by_player.items()):
        player_total = sum(e["total_seconds"] for e in entries)
        details = []
        for e in sorted(entries, key=lambda x: x["total_seconds"], reverse=True):
            tname = territories.get(e["territory_id"], "???")
            details.append(f"{tname}: {_secs_to_str(e['total_seconds'])}")
        lines.append(f"- <b>{player}</b> — всього {_secs_to_str(player_total)}")
        for d in details[:3]:
            lines.append(f"  \u2022 {d}")

    await message.answer(
        _build_section(lines, f"Статистика <i>({len(by_player)})</i>:")
        or "Статистика поки що порожня."
    )


# ── /monitor ───────────────────────────────────────────────────────────


@router.message(Command("monitor"))
async def cmd_monitor(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    player_name = (command.args or "").strip()
    if not player_name:
        await message.answer("Вкажіть гравця: /monitor <b>нік</b>")
        return

    async with async_session() as session:
        existing = await crud.get_monitor(session, message.chat.id, player_name)
        if existing:
            await crud.remove_monitor(session, message.chat.id, player_name)
            await message.answer(f'\u2705 Моніторинг <b>{player_name}</b> вимкнено')
        else:
            await crud.add_monitor(session, message.chat.id, player_name)
            await message.answer(
                f'\u2705 Моніторинг <b>{player_name}</b> увімкнено.\n'
                f'Я отримуватиму сповіщення коли він заходить/виходить з сервера.'
            )


# ── /search ────────────────────────────────────────────────────────────


@router.message(Command("search"))
async def cmd_search(message: Message, bot: Bot, command: CommandObject):
    player_name = (command.args or "").strip()
    if not player_name:
        await message.answer("Вкажіть гравця: /search <b>нік</b>")
        return

    chat = message.chat
    user = message.from_user

    if chat.type == ChatType.PRIVATE and user and is_owner(user.id):
        async with async_session() as session:
            results = await crud.search_player_across_chats(session, player_name)
        if not results:
            await message.answer(f'Гравця "{player_name}" не знайдено в жодному чаті.')
            return
        lines = []
        for r in results:
            tnames = ", ".join(t["name"] for t in r["territories"])
            lines.append(f"- Чат {r['chat_id']}: території: {tnames}")
        await message.answer(
            _build_section(lines, f'Результати пошуку "{player_name}":')
        )
    else:
        async with async_session() as session:
            result = await crud.search_player_in_chat(session, message.chat.id, player_name)
        territories = result["territories"]
        history = result["history"]
        active = result["active_sessions"]
        tracked = result["tracked"]

        parts = []
        if territories:
            parts.append(
                _build_section(
                    [f"- {t['name']}" for t in territories],
                    f"Території в цьому чаті:",
                )
            )
        if active:
            parts.append(
                _build_section(
                    [f"- {s.player_name} на території" for s in active],
                    "Активні сесії:",
                )
            )
        if history:
            history_lines = []
            for h in history[:10]:
                entered = format_dt(h.entered_at, "Europe/Kyiv")
                if h.exited_at:
                    exited = format_dt(h.exited_at, "Europe/Kyiv")
                    dur = f" ({_secs_to_str(h.duration_seconds)})" if h.duration_seconds else ""
                    history_lines.append(
                        f"- <b>{h.player_name}</b> зайшов {entered}, вийшов {exited}{dur}"
                    )
                else:
                    history_lines.append(
                        f"- <b>{h.player_name}</b> зайшов {entered}, ще на території"
                    )
            parts.append(
                _build_section(history_lines, f"Історія ({len(history)}):")
            )
        if tracked:
            parts.append(
                f"Останній раз був: {format_dt(tracked.last_seen, 'Europe/Kyiv')}"
            )

        if not parts:
            await message.answer(f'Гравця "{player_name}" не знайдено в цьому чаті.')
            return
        await message.answer("\n\n".join(parts))


# ── /time ──────────────────────────────────────────────────────────────


@router.message(Command("time"))
async def cmd_time(message: Message, command: CommandObject):
    args = (command.args or "").strip()

    async with async_session() as session:
        config = await crud.get_chat_config(session, message.chat.id)

        if not args:
            tz = config.timezone
            try:
                offset = datetime.now(parse_timezone(tz)).strftime("%z")
                tz_display = f"GMT{offset[:3]}:{offset[3:]}" if offset else "UTC"
            except ValueError:
                tz_display = tz
            await message.answer(
                f"\U0001f550 Поточний часовий пояс: <b>{tz_display}</b> (<code>{tz}</code>)\n"
                "Змінити: /time <b>назва_таймзони</b>\n"
                "Наприклад: <code>/time Europe/Kyiv</code> або <code>/time gmt+2</code>"
            )
            return

        tz_input = normalize_tz_input(args)
        try:
            parse_timezone(tz_input)
        except ValueError:
            await message.answer(
                f"\u274c Невідомий часовий пояс: <code>{args}</code>\n"
                "Використовуйте IANA назву (наприклад, <code>Europe/Kyiv</code>) "
                "або GMT зміщення (наприклад, <code>gmt+2</code>, <code>gmt-5</code>)."
            )
            return

        await crud.update_chat_timezone(session, message.chat.id, tz_input)

    try:
        offset = datetime.now(parse_timezone(tz_input)).strftime("%z")
        tz_display = f"GMT{offset[:3]}:{offset[3:]}" if offset else "UTC"
    except ValueError:
        tz_display = tz_input

    await message.answer(
        f"\u2705 Часовий пояс змінено на <b>{tz_display}</b> (<code>{tz_input}</code>)"
    )


# ── /alert ─────────────────────────────────────────────────────────────


@router.message(Command("alert"))
async def cmd_alert(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=2)
    first = parts[0].lower() if parts else ""

    valid_events = {"enter", "exit", "proximity", "monitor_join", "monitor_leave"}
    event_enabled_map = {
        "enter": "enter_enabled",
        "exit": "exit_enabled",
        "proximity": "proximity_enabled",
        "monitor_join": "monitor_join_enabled",
        "monitor_leave": "monitor_leave_enabled",
    }
    template_map = {
        "enter": "enter_template",
        "exit": "exit_template",
        "proximity": "proximity_template",
        "monitor_join": "monitor_join_template",
        "monitor_leave": "monitor_leave_template",
    }

    # ── Show current config ────────────────────────────────────────
    if first in ("", "show"):
        async with async_session() as session:
            config = await crud.get_alert_config(session, message.chat.id)
        status_lines = []
        for evt in sorted(event_enabled_map.keys()):
            enabled = getattr(config, event_enabled_map[evt], True)
            icon = "\u2705" if enabled else "\u274c"
            template = getattr(config, template_map[evt], "")
            status_lines.append(f"{icon} <b>{evt}</b>: <code>{template}</code>")
        await message.answer(
            "Поточні сповіщення:\n\n"
            + "\n".join(status_lines)
            + "\n\nЗмінні: <code>{player}</code>, <code>{territory}</code>, <code>{world}</code>\n\n"
            "Змінити текст: <code>/alert подія новий_текст</code>\n"
            "Вимкнути: <code>/alert disable подія</code>\n"
            "Увімкнути: <code>/alert enable подія</code>\n"
            "Приклад: <code>/alert disable enter</code>"
        )
        return

    # ── enable/disable ─────────────────────────────────────────────
    if first in ("enable", "disable"):
        if len(parts) < 2:
            await message.answer(f"Вкажіть подію: /alert {first} <b>подія</b>")
            return
        event = parts[1].lower()
        if event not in event_enabled_map:
            await message.answer(
                f"Невідома подія: {event}\n"
                f"Доступні: {', '.join(sorted(valid_events))}"
            )
            return
        enabled = first == "enable"
        async with async_session() as session:
            await crud.set_alert_enabled(
                session, message.chat.id, event_enabled_map[event], enabled
            )
        status = "увімкнено" if enabled else "вимкнено"
        await message.answer(f'\u2705 Сповіщення "{event}" {status}')
        return

    # ── Set template ───────────────────────────────────────────────
    field = first
    text = parts[1] if len(parts) > 1 else ""

    if field not in valid_events:
        await message.answer(
            f"Невідома подія: {field}\n"
            f"Доступні: {', '.join(sorted(valid_events))}\n"
            f"Або: enable/disable"
        )
        return

    if not text:
        await message.answer("Вкажіть текст сповіщення.")
        return

    async with async_session() as session:
        await crud.update_alert_template(
            session, message.chat.id, template_map[field], text
        )

    await message.answer(f'\u2705 Шаблон для події "{field}" оновлено.')
