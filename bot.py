import os, asyncio, re, hashlib
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile
from supabase import create_client
import yt_dlp
import requests
from huggingface_hub import InferenceClient

TOKEN = "8551273103:AAGXRWGAnOCcGmY1zOPYN-tSpGmWL-nSO9Q"
S_URL = os.environ.get("SUPABASE_URL")
S_KEY = os.environ.get("SUPABASE_KEY")
HF_TOKEN = os.environ.get("HF_TOKEN")

ADMIN_ID = 7406956732
MOD_ID = 8428941343
GROUP_ID = -1003230264529

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = create_client(S_URL, S_KEY)
ai_client = InferenceClient("mistralai/Mistral-7B-Instruct-v0.2", token=HF_TOKEN) if HF_TOKEN else None

def get_role(uid):
    r = db.table("users").select("*").eq("id", uid).execute()
    if r.data:
        u = r.data[0]
        if u.get("expires") and datetime.fromisoformat(u["expires"]) < datetime.now(timezone.utc):
            db.table("users").update({"role": "user", "expires": None}).eq("id", uid).execute()
            return "user"
        return u["role"]
    if uid == ADMIN_ID: return "admin"
    if uid == MOD_ID: return "moderator"
    return "user"

def set_role(uid, role, expires=None):
    r = db.table("users").select("*").eq("id", uid).execute()
    data = {"id": uid, "role": role, "expires": expires}
    if r.data: db.table("users").update(data).eq("id", uid).execute()
    else: db.table("users").insert(data).execute()

def p_time(ts):
    if not ts: return None
    m = re.match(r"(\d+)([mhdwyM])", ts)
    if not m: return None
    v, u = int(m.group(1)), m.group(2)
    n = datetime.now(timezone.utc)
    if u == 'm': return (n + timedelta(minutes=v)).isoformat()
    if u == 'h': return (n + timedelta(hours=v)).isoformat()
    if u == 'd': return (n + timedelta(days=v)).isoformat()
    if u == 'w': return (n + timedelta(weeks=v)).isoformat()
    if u == 'M': return (n + timedelta(days=v*30)).isoformat()
    if u == 'y': return (n + timedelta(days=v*365)).isoformat()
    return None

def get_weekly_pwd():
    return hashlib.sha256(f"KVRAMIS_{datetime.now().isocalendar()[1]}".encode()).hexdigest()[:8]

@dp.message(Command("pwd"))
async def send_pwd(m: types.Message):
    if get_role(m.from_user.id) == "admin":
        await bot.send_message(GROUP_ID, f"KEY: {get_weekly_pwd()}")

@dp.message(Command("sudo"))
async def sudo_cmd(m: types.Message):
    args = m.text.split()
    if len(args) == 3 and args[1] == get_weekly_pwd():
        role = args[2].lower()
        if role in ["helper", "moderator", "admin"]:
            set_role(m.from_user.id, role)
            await m.answer(f"ROLE: {role.upper()}")

@dp.message(Command("instructions"))
async def cmds(m: types.Message):
    role = get_role(m.from_user.id)
    if role == "admin": await m.answer("/promote <id> <role> [time]\n/demote <id>\n/ban <id> <time> <reason>\n/unban <id>\n/pwd")
    elif role == "moderator": await m.answer("/ban <id> <time> <reason>")
    elif role == "helper": await m.answer("REPLY IN GROUP")

@dp.message(Command("promote"))
async def prm(m: types.Message):
    if get_role(m.from_user.id) != "admin": return
    a = m.text.split()
    if len(a) < 3: return
    uid, role = int(a[1]), a[2]
    exp = p_time(a[3]) if len(a) > 3 else None
    set_role(uid, role, exp)
    await m.answer(f"{uid} -> {role.upper()} [{exp}]")

@dp.message(Command("demote"))
async def dmt(m: types.Message):
    if get_role(m.from_user.id) != "admin": return
    uid = int(m.text.split()[1])
    set_role(uid, "user")
    await m.answer(f"{uid} -> DEMOTED")

@dp.message(Command("ban"))
async def bn(m: types.Message):
    role = get_role(m.from_user.id)
    if role not in ["admin", "moderator"]: return
    a = m.text.split(maxsplit=3)
    if len(a) < 4: return
    uid, exp, rsn = int(a[1]), p_time(a[2]), a[3]
    if role == "moderator" and not exp: return
    set_role(uid, "banned", exp)
    await bot.send_message(uid, f"BANNED: {rsn}\n/appeal <text>")
    await m.answer(f"{uid} BANNED")

@dp.message(Command("unban"))
async def ubn(m: types.Message):
    if get_role(m.from_user.id) != "admin": return
    uid = int(m.text.split()[1])
    set_role(uid, "user")
    await m.answer(f"{uid} UNBANNED")

@dp.message(Command("appeal"))
async def apl(m: types.Message):
    if get_role(m.from_user.id) == "banned":
        await bot.send_message(GROUP_ID, f"APPEAL | UID:{m.from_user.id} | {m.text.replace('/appeal ', '')}")

@dp.message(Command("support"))
async def spt(m: types.Message):
    if get_role(m.from_user.id) == "banned": return
    await bot.send_message(GROUP_ID, f"TICKET | UID:{m.from_user.id} | {m.text.replace('/support ', '')}")

@dp.message(F.chat.id == GROUP_ID)
async def grp_rep(m: types.Message):
    if m.reply_to_message and ("TICKET | UID:" in m.reply_to_message.text or "APPEAL | UID:" in m.reply_to_message.text):
        if get_role(m.from_user.id) in ["admin", "moderator", "helper"]:
            uid = int(m.reply_to_message.text.split("UID:")[1].split("|")[0].strip())
            await bot.send_message(uid, f"SUPPORT: {m.text}")

@dp.message(Command("dl"))
async def dl_media(m: types.Message):
    if get_role(m.from_user.id) == "banned": return
    url = m.text.split()[1]
    msg = await m.answer("DL...")
    def _dl():
        opts = {'format': 'best', 'outtmpl': f'{m.from_user.id}.%(ext)s'}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)['ext']
    try:
        ext = await asyncio.to_thread(_dl)
        await bot.send_video(m.chat.id, FSInputFile(f"{m.from_user.id}.{ext}"))
        os.remove(f"{m.from_user.id}.{ext}")
    except: pass
    await msg.delete()

@dp.message(Command("crypto"))
async def crp(m: types.Message):
    c = m.text.split()[1]
    r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={c}&vs_currencies=usd").json()
    await m.answer(f"{c.upper()}: ${r.get(c, {}).get('usd', 'ERR')}")

@dp.message(Command("ai"))
async def ask_ai(m: types.Message):
    if not ai_client: return
    resp = await asyncio.to_thread(ai_client.text_generation, m.text.replace("/ai ", ""))
    await m.answer(resp)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
