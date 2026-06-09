import json
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db import crud
from db.database import async_session
from db.models import PlayerLink, PlayerSession
from services.api import fetch_players, fetch_worldguard_regions, fetch_worlds

router = Router()
logger = logging.getLogger(__name__)


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if chat_id == user_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


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


class CreateTerritory(StatesGroup):
    waiting_for_world = State()
    waiting_for_method = State()
    waiting_for_coordinates = State()
    waiting_for_region_selection = State()


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "бот для відстеження хто що і де робить\n\n"
        "команди:\n"
        "/create <b>назва</b> — створити територію\n"
        "/create <b>назва світ x1,z1 x2,z2 ...</b> — скорочена форма\n"
        "/whitelist <b>гравець</b> — додати гравця до ігнору\n"
        "/unwhitelist <b>гравець</b> — видалити з ігнору\n"
        "/list — список територій та гравців\n"
        "/delete <b>назва</b> — видалити територію\n"
        "/radius <b>назва</b> <b>радіус</b> — радіус сповіщення про близькість\n"
        "/link <b>нік</b> <b>@юзернейм|id</b> [ім'я] — прив'язати гравця\n"
        "/unlink <b>нік</b> — видалити прив'язку\n"
        "/links — список прив'язок\n"
        "/cancel — скасувати створення території\n\n"
        "<blockquote>автор бота: @migor1103 <i>(всі питання до нього)</i></blockquote>"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активних дій для скасування.")
        return
    await state.clear()
    await message.answer("✅ Створення території скасовано.")


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


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
    """Create territory from parsed coordinates. Returns True on success."""
    async with async_session() as session:
        await crud.create_territory(
            session, message.chat.id, name,
            shape_type, json.dumps(coords),
            world=world, region_id=None, auto_update=False,
        )
    await message.answer(f'\u2705 Територія <b>{name}</b> створена!')
    return True


@router.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    cmd = (message.text or "").split(maxsplit=1)
    rest = cmd[1].strip() if len(cmd) > 1 else ""
    if not rest:
        await message.answer("Вкажіть назву: /create <b>назва</b>\n"
                             "Або скорочено: /create <b>назва світ x1,z1 x2,z2 ...</b>")
        return

    parts = rest.split()
    name = parts[0]

    # Try single-command mode: /create <name> <world> <coords>
    if len(parts) >= 3:
        world_input = parts[1]
        coords_text = " ".join(parts[2:])
        shape_type, coords = _parse_coords(coords_text)
        if shape_type:
            worlds = await fetch_worlds()
            # Match by internal name (underscore) or display name (colon)
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

    # Multi-step FSM flow
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


