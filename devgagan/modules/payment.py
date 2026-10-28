# ---------------------------------------------------
# File Name: payment.py
# Description: UPI Payment System via ZapUPI
# Plans: 7d/₹30 | 15d/₹50 | 30d/₹100 | 60d/₹200 | 90d/₹300
# API: POST https://pay.zapupi.com/api/create-order
# ---------------------------------------------------

import datetime
import time
import aiohttp
import pytz
from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from devgagan import app
from config import OWNER_ID, ZAPUPI_TOKEN
from devgagan.core.mongo import plans_db
from devgagan.core.mongo.payments_db import (
    create_payment_record,
    get_payment_record,
    get_pending_payment_by_user,
    mark_payment_done,
    mark_payment_failed,
)

# ── Plan definitions ───────────────────────────────────────────────────────────
PLANS = {
    "7":  {"days": 7,  "amount": 30,  "label": "7 Days"},
    "15": {"days": 15, "amount": 50,  "label": "15 Days"},
    "30": {"days": 30, "amount": 100, "label": "30 Days"},
    "60": {"days": 60, "amount": 200, "label": "60 Days"},
    "90": {"days": 90, "amount": 300, "label": "90 Days"},
}

IST = pytz.timezone("Asia/Kolkata")
BOT_URL = "http://t.me/Mjolnir369bot"


async def _private_payment_callback(callback: CallbackQuery) -> bool:
    """Reject payment buttons outside the private chat of the clicking user."""
    message = callback.message
    if (
        message
        and message.chat.type == ChatType.PRIVATE
        and message.chat.id == callback.from_user.id
    ):
        return True
    await callback.answer("For privacy, payment buttons work only in your private chat with the bot.", show_alert=True)
    return False

# ── ZapUPI API helpers ─────────────────────────────────────────────────────────

async def zapupi_create_order(order_id: str, amount: int, remark: str) -> dict:
    """
    Create an order via ZapUPI official API.
    POST https://pay.zapupi.com/api/create-order
    Auth: zap_key in POST body (no Authorization header).
    Success response: {"status":"success","payment_url":"https://pay.zapupi.com/instent-pay-..."}
    """
    payload = {
        "zap_key":  ZAPUPI_TOKEN,
        "order_id": order_id,
        "amount":   str(amount),
        "remark":   remark,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://pay.zapupi.com/api/create-order",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    url = data.get("payment_url", "")
                    if data.get("status") == "success" and url:
                        return {"success": True, "payment_url": url}
                    err = data.get("message") or data.get("error") or str(data)
                    return {"success": False, "error": err}
                return {"success": False, "error": f"Bad response: {str(data)[:100]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


async def zapupi_check_order(order_id: str) -> dict:
    """
    Check payment status via ZapUPI official order-status API.
    POST https://pay.zapupi.com/api/order-status
    Response: {"status":"success","data":{"status":"Success"|"Failed"|"Pending"}}
    """
    payload = {
        "zap_key":  ZAPUPI_TOKEN,
        "order_id": order_id,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://pay.zapupi.com/api/order-status",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    if data.get("status") == "success":
                        order_data = data.get("data", {})
                        raw = str(order_data.get("status", "")).upper()
                        if raw in ("SUCCESS", "PAID", "COMPLETED"):
                            return {"status": "SUCCESS"}
                        elif raw in ("FAILED", "FAILURE", "DECLINED"):
                            return {"status": "FAILED"}
                        else:
                            return {"status": "PENDING"}
                    return {"status": "ERROR", "error": data.get("message", str(data))}
                return {"status": "PENDING"}
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


def make_order_id(user_id: int, plan_days: int) -> str:
    """Generate a unique order ID for tracking."""
    ts = int(time.time())
    return f"TG{user_id}P{plan_days}T{ts}"


# ── Shared: show plan selection ────────────────────────────────────────────────

def _plans_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 7 Days — ₹30",   callback_data="plan_7"),
            InlineKeyboardButton("📅 15 Days — ₹50",  callback_data="plan_15"),
        ],
        [
            InlineKeyboardButton("📅 30 Days — ₹100", callback_data="plan_30"),
            InlineKeyboardButton("📅 60 Days — ₹200", callback_data="plan_60"),
        ],
        [
            InlineKeyboardButton("📅 90 Days — ₹300", callback_data="plan_90"),
        ],
    ])


