import asyncio
import html
import logging
import os
import re
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, LabeledPrice, Message,
                           PreCheckoutQuery)

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB = "market.db"
USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")

router = Router()


def q(sql, args=(), one=False, commit=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, args)
    if commit:
        con.commit()
    rows = cur.fetchone() if one else cur.fetchall()
    last = cur.lastrowid
    con.close()
    return last if commit else rows


def init_db():
    q("""CREATE TABLE IF NOT EXISTS lots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER, seller_name TEXT,
        username TEXT, kind TEXT, price INTEGER, days INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        buyer_id INTEGER, rent_until TEXT)""", commit=True)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


MAIN = kb([
    [("🛒 Купить юзернейм", "list:sale")],
    [("⏳ Арендовать юзернейм", "list:rent")],
    [("➕ Выставить свой юзернейм", "add")],
    [("📦 Мои лоты", "my")],
])


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("💎 <b>Маркетплейс премиум NFT-юзернеймов</b>\n"
                   "Покупка и аренда за Telegram Stars ⭐", reply_markup=MAIN)


@router.callback_query(F.data == "menu")
async def menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("💎 <b>Маркетплейс премиум NFT-юзернеймов</b>", reply_markup=MAIN)
    await c.answer()


@router.callback_query(F.data.startswith("list:"))
async def list_lots(c: CallbackQuery):
    kind = c.data.split(":")[1]
    lots = q("SELECT * FROM lots WHERE status='active' AND kind=? ORDER BY id DESC LIMIT 20", (kind,))
    title = "🛒 Продаются" if kind == "sale" else "⏳ Сдаются в аренду"
    if not lots:
        await c.message.edit_text(f"{title}\n\nПока пусто.", reply_markup=kb([[("⬅️ Назад", "menu")]]))
        return await c.answer()
    rows = []
    for l in lots:
        extra = f" / {l['days']} дн." if kind == "rent" else ""
        rows.append([(f"@{l['username']} — {l['price']}⭐{extra}", f"lot:{l['id']}")])
    rows.append([("⬅️ Назад", "menu")])
    await c.message.edit_text(title, reply_markup=kb(rows))
    await c.answer()


@router.callback_query(F.data.startswith("lot:"))
async def lot_view(c: CallbackQuery):
    l = q("SELECT * FROM lots WHERE id=?", (c.data.split(":")[1],), one=True)
    if not l or l["status"] != "active":
        return await c.answer("Лот недоступен", show_alert=True)
    if l["kind"] == "sale":
        text = f"💎 <b>@{l['username']}</b>\nПродажа: <b>{l['price']}⭐</b>"
        btn = f"Купить за {l['price']}⭐"
    else:
        text = f"💎 <b>@{l['username']}</b>\nАренда на <b>{l['days']} дн.</b>: <b>{l['price']}⭐</b>"
        btn = f"Арендовать за {l['price']}⭐"
    await c.message.edit_text(text, reply_markup=kb([[(btn, f"pay:{l['id']}")], [("⬅️ Назад", "menu")]]))
    await c.answer()


@router.callback_query(F.data.startswith("pay:"))
async def pay(c: CallbackQuery, bot: Bot):
    l = q("SELECT * FROM lots WHERE id=?", (c.data.split(":")[1],), one=True)
    if not l or l["status"] != "active":
        return await c.answer("Лот уже недоступен", show_alert=True)
    if l["seller_id"] == c.from_user.id:
        return await c.answer("Это ваш лот", show_alert=True)
    what = "Покупка" if l["kind"] == "sale" else f"Аренда на {l['days']} дн."
    await bot.send_invoice(
        chat_id=c.from_user.id,
        title=f"{what}: @{l['username']}",
        description="Юзернейм будет передан продавцом после оплаты.",
        payload=f"lot:{l['id']}",
        currency="XTR",
        prices=[LabeledPrice(label=what, amount=l["price"])],
        provider_token="",
    )
    await c.answer()


@router.pre_checkout_query()
async def pre_checkout(p: PreCheckoutQuery):
    lot_id = p.invoice_payload.split(":")[1]
    l = q("SELECT status FROM lots WHERE id=?", (lot_id,), one=True)
    if l and l["status"] == "active":
        await p.answer(ok=True)
    else:
        await p.answer(ok=False, error_message="Лот уже занят")