@router.callback_query(CreateTerritory.waiting_for_world, F.data.startswith("world:"))
async def on_world_selected(callback: CallbackQuery, state: FSMContext):
    world = (callback.data or "").split(":", 1)[1]
    await state.update_data(world=world)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="\u270f\ufe0f Ввести вручну", callback_data="method:manual")
    builder.button(text="\U0001f4e1 Імпорт з WorldGuard", callback_data="method:wg")
    builder.adjust(1)

    await state.set_state(CreateTerritory.waiting_for_method)
    await callback.message.edit_text(
        f"Світ: <b>{world}</b>\nЯк створити територію?",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(CreateTerritory.waiting_for_method, F.data == "method:manual")
async def on_method_manual(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await state.set_state(CreateTerritory.waiting_for_coordinates)
    await callback.message.edit_text(
        "Надішліть координати одним з форматів:\n"
        "\u2022 <b>Прямокутник</b>: <code>x1 z1 x2 z2</code>\n"
        "\u2022 <b>Багатокутник</b>: <code>x1,z1 x2,z2 x3,z3 ...</code>"
    )


@router.callback_query(CreateTerritory.waiting_for_method, F.data == "method:wg")
async def on_method_wg(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    world = data.get("world", "minecraft_overworld")
    await callback.answer()
    if not isinstance(callback.message, Message):
        return

    regions = await fetch_worldguard_regions(world)
    if not regions:
        await callback.message.edit_text(
            "\u26a0\ufe0f Не знайдено регіонів WorldGuard у цьому світі."
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
        "Оберіть регіон:", reply_markup=builder.as_markup()
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
            session,
            data["chat_id"],
            data["territory_name"],
            matched["shape_type"],
            matched["coordinates"],
            world=data.get("world", "minecraft_overworld"),
            region_id=region_name,
            auto_update=True,
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
            "\u26a0\ufe0f Стан створення територі\u0457 втрачено через перезапуск бота.\n"
            "Почніть заново: /create <b>назва</b>"
        )
        return

    text = (message.text or "").strip()

    shape_type: str
    coords: list[float] | list[list[float]]

    parts = text.split()
    if len(parts) == 4:
        try:
            x1, z1, x2, z2 = map(float, parts)
        except ValueError:
            await message.answer("Невірний формат чисел. Спробуйте ще раз.")
            return
        coords = [x1, z1, x2, z2]
        shape_type = "rectangle"
    else:
        try:
            points = []
            for part in parts:
                x_str, z_str = part.split(",", 1)
                points.append([float(x_str.strip()), float(z_str.strip())])
            if len(points) < 3:
                await message.answer("Для багатокутника потрібно мінімум 3 точки.")
                return
            coords = points
            shape_type = "polygon"
        except (ValueError, IndexError):
            await message.answer(
                "Невірний формат. Використовуйте:\n"
                "\u2022 Прямокутник: <code>x1 z1 x2 z2</code>\n"
                "\u2022 Багатокутник: <code>x1,z1 x2,z2 x3,z3 ...</code>"
            )
            return

    async with async_session() as session:
        await crud.create_territory(
            session,
            data["chat_id"],
            data["territory_name"],
            shape_type,
            json.dumps(coords),
            world=data.get("world", "minecraft_overworld"),
            region_id=None,
            auto_update=False,
        )

    await state.clear()
    await message.answer(
        f'\u2705 Територія <b>{data["territory_name"]}</b> створена!'
    )


@router.message(CreateTerritory.waiting_for_coordinates)
async def handle_coordinates_non_text(message: Message):
    await message.answer("Будь ласка, надішліть координати текстом.")


# Catch text messages in creation states when user clicked wrong
@router.message(CreateTerritory.waiting_for_world, F.text)
@router.message(CreateTerritory.waiting_for_method, F.text)
async def handle_unexpected_text_in_creation(message: Message, state: FSMContext):
    await state.clear()
    text = message.text or ""
    # Check if this looks like coordinates (user trying to paste without clicking buttons)
    shape_type, coords = _parse_coords(text.strip())
    if shape_type and len(text.split()) >= 3:
        # They sent coordinates without selecting world/method - guide them
        await message.answer(
            "Оберіть світ та метод введення через кнопки вище.\n"
            "Або використайте скорочену форму:\n"
            "/create <b>назва світ x1,z1 x2,z2 ...</b>"
        )
    else:
        await message.answer(
            "\u26a0\ufe0f Стан створення територі\u0457 втрачено.\n"
            "Почніть заново: /create <b>назва</b>"
        )


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Вкажіть гравця: /whitelist <b>ім'я_гравця</b>")
        return

    async with async_session() as session:
        await crud.add_whitelist(session, message.chat.id, name)

    await message.answer(f"\u2705 {name} доданий до вайтлиста")


@router.message(Command("unwhitelist"))
async def cmd_unwhitelist(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Вкажіть гравця: /unwhitelist <b>ім'я_гравця</b>")
        return

    async with async_session() as session:
        await crud.remove_whitelist(session, message.chat.id, name)

    await message.answer(f"\u2705 {name} видалений з вайтлиста")


@router.message(Command("delete"))
async def cmd_delete(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Вкажіть назву: /delete <b>назва</b>")
        return

    async with async_session() as session:
        t = await crud.get_territory_by_name(session, message.chat.id, name)
        if not t:
            await message.answer(f'\u26a0\ufe0f Територію "{name}" не знайдено')
            return
        await crud.delete_territory(session, t.id)

    await message.answer(f"\u2705 Територія <b>{name}</b> видалена")


@router.message(Command("list"))
async def cmd_list(message: Message):
    chat_id = message.chat.id

    async with async_session() as session:
        territories = await crud.get_territories(session, chat_id)
        sessions = await crud.get_active_sessions(session, chat_id)
        whitelist_entries = await crud.get_whitelist(session, chat_id)
        tracked = await crud.get_tracked_players(session, chat_id)
        links = await crud.get_all_player_links(session)

    link_map = {l.minecraft_name: l for l in links}

    players = await fetch_players()
    name_map: dict[str, str] = {}
    if players:
        for p in players:
            original = str(p["name"])
            name_map[original.lower()] = original

    if not territories and not whitelist_entries and not tracked:
        await message.answer("Немає даних. Створіть територію: /create <b>назва</b>")
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
            ts = s.entered_at.strftime("%Y-%m-%d %H:%M")
            display = _fmt_name(s.player_name, link_map, name_map)
            territory_lines.append(
                f"- {display} <i>[\u0437 {ts}]</i>  <b>{tname}</b>"
            )
    s = _build_section(
        territory_lines,
        f"\u043d\u0430 \u0442\u0435\u0440\u0438\u0442\u043e\u0440\u0456\u0457 "
        f"<i>({len(territory_lines)})</i>:",
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
                    ts = s.entered_at.strftime("%Y-%m-%d %H:%M")
                    whitelist_lines.append(f"- {display} <i>(\u0437 {ts})</i>")
                    break
        else:
            tp = tracked_lookup.get(pn)
            if tp:
                ts = tp.last_seen.strftime("%Y-%m-%d %H:%M")
                whitelist_lines.append(f"- {display} <i>(\u0431\u0443\u0432 {ts})</i>")
            else:
                whitelist_lines.append(f"- {display}")
    s = _build_section(
        whitelist_lines,
        f"\u0443 \u0432\u0430\u0439\u0442\u043b\u0438\u0441\u0442\u0456 "
        f"<i>({len(whitelist_lines)})</i>:",
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
                    ts = s.entered_at.strftime("%Y-%m-%d %H:%M")
                    unique_lines.append(f"- {display} <i>(\u0437 {ts})</i>")
                    break
        else:
            ts = tp.last_seen.strftime("%Y-%m-%d %H:%M")
            unique_lines.append(f"- {display} <i>(\u0431\u0443\u0432 {ts})</i>")
    s = _build_section(
        unique_lines,
        f"\u0443\u043d\u0456\u043a\u0430\u043b\u044c\u043d\u0456 \u0433\u0440\u0430\u0432\u0446\u0456 "
        f"<i>({len(unique_lines)})</i>:",
    )
    if s:
        parts.append(s)

    if not parts:
        await message.answer("пустовато на вашій(их) території(ях)...")
        return

    await message.answer("\n\n".join(parts))


@router.message(Command("radius"))
async def cmd_radius(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Вкажіть назву території та радіус:\n"
            "/radius <b>назва</b> <b>радіус</b>"
        )
        return

    name = args[0]
    try:
        radius = int(args[1])
    except ValueError:
        await message.answer("Радіус має бути цілим числом.")
        return
    if radius < 0:
        await message.answer("Радіус не може бути від'ємним.")
        return

    async with async_session() as session:
        t = await crud.get_territory_by_name(session, message.chat.id, name)
        if not t:
            await message.answer(f'\u26a0\ufe0f Територію "{name}" не знайдено')
            return
        await crud.update_territory_radius(session, t.id, radius)

    await message.answer(
        f'\u2705 Для території <b>{name}</b> встановлено радіус сповіщення <b>{radius}</b> блоків.'
    )


@router.message(Command("link"))
async def cmd_link(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    parts = (command.args or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            "Вкажіть нік гравця та @юзернейм або ID:\n"
            "/link <b>нік</b> <b>@юзернейм|id</b> [ім'я]"
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
            await message.answer(
                "Другий параметр має бути @юзернейм або числовим ID."
            )
            return

    async with async_session() as session:
        await crud.create_player_link(
            session,
            minecraft_name=minecraft_name,
            telegram_id=telegram_id,
            telegram_username=telegram_username,
            custom_name=custom_name,
        )

    await message.answer(
        f'\u2705 Гравець <b>{minecraft_name}</b> прив\'язаний до '
        + (f'<b>@{telegram_username}</b>' if telegram_username else f'<b>{telegram_id}</b>')
        + (f' як <b>{custom_name}</b>' if custom_name != minecraft_name else '')
    )


@router.message(Command("unlink"))
async def cmd_unlink(message: Message, bot: Bot, command: CommandObject):
    if not await is_admin(bot, message.chat.id, message.from_user.id if message.from_user else 0):
        await message.answer("\u26a0\ufe0f Ця команда доступна тільки адміністраторам.")
        return

    name = (command.args or "").strip()
    if not name:
        await message.answer("Вкажіть нік: /unlink <b>нік</b>")
        return

    async with async_session() as session:
        link = await crud.get_player_link(session, name)
        if not link:
            await message.answer(f'\u26a0\ufe0f Прив\'язку для {name} не знайдено')
            return
        await crud.delete_player_link(session, name)

    await message.answer(f"\u2705 Прив'язка для <b>{name}</b> видалена")


@router.message(Command("links"))
async def cmd_links(message: Message):
    async with async_session() as session:
        links = await crud.get_all_player_links(session)

    if not links:
        await message.answer("Немає прив'язок гравців.")
        return

    lines: list[str] = []
    for l in links:
        target = (
            f"@{l.telegram_username}" if l.telegram_username else str(l.telegram_id)
        )
        lines.append(f"- {l.minecraft_name} → {target} <i>({l.custom_name})</i>")

    await message.answer(
        _build_section(lines, "Прив'язки гравців:")
        or "Немає прив'язок гравців."
    )