def _plans_text():
    return (
        "💎 **Premium Subscription**\n\n"
        "🚀 **Premium Benefits:**\n"
        "✅ Unlimited file saves\n"
        "✅ Batch download (50 files)\n"
        "✅ No 120s waiting time\n"
        "✅ Priority support\n\n"
        "🗓️ **Select your plan:**\n\n"
        "📅 7 Days — ₹30\n"
        "📅 15 Days — ₹50\n"
        "📅 30 Days — ₹100\n"
        "📅 60 Days — ₹200\n"
        "📅 90 Days — ₹300"
    )


# ── /pay command ───────────────────────────────────────────────────────────────

@app.on_message(filters.command("pay") & filters.private)
async def pay_command(client, message):
    user_id = message.from_user.id

    data = await plans_db.check_premium(user_id)
    if data and data.get("expire_date"):
        exp = data["expire_date"].astimezone(IST)
        exp_str = exp.strftime("%d-%m-%Y %I:%M %p")
        await message.reply_text(
            f"✅ **You are already a Premium member!**\n\n"
            f"⌛ Expiry: `{exp_str}` IST\n\n"
            f"Use /myplan to view your plan details."
        )
        return

    await message.reply_text(_plans_text(), reply_markup=_plans_keyboard())


# ── "Get Premium" button from /start ──────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^get_premium_btn$"))
async def get_premium_btn(client, callback: CallbackQuery):
    if not await _private_payment_callback(callback):
        return
    await callback.answer()
    user_id = callback.from_user.id

    data = await plans_db.check_premium(user_id)
    if data and data.get("expire_date"):
        exp = data["expire_date"].astimezone(IST)
        exp_str = exp.strftime("%d-%m-%Y %I:%M %p")
        await callback.message.reply_text(
            f"✅ **You are already a Premium member!**\n\n"
            f"⌛ Expiry: `{exp_str}` IST\n\n"
            f"Use /myplan to view your plan details."
        )
        return

    await callback.message.reply_text(_plans_text(), reply_markup=_plans_keyboard())


# ── Plan selection callback ────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^plan_(7|15|30|60|90)$"))
async def plan_selected(client, callback: CallbackQuery):
    if not await _private_payment_callback(callback):
        return
    plan_key = callback.data.split("_")[1]
    plan = PLANS[plan_key]
    await callback.answer()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 Pay via UPI", callback_data=f"pay_upi_{plan_key}")],
        [InlineKeyboardButton("« Back", callback_data="back_plans")],
    ])

    await callback.message.edit_text(
        f"💎 **Premium Subscription**\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"📦 **Selected Plan:** {plan['label']}\n"
        f"💰 **Amount:** ₹{plan['amount']}\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"Click below to proceed with UPI payment:",
        reply_markup=keyboard
    )


# ── Back to plans ──────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^back_plans$"))
async def back_to_plans(client, callback: CallbackQuery):
    if not await _private_payment_callback(callback):
        return
    await callback.answer()
    await callback.message.edit_text(_plans_text(), reply_markup=_plans_keyboard())


