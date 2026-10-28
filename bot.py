import os
import sqlite3
import asyncio
import logging
import time
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "9096866450:AAFWeA7PUmEjKiMj5v2s-PthSPCafbBUowE"
ADMIN_IDS = [1289964116]  # Ваши ID админов

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
DB_NAME = "shop.db"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


# ==================== БАЗА ДАННЫХ (SQLite) ====================
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                photo_path TEXT NOT NULL,
                photo_file_id TEXT,
                in_stock INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cart (
                user_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, product_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                customer_name TEXT,
                phone TEXT,
                delivery_type TEXT,
                address TEXT,
                delivery_datetime TEXT,
                items_text TEXT,
                total_price INTEGER,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(query, params)
        if commit:
            conn.commit()
            return c.lastrowid
        if fetchone:
            row = c.fetchone()
            return dict(row) if row else None
        if fetchall:
            return [dict(r) for r in c.fetchall()]


# ==================== СОСТОЯНИЯ (FSM) ====================
class AdminStates(StatesGroup):
    add_name = State()
    add_desc = State()
    add_price = State()
    add_photo = State()
    edit_price = State()


class OrderStates(StatesGroup):
    name = State()
    phone = State()
    delivery_type = State()
    address = State()
    datetime = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_cart_summary(user_id: int):
    items = db_query("""
        SELECT c.quantity, p.id, p.name, p.price 
        FROM cart c 
        JOIN products p ON c.product_id = p.id 
        WHERE c.user_id = ? AND p.in_stock = 1
    """, (user_id,), fetchall=True)
    total_qty = sum(i["quantity"] for i in items)
    total_price = sum(i["quantity"] * i["price"] for i in items)
    return items, total_qty, total_price


# ==================== КЛИЕНТСКОЕ МЕНЮ И КАТАЛОГ ====================
def get_main_menu_kb(user_id: int):
    _, total_qty, total_price = get_cart_summary(user_id)
    cart_text = f"🛒 Корзина ({total_qty} шт. | {total_price} ₽)" if total_qty > 0 else "🛒 Корзина (пусто)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌸 Каталог букетов", callback_data="client_catalog")],
        [InlineKeyboardButton(text=cart_text, callback_data="client_cart")]
    ])
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Добро пожаловать в магазин «Цветы Белгород»! 💐\n\n"
        "Выберите свежие букеты в нашем каталоге, и мы доставим их к нужному времени:",
        reply_markup=get_main_menu_kb(message.from_user.id),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "client_home")
async def cb_client_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "Добро пожаловать в магазин «Цветы Белгород»! 💐\n\n"
        "Выберите свежие букеты в нашем каталоге, и мы доставим их к нужному времени:"
    )
    if call.message.photo:
        await call.message.delete()
        await call.message.answer(text, reply_markup=get_main_menu_kb(call.from_user.id), parse_mode="Markdown")
    else:
        await call.message.edit_text(text, reply_markup=get_main_menu_kb(call.from_user.id), parse_mode="Markdown")


@router.callback_query(F.data == "client_catalog")
async def cb_client_catalog(call: CallbackQuery):
    products = db_query("SELECT * FROM products WHERE in_stock = 1 ORDER BY id DESC", fetchall=True)
    if not products:
        return await call.answer("Витрина обновляется! Загляните чуть позже 🌸", show_alert=True)

    builder = InlineKeyboardBuilder()
    for p in products:
        builder.row(InlineKeyboardButton(
            text=f"💐 {p['name']} — {p['price']} ₽",
            callback_data=f"view_item_{p['id']}"
        ))
    _, total_qty, total_price = get_cart_summary(call.from_user.id)
    if total_qty > 0:
        builder.row(InlineKeyboardButton(text=f"🛒 Перейти в корзину ({total_price} ₽)", callback_data="client_cart"))
    builder.row(InlineKeyboardButton(text="⬅️ На главную", callback_data="client_home"))

    text = "🌸 Выберите букет, чтобы посмотреть фото и описание:"
    if call.message.photo:
        await call.message.delete()
        await call.message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("view_item_"))