@router.message(F.successful_payment)
async def paid(m: Message, bot: Bot):
    lot_id = m.successful_payment.invoice_payload.split(":")[1]
    l = q("SELECT * FROM lots WHERE id=?", (lot_id,), one=True)
    if l["kind"] == "sale":
        q("UPDATE lots SET status='sold', buyer_id=? WHERE id=?", (m.from_user.id, lot_id), commit=True)
    else:
        q("UPDATE lots SET status='rented', buyer_id=?, rent_until=datetime('now', ?) WHERE id=?",
          (m.from_user.id, f"+{l['days']} days", lot_id), commit=True)
    buyer = f"@{m.from_user.username}" if m.from_user.username else f"id{m.from_user.id}"
    await m.answer(f"✅ Оплачено! Продавец передаст вам @{l['username']} в ближайшее время.")
    await bot.send_message(
        l["seller_id"],
        f"💰 Ваш лот @{l['username']} оплачен ({l['price']}⭐).\n"
        f"Покупатель: {buyer}. Передайте юзернейм через Fragment/Telegram.")
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"💰 Сделка по лоту #{lot_id} @{l['username']}: {l['price']}⭐, покупатель {buyer}")


class Add(StatesGroup):
    username = State()
    kind = State()
    price = State()
    days = State()


@router.callback_query(F.data == "add")
async def add_start(c: CallbackQuery, state: FSMContext):
    await state.set_state(Add.username)
    await c.message.answer("Пришлите юзернейм, например <code>@vip</code>")
    await c.answer()


@router.message(Add.username)
async def add_username(m: Message, state: FSMContext):
    r = USERNAME_RE.match((m.text or "").strip())
    if not r:
        return await m.answer("Неверный формат. Пример: @vip")
    await state.update_data(username=r.group(1))
    await state.set_state(Add.kind)
    await m.answer("Что хотите?", reply_markup=kb([[("Продать", "kind:sale"), ("Сдать в аренду", "kind:rent")]]))


@router.callback_query(Add.kind, F.data.startswith("kind:"))
async def add_kind(c: CallbackQuery, state: FSMContext):
    await state.update_data(kind=c.data.split(":")[1])
    await state.set_state(Add.price)
    await c.message.answer("Цена в Stars ⭐ (целое число):")
    await c.answer()


@router.message(Add.price)
async def add_price(m: Message, state: FSMContext):
    if not (m.text or "").isdigit() or int(m.text) <= 0:
        return await m.answer("Введите целое число больше 0")
    await state.update_data(price=int(m.text))
    d = await state.get_data()
    if d["kind"] == "rent":
        await state.set_state(Add.days)
        return await m.answer("На сколько дней аренда?")
    await finish_add(m, state, 0)


@router.message(Add.days)
async def add_days(m: Message, state: FSMContext):
    if not (m.text or "").isdigit() or int(m.text) <= 0:
        return await m.answer("Введите число дней")
    await finish_add(m, state, int(m.text))


async def finish_add(m: Message, state: FSMContext, days: int):
    d = await state.get_data()
    await state.clear()
    name = m.from_user.username or str(m.from_user.id)
    lot_id = q("INSERT INTO lots(seller_id, seller_name, username, kind, price, days) VALUES(?,?,?,?,?,?)",
               (m.from_user.id, name, d["username"], d["kind"], d["price"], days), commit=True)
    await m.answer("Лот отправлен на модерацию ✅", reply_markup=MAIN)
    if ADMIN_ID:
        await m.bot.send_message(
            ADMIN_ID,
            f"Новый лот #{lot_id}: @{d['username']} ({d['kind']}, {d['price']}⭐, {days} дн.)\n"
            f"Продавец: {html.escape(name)}",
            reply_markup=kb([[("✅ Одобрить", f"adm:ok:{lot_id}"), ("❌ Отклонить", f"adm:no:{lot_id}")]]))


@router.callback_query(F.data.startswith("adm:"))
async def admin(c: CallbackQuery, bot: Bot):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("Нет доступа", show_alert=True)
    _, act, lot_id = c.data.split(":")
    status = "active" if act == "ok" else "rejected"
    q("UPDATE lots SET status=? WHERE id=?", (status, lot_id), commit=True)
    l = q("SELECT * FROM lots WHERE id=?", (lot_id,), one=True)
    await c.message.edit_text(f"Лот #{lot_id} @{l['username']}: {status}")
    await bot.send_message(l["seller_id"], f"Ваш лот @{l['username']}: {'одобрен ✅' if act == 'ok' else 'отклонён ❌'}")
    await c.answer()


@router.callback_query(F.data == "my")
async def my(c: CallbackQuery):
    lots = q("SELECT * FROM lots WHERE seller_id=? ORDER BY id DESC LIMIT 20", (c.from_user.id,))
    text = "📦 <b>Мои лоты</b>\n\n" + ("\n".join(
        f"@{l['username']} — {l['kind']}, {l['price']}⭐, {l['status']}" for l in lots) or "Пусто")
    await c.message.edit_text(text, reply_markup=kb([[("⬅️ Назад", "menu")]]))
    await c.answer()


async def rent_watcher():
    while True:
        q("UPDATE lots SET status='active', buyer_id=NULL, rent_until=NULL "
          "WHERE status='rented' AND rent_until <= datetime('now')", commit=True)
        await asyncio.sleep(3600)


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(rent_watcher())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