# ── UPI Payment initiation ─────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^pay_upi_(7|15|30|60|90)$"))
async def pay_upi(client, callback: CallbackQuery):
    if not await _private_payment_callback(callback):
        return
    if not ZAPUPI_TOKEN:
        await callback.answer("Payment service is not configured. Please contact support.", show_alert=True)
        return
    plan_key = callback.data.split("_")[2]
    plan = PLANS[plan_key]
    user_id = callback.from_user.id

    await callback.answer("⏳ Generating payment link...")
    loading_msg = await callback.message.edit_text("⏳ **Generating payment link, please wait...**")

    order_id = make_order_id(user_id, plan["days"])
    remark = f"Bot Premium {plan['days']} Days"

    result = await zapupi_create_order(order_id, plan["amount"], remark)

    if result.get("success"):
        payment_url = result["payment_url"]
        # Save pending payment to MongoDB
        await create_payment_record(order_id, user_id, plan["days"], plan["amount"])

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇮🇳 Pay with UPI", url=payment_url)],
            [InlineKeyboardButton("✅ Verify Payment", callback_data=f"verify_{order_id}")],
            [InlineKeyboardButton("🎧 Need support", url="https://t.me/Pre_contact_bot")],
            [InlineKeyboardButton("« Back to Payment Methods", callback_data="back_plans")],
        ])
        await loading_msg.edit_text(
            f"📋 **Order Summary:**\n"
            f"• **Service:** Premium Access\n"
            f"• **Duration:** {plan['label']}\n"
            f"• **Final Price:** ₹{plan['amount']}\n\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔖 **Order ID:** `{order_id}`\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"**How to pay:**\n"
            f"1️⃣ Tap **\"Pay with UPI\"** below\n"
            f"2️⃣ Complete the payment of ₹{plan['amount']}\n"
            f"3️⃣ Return here and tap **\"Verify Payment\"**\n\n"
            f"✅ Payment will be verified automatically!",
            reply_markup=keyboard
        )
    else:
        err_msg = result.get("error", "Unknown error")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Try Again", callback_data=f"pay_upi_{plan_key}")],
            [InlineKeyboardButton("🎧 Support", url="https://t.me/Pre_contact_bot")],
        ])
        await loading_msg.edit_text(
            f"❌ **Could not generate payment link.**\n\n"
            f"📦 Plan: {plan['label']} — ₹{plan['amount']}\n\n"
            f"_Error: {err_msg[:120]}_\n\n"
            f"Please try again in a moment.",
            reply_markup=keyboard
        )


# ── Premium activation helper ─────────────────────────────────────────────────

async def _activate_premium(client, tg_user, record, order_id, message=None):
    """Activate premium for user and notify both user and owner."""
    user_id = tg_user.id
    plan_days = record["plan_days"]
    expire_date = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=plan_days)
    await plans_db.add_premium(user_id, expire_date)
    await mark_payment_done(order_id, user_id)
    expire_ist = expire_date.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")

    text = (
        f"🎉 **Payment Successful!**\n\n"
        f"✅ Your {plan_days}-day Premium plan is now active!\n\n"
        f"⌛ **Expiry:** `{expire_ist}` IST\n\n"
        f"🚀 You can now enjoy all premium features!\n"
        f"Use /myplan to view your plan details."
    )
    if message:
        try:
            await message.edit_text(text)
        except Exception:
            try:
                await client.send_message(user_id, text)
            except Exception:
                pass
    else:
        try:
            await client.send_message(user_id, text)
        except Exception:
            pass

    # Notify owner
    try:
        name = f"{tg_user.first_name} {tg_user.last_name or ''}".strip()
        for owner in OWNER_ID:
            await client.send_message(
                owner,
                f"✅ **Payment Confirmed!**\n\n"
                f"👤 User: [{name}](tg://user?id={user_id})\n"
                f"🆔 ID: `{user_id}`\n"
                f"📦 Plan: {plan_days} Days\n"
                f"🔖 Order: `{order_id}`\n"
                f"⌛ Expiry: {expire_ist}"
            )
    except Exception:
        pass