async def cb_view_item(call: CallbackQuery):
    prod_id = int(call.data.split("_")[2])
    p = db_query("SELECT * FROM products WHERE id = ? AND in_stock = 1", (prod_id,), fetchone=True)
    if not p:
        return await call.answer("Этот букет уже раскупили!", show_alert=True)

    cart_item = db_query("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), fetchone=True)
    qty = cart_item["quantity"] if cart_item else 0

    builder = InlineKeyboardBuilder()
    if qty == 0:
        builder.row(InlineKeyboardButton(text="➕ Добавить в корзину", callback_data=f"cart_add_{prod_id}"))
    else:
        builder.row(
            InlineKeyboardButton(text="➖", callback_data=f"cart_sub_{prod_id}"),
            InlineKeyboardButton(text=f"В корзине: {qty} шт.", callback_data="ignore"),
            InlineKeyboardButton(text="➕", callback_data=f"cart_add_{prod_id}")
        )
    builder.row(InlineKeyboardButton(text="🛒 Перейти в корзину", callback_data="client_cart"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад к букетам", callback_data="client_catalog"))

    caption = (
        f"💐 {p['name']}\n\n"
        f"{p['description']}\n\n"
        f"💰 Цена: {p['price']} ₽"
    )

    await call.message.delete()
    if p.get("photo_file_id"):
        await call.message.answer_photo(photo=p["photo_file_id"], caption=caption, reply_markup=builder.as_markup(), parse_mode="Markdown")
    elif os.path.exists(p["photo_path"]):
        await call.message.answer_photo(photo=FSInputFile(p["photo_path"]), caption=caption, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await call.message.answer(caption, reply_markup=builder.as_markup(), parse_mode="Markdown")
@router.callback_query(F.data == "ignore")
async def cb_ignore(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("cart_add_"))
async def cb_cart_add(call: CallbackQuery):
    prod_id = int(call.data.split("_")[2])
    existing = db_query("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), fetchone=True)
    if existing:
        db_query("UPDATE cart SET quantity = quantity + 1 WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), commit=True)
    else:
        db_query("INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, 1)", (call.from_user.id, prod_id), commit=True)
    await call.answer("Добавлено в корзину! 💐")
    await cb_view_item(call)


@router.callback_query(F.data.startswith("cart_sub_"))
async def cb_cart_sub(call: CallbackQuery):
    prod_id = int(call.data.split("_")[2])
    existing = db_query("SELECT quantity FROM cart WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), fetchone=True)
    if existing:
        if existing["quantity"] > 1:
            db_query("UPDATE cart SET quantity = quantity - 1 WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), commit=True)
        else:
            db_query("DELETE FROM cart WHERE user_id = ? AND product_id = ?", (call.from_user.id, prod_id), commit=True)
    await call.answer("Количество изменено")
    await cb_view_item(call)


# ==================== КОРЗИНА И ОФОРМЛЕНИЕ ЗАКАЗА ====================
@router.callback_query(F.data == "client_cart")
async def cb_client_cart(call: CallbackQuery):
    items, total_qty, total_price = get_cart_summary(call.from_user.id)
    if not items:
        return await call.answer("Ваша корзина пока пуста 🛒", show_alert=True)

    lines = [f"• {i['name']} x{i['quantity']} — {i['price'] * i['quantity']} ₽" for i in items]
    text = (
        "🛒 Ваша корзина:\n\n" +
        "\n".join(lines) +
        f"\n\n💰 Итого к оплате: {total_price} ₽"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout_start")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="cart_clear")],
        [InlineKeyboardButton(text="🌸 Продолжить выбор", callback_data="client_catalog")]
    ])

    if call.message.photo:
        await call.message.delete()
        await call.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data == "cart_clear")
async def cb_cart_clear(call: CallbackQuery, state: FSMContext):
    db_query("DELETE FROM cart WHERE user_id = ?", (call.from_user.id,), commit=True)
    await call.answer("Корзина очищена", show_alert=True)
    await cb_client_home(call, state)


@router.callback_query(F.data == "checkout_start")
async def cb_checkout_start(call: CallbackQuery, state: FSMContext):
    items, _, _ = get_cart_summary(call.from_user.id)
    if not items:
        return await call.answer("Корзина пуста!", show_alert=True)

    await state.set_state(OrderStates.name)
    await call.message.edit_text(
        "📝 Оформление заказа (Шаг 1 из 4)\n\n"
        "Введите ваше имя:",
        parse_mode="Markdown"
    )


@router.message(OrderStates.name)
async def step_order_name(message: Message, state: FSMContext):
    await state.update_data(customer_name=message.text.strip())
    await state.set_state(OrderStates.phone)
    await message.answer(
        "📞 Шаг 2 из 4\n\n"
        "Введите ваш номер телефона для связи (например, +79990000000):",
        parse_mode="Markdown"
    )


@router.message(OrderStates.phone)
async def step_order_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await state.set_state(OrderStates.delivery_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚗 Доставка по Белгороду", callback_data="deliv_courier")],
        [InlineKeyboardButton(text="🏪 Самовывоз из магазина", callback_data="deliv_pickup")]
    ])
    await message.answer(
        "🚚 Шаг 3 из 4\n\n"
        "Выберите способ получения букета:",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(OrderStates.delivery_type, F.data.in_(["deliv_courier", "deliv_pickup"]))
async def step_order_delivery_type(call: CallbackQuery, state: FSMContext):
    if call.data == "deliv_courier":
        await state.update_data(delivery_type="Доставка по Белгороду")
        await state.set_state(OrderStates.address)
        await call.message.edit_text(
            "📍 Укажите точный адрес доставки в Белгороде (улица, дом, квартира/офис):",
            parse_mode="Markdown"
        )
    else:
        await state.update_data(delivery_type="Самовывоз", address="Самовывоз")
        await state.set_state(OrderStates.datetime)
        await call.message.edit_text(
            "⏰ Шаг 4 из 4\n\n"
            "Напишите дату и время, когда вы заберете букет (например: Сегодня к 18:30 или 29 октября к 14:00):",
            parse_mode="Markdown"
        )


@router.message(OrderStates.address)
async def step_order_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text.strip())
    await state.set_state(OrderStates.datetime)
    await message.answer(
        "⏰ Шаг 4 из 4\n\n"
        "Напишите дату и время доставки (например: Сегодня к 18:30 или 29 октября с 12:00 до 14:00):",
        parse_mode="Markdown"
    )


@router.message(OrderStates.datetime)
async def step_order_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    delivery_datetime = message.text.strip()
    user = message.from_user

    items, _, total_price = get_cart_summary(user.id)
    if not items:
        await state.clear()
        return await message.answer("Ваша корзина пуста.", reply_markup=get_main_menu_kb(user.id))

    items_text = "\n".join([f"• {i['name']} x{i['quantity']} ({i['price'] * i['quantity']} ₽)" for i in items])
    username = user.username if user.username else "нет_юзернейма"
    address = data.get("address", "Самовывоз")

    order_id = db_query(
        """INSERT INTO orders 
           (user_id, username, customer_name, phone, delivery_type, address, delivery_datetime, items_text, total_price) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user.id, username, data["customer_name"],
            data["phone"], data["delivery_type"], address,
            delivery_datetime, items_text, total_price
        ),
        commit=True
    )

    db_query("DELETE FROM cart WHERE user_id = ?", (user.id,), commit=True)
    await state.clear()

    user_display = f"@{username}" if username != "нет_юзернейма" else f"<a href='tg://user?id={user.id}'>Открыть профиль</a>"
    admin_msg = (
        f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_id}!</b>\n\n"
        f"👤 <b>Клиент:</b> {data['customer_name']}\n"
        f"📱 <b>Telegram:</b> {user_display} (ID: <code>{user.id}</code>)\n"
        f"📞 <b>Телефон:</b> <code>{data['phone']}</code>\n"
        f"🚚 <b>Способ:</b> {data['delivery_type']}\n"
        f"📍 <b>Адрес:</b> {address}\n"
        f"⏰ <b>Дата и время:</b> {delivery_datetime}\n\n"
        f"💐 <b>Букеты:</b>\n{items_text}\n\n"
        f"💰 <b>Сумма заказа: {total_price} ₽</b>"
    )
    adm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить выполненным", callback_data=f"ord_done_{order_id}")]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_msg, parse_mode="HTML", reply_markup=adm_kb)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
            await message.answer(
        f"✅ <b>Спасибо! Ваша заявка #{order_id} успешно принята!</b>\n\n"
        f"💐 <b>Состав заказа:</b>\n{items_text}\n"
        f"💰 <b>Сумма:</b> {total_price} ₽\n\n"
        f"Администратор магазина «Цветы Белгород» свяжется с вами в ближайшее время для подтверждения.",
        parse_mode="HTML",
        reply_markup=get_main_menu_kb(user.id)
    )


# ==================== ПАНЕЛЬ АДМИНИСТРАТОРА (/admin) ====================
def get_admin_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить букет", callback_data="adm_add")],
        [InlineKeyboardButton(text="💐 Управление каталогом", callback_data="adm_catalog")],
        [InlineKeyboardButton(text="📦 Активные заказы", callback_data="adm_orders")]
    ])


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🔧 Панель администратора «Цветы Белгород»", reply_markup=get_admin_main_kb(), parse_mode="Markdown")


@router.callback_query(F.data == "adm_menu")
async def cb_admin_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.clear()
    await call.message.edit_text("🔧 Панель администратора «Цветы Белгород»", reply_markup=get_admin_main_kb(), parse_mode="Markdown")


@router.callback_query(F.data == "adm_add")
async def cb_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminStates.add_name)
    await call.message.edit_text("Введите название букета:", parse_mode="Markdown")


@router.message(AdminStates.add_name)
async def step_add_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminStates.add_desc)
    await message.answer("Введите описание букета (состав, упаковка):")


@router.message(AdminStates.add_desc)
async def step_add_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(AdminStates.add_price)
    await message.answer("Введите цену букета в рублях (только цифры, например 3500):", parse_mode="Markdown")


@router.message(AdminStates.add_price)
async def step_add_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введите цену числом (без пробелов и символов):")
    await state.update_data(price=int(message.text))
    await state.set_state(AdminStates.add_photo)
    await message.answer("Отправьте фотографию букета:")


@router.message(AdminStates.add_photo, F.photo)
async def step_add_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    filename = f"bouquet_{int(time.time())}_{photo.file_unique_id}.jpg"
    filepath = os.path.join(UPLOAD_DIR, filename)
    await bot.download_file(file_info.file_path, filepath)

    db_query(
        "INSERT INTO products (name, description, price, photo_path, photo_file_id, in_stock) VALUES (?, ?, ?, ?, ?, 1)",
        (data["name"], data["description"], data["price"], filepath, photo.file_id),
        commit=True
    )
    await state.clear()
    await message.answer(
        f"✅ Букет «{data['name']}» ({data['price']} ₽) сохранен на сервере и добавлен в каталог!",
        reply_markup=get_admin_main_kb(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "adm_catalog")
async def cb_catalog(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True)
    if not products:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_menu")]])
        return await call.message.edit_text("Каталог пока пуст.", reply_markup=kb)
    builder = InlineKeyboardBuilder()
    for p in products:
        status_icon = "✅" if p["in_stock"] else "🚫"
        builder.row(InlineKeyboardButton(
            text=f"{status_icon} {p['name']} — {p['price']} ₽",
            callback_data=f"prod_view_{p['id']}"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="adm_menu"))

    if call.message.photo:
        await call.message.delete()
        await call.message.answer("Выберите букет для редактирования:", reply_markup=builder.as_markup())
    else:
        await call.message.edit_text("Выберите букет для редактирования:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("prod_view_"))
async def cb_prod_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    prod_id = int(call.data.split("_")[2])
    p = db_query("SELECT * FROM products WHERE id = ?", (prod_id,), fetchone=True)
    if not p:
        return await call.answer("Товар не найден", show_alert=True)

    status_text = "✅ В наличии" if p["in_stock"] else "🚫 Нет в наличии (скрыт от клиентов)"
    toggle_btn_text = "🚫 Скрыть («Нет в наличии»)" if p["in_stock"] else "✅ Вернуть в продажу"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_btn_text, callback_data=f"prod_toggle_{p['id']}")],
        [InlineKeyboardButton(text="✏️ Изменить цену", callback_data=f"prod_price_{p['id']}")],
        [InlineKeyboardButton(text="🗑 Удалить букет", callback_data=f"prod_del_{p['id']}")],
        [InlineKeyboardButton(text="⬅️ К списку букетов", callback_data="adm_catalog")]
    ])

    caption = (
        f"💐 {p['name']}\n\n"
        f"📝 Описание: {p['description']}\n"
        f"💰 Цена: {p['price']} ₽\n"
        f"Статус: {status_text}"
    )

    await call.message.delete()
    if p.get("photo_file_id"):
        await call.message.answer_photo(photo=p["photo_file_id"], caption=caption, reply_markup=kb, parse_mode="Markdown")
    elif os.path.exists(p["photo_path"]):
        await call.message.answer_photo(photo=FSInputFile(p["photo_path"]), caption=caption, reply_markup=kb, parse_mode="Markdown")
    else:
        await call.message.answer(caption, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("prod_toggle_"))
async def cb_prod_toggle(call: CallbackQuery):
    prod_id = int(call.data.split("_")[2])
    p = db_query("SELECT in_stock FROM products WHERE id = ?", (prod_id,), fetchone=True)
    if p:
        new_status = 0 if p["in_stock"] else 1
        db_query("UPDATE products SET in_stock = ? WHERE id = ?", (new_status, prod_id), commit=True)
    await cb_prod_view(call)


@router.callback_query(F.data.startswith("prod_del_"))
async def cb_prod_del(call: CallbackQuery):
    prod_id = int(call.data.split("_")[2])
    p = db_query("SELECT photo_path FROM products WHERE id = ?", (prod_id,), fetchone=True)
    if p and os.path.exists(p["photo_path"]):
        os.remove(p["photo_path"])
    db_query("DELETE FROM products WHERE id = ?", (prod_id,), commit=True)
    db_query("DELETE FROM cart WHERE product_id = ?", (prod_id,), commit=True)
    await call.answer("Букет удален!", show_alert=True)
    await cb_catalog(call)


@router.callback_query(F.data.startswith("prod_price_"))
async def cb_prod_price(call: CallbackQuery, state: FSMContext):
    prod_id = int(call.data.split("_")[2])
    await state.update_data(edit_prod_id=prod_id)
    await state.set_state(AdminStates.edit_price)
    await call.message.delete()
    await call.message.answer("Введите новую цену букета в рублях (только цифры):", parse_mode="Markdown")


@router.message(AdminStates.edit_price)
async def step_edit_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Введите корректное число:")
    data = await state.get_data()
    db_query("UPDATE products SET price = ? WHERE id = ?", (int(message.text), data["edit_prod_id"]), commit=True)
    await state.clear()
    await message.answer("✅ Цена успешно обновлена!", reply_markup=get_admin_main_kb())


@router.callback_query(F.data == "adm_orders")
async def cb_orders(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    orders = db_query("SELECT * FROM orders WHERE status = 'active' ORDER BY id DESC", fetchall=True)
    if not orders:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_menu")]])
        return await call.message.edit_text("Активных заказов сейчас нет.", reply_markup=kb)

    builder = InlineKeyboardBuilder()
    for o in orders:
        builder.row(InlineKeyboardButton(
            text=f"Заказ #{o['id']} | {o['customer_name']} ({o['total_price']} ₽)",
            callback_data=f"ord_view_{o['id']}"
        ))
    builder.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="adm_menu"))
    await call.message.edit_text("📦 Список активных заказов:", reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith("ord_view_"))
async def cb_order_view(call: CallbackQuery):
    ord_id = int(call.data.split("_")[2])
    o = db_query("SELECT * FROM orders WHERE id = ?", (ord_id,), fetchone=True)
    if not o:
        return await call.answer("Заказ не найден", show_alert=True)

    user_link = f"@{o['username']}" if o["username"] != "нет_юзернейма" else f"[Профиль](tg://user?id={o['user_id']})"
    text = (
        f"📦 Заказ #{o['id']}\n\n"
        f"👤 Клиент: {o['customer_name']} ({user_link} | ID: {o['user_id']})\n"
        f"📞 Телефон: {o['phone']}\n"
        f"🚚 Способ получения: {o['delivery_type']}\n"
        f"📍 Адрес: {o['address']}\n"
        f"⏰ Дата и время: {o['delivery_datetime']}\n\n"
        f"💐 Состав заказа:\n{o['items_text']}\n\n"
        f"💰 Итого: {o['total_price']} ₽"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить (выполнен)", callback_data=f"ord_done_{o['id']}")],
        [InlineKeyboardButton(text="⬅️ К списку заказов", callback_data="adm_orders")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


@router.callback_query(F.data.startswith("ord_done_"))
async def cb_order_done(call: CallbackQuery):
    ord_id = int(call.data.split("_")[2])
    db_query("UPDATE orders SET status = 'completed' WHERE id = ?", (ord_id,), commit=True)
    await call.answer(f"Заказ #{ord_id} завершен!", show_alert=True)
    await cb_orders(call)


# --- ЗАГЛУШКА ВЕБ-СЕРВЕРА ДЛЯ RENDER WEB SERVICE ---
async def handle_ping(request):
    return web.Response(text="Bot is running!")


async def main():
    init_db()

    # Запускаем легкий веб-сервер на порту, который требует Render (порт берется из системной переменной PORT)
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Веб-сервер запущен на порту {port} для прохождения проверки Render.")

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот «Цветы Белгород» успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