# ── Payment Verification ───────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^verify_(.+)$"))
async def verify_payment(client, callback: CallbackQuery):
    if not await _private_payment_callback(callback):
        return
    order_id = callback.data[len("verify_"):]
    user_id = callback.from_user.id

    record = await get_payment_record(order_id, user_id)
    if not record or record.get("user_id") != user_id:
        await callback.answer(
            "❌ This payment could not be found for your account. Use /status or /pay.",
            show_alert=True
        )
        return

    await callback.answer("⏳ Verifying payment...")

    if record.get("status") == "COMPLETED":
        await callback.answer("✅ This payment has already been verified!", show_alert=True)
        return

    status_result = await zapupi_check_order(order_id)
    payment_status = status_result.get("status", "PENDING")

    if payment_status == "SUCCESS":
        await _activate_premium(client, callback.from_user, record, order_id, callback.message)

    elif payment_status == "FAILED":
        await mark_payment_failed(order_id, user_id)
        await callback.answer(
            "❌ Payment failed. Please use /pay to try again.",
            show_alert=True
        )

    else:
        # Still pending — ask user to wait
        await callback.answer(
            "⏳ Payment is still pending. Please wait 1-2 minutes after paying, then tap Verify again.",
            show_alert=True
        )


# ── /status command ────────────────────────────────────────────────────────────

@app.on_message(filters.command("status") & filters.private)
async def payment_status_cmd(client, message):
    user_id = message.from_user.id
    record = await get_pending_payment_by_user(user_id)

    if not record:
        await message.reply_text(
            "❌ No pending payment found.\nUse /pay to get a subscription."
        )
        return

    order_id = record["order_id"]
    plan_days = record["plan_days"]
    amount = record["amount"]

    status_result = await zapupi_check_order(order_id)
    payment_status = status_result.get("status", "PENDING")

    if payment_status == "SUCCESS":
        if record.get("status") != "COMPLETED":
            try:
                tg_user = await client.get_users(user_id)
            except Exception:
                from types import SimpleNamespace
                tg_user = SimpleNamespace(id=user_id, first_name="User", last_name="")
            await _activate_premium(client, tg_user, record, order_id, message=None)
            await message.reply_text("🎉 Payment verified! Use /myplan to view your plan.")
        else:
            await message.reply_text("✅ Your payment was already verified. Use /myplan to view details.")
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Verify Payment", callback_data=f"verify_{order_id}")],
        ])
        await message.reply_text(
            f"⏳ **Payment Pending**\n\n"
            f"🔖 Order ID: `{order_id}`\n"
            f"💰 Amount: ₹{amount}\n"
            f"📦 Plan: {plan_days} Days\n\n"
            f"Complete your payment and tap **Verify Payment**.\n"
            f"Premium will activate automatically once payment is confirmed.",
            reply_markup=keyboard
        )


# ── Owner: manual add premium ─────────────────────────────────────────────────

@app.on_message(filters.command("addpremium") & filters.user(OWNER_ID))
async def add_premium_cmd(client, message):
    """
    Usage: /addpremium <user_id> <days>
    Owner manually adds premium to a user.
    Example: /addpremium 123456789 30
    """
    args = message.command
    if len(args) < 3:
        await message.reply_text("Usage: /addpremium user_id days\nExample: /addpremium 123456789 30")
        return

    try:
        target_id = int(args[1])
        days = int(args[2])
    except ValueError:
        await message.reply_text("❌ Invalid user_id or days. Please use numbers only.")
        return

    expire_date = datetime.datetime.now(pytz.utc) + datetime.timedelta(days=days)
    await plans_db.add_premium(target_id, expire_date)

    expire_ist = expire_date.astimezone(IST).strftime("%d-%m-%Y %I:%M %p")
    await message.reply_text(
        f"✅ **Premium Added!**\n\n"
        f"👤 User ID: `{target_id}`\n"
        f"📦 Plan: {days} Days\n"
        f"⌛ Expiry: {expire_ist} IST"
    )

    try:
        await client.send_message(
            target_id,
            f"🎉 **Your Premium plan has been activated!**\n\n"
            f"📦 Plan: {days} Days\n"
            f"⌛ Expiry: `{expire_ist}` IST\n\n"
            f"🚀 Enjoy all premium features!\n"
            f"Use /myplan to view your plan details."
        )
    except Exception:
        pass
