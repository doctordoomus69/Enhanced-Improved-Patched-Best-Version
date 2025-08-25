import discord
import os
import time
from discord.ext import commands, tasks
from dotenv import load_dotenv
from asyncio import sleep

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
ALERT_CHANNEL_NAME = "the-chat-signal"

intents = discord.Intents.default()
intents.voice_states = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

alert_channel = None

# Cooldown and state tracking
user_cooldowns = {}  # user_id: {"chatting": ts, "entrance": ts, "lonely": ts}
REJOIN_IGNORE_SECONDS = 60
CHAT_ALERT_DELAY = 10
LONELY_ALERT_DELAY = 60
ALERT_COOLDOWN = 3600

last_chat_alert_users = set()
checking_chat_alert = {}
lonely_alert_tasks = {}
active_chat_sessions = set()  # NEW: track channels currently in a chat session

# ---------------- UTILS ----------------
def can_alert_user(user_id, alert_type):
    now = time.time()
    user_times = user_cooldowns.get(user_id, {})
    last_alert = user_times.get(alert_type, 0)
    return (now - last_alert) > ALERT_COOLDOWN

def update_user_cooldown(user_id, alert_type):
    now = time.time()
    if user_id not in user_cooldowns:
        user_cooldowns[user_id] = {}
    user_cooldowns[user_id][alert_type] = now

# Clean up old cooldowns to prevent unbounded memory growth
@tasks.loop(hours=6)
async def prune_old_cooldowns():
    cutoff = time.time() - (ALERT_COOLDOWN * 2)
    to_delete = [uid for uid, times in user_cooldowns.items()
                 if all(ts < cutoff for ts in times.values())]
    for uid in to_delete:
        del user_cooldowns[uid]

# ---------------- ALERTS ----------------
async def send_chatting_alert(members, vc_name):
    user_ids = set(m.id for m in members if can_alert_user(m.id, "chatting"))
    if len(user_ids) < 2:
        return
    names = " and ".join([m.display_name for m in members if m.id in user_ids])
    if not names:
        return
    await alert_channel.send(f"👀 {names} are chatting in {vc_name}")
    for uid in user_ids:
        update_user_cooldown(uid, "chatting")
        update_user_cooldown(uid, "entrance")
    global last_chat_alert_users
    last_chat_alert_users = user_ids

async def send_entrance_alert(new_member, vc_name):
    if not can_alert_user(new_member.id, "entrance"):
        return
    await alert_channel.send(f"🎙️ {new_member.display_name} has joined {vc_name}")
    update_user_cooldown(new_member.id, "entrance")
    global last_chat_alert_users
    last_chat_alert_users = last_chat_alert_users.union({new_member.id})

# ---------- FIXED LONELY ALERT ----------
async def send_lonely_alert(member, vc_name):
    if not can_alert_user(member.id, "lonely"):
        return
    await alert_channel.send(f"😔 {member.display_name} is all alone in {vc_name}")
    # cooldown only starts **after alert is sent**
    update_user_cooldown(member.id, "lonely")

async def schedule_lonely_alert(member, vc):
    await sleep(LONELY_ALERT_DELAY)
    if member not in vc.members:
        return
    non_bot_members = [m for m in vc.members if not m.bot]
    if len(non_bot_members) == 1 and non_bot_members[0].id == member.id:
        await send_lonely_alert(member, vc.name)

# ---------------- EVENTS ----------------
@bot.event
async def on_ready():
    global alert_channel
    print(f"Logged in as {bot.user}")
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("Guild not found")
        return
    alert_channel = discord.utils.get(guild.text_channels, name=ALERT_CHANNEL_NAME)
    if not alert_channel:
        alert_channel = await guild.create_text_channel(ALERT_CHANNEL_NAME)
    prune_old_cooldowns.start()
    print("Bot is ready and monitoring all voice channels.")

@bot.event
async def on_voice_state_update(member, before, after):
    global checking_chat_alert, last_chat_alert_users

    if not alert_channel:
        return

    now = time.time()

    if before.channel and before.channel != after.channel:
        lonely_alert_tasks.pop(member.id, None)
        if before.channel.id in active_chat_sessions:
            non_bot = [m for m in before.channel.members if not m.bot]
            if not non_bot:
                active_chat_sessions.remove(before.channel.id)
        return

    if after.channel and before.channel != after.channel:
        vc = after.channel
        if vc.id not in checking_chat_alert:
            checking_chat_alert[vc.id] = False

        user_times = user_cooldowns.get(member.id, {})
        last_leave = max(user_times.values()) if user_times else 0
        if (now - last_leave) < REJOIN_IGNORE_SECONDS:
            return

        non_bot_members = [m for m in vc.members if not m.bot]

        if len(non_bot_members) >= 2:
            if vc.id not in active_chat_sessions:
                if not checking_chat_alert[vc.id]:
                    checking_chat_alert[vc.id] = True
                    await sleep(CHAT_ALERT_DELAY)
                    updated_members = [m for m in vc.members if not m.bot]
                    eligible_members = [m for m in updated_members if can_alert_user(m.id, "chatting")]
                    if len(eligible_members) >= 2:
                        await send_chatting_alert(eligible_members, vc.name)
                        active_chat_sessions.add(vc.id)
                    checking_chat_alert[vc.id] = False

            if len(non_bot_members) > 2:
                new_users = [m for m in non_bot_members if m.id not in last_chat_alert_users and can_alert_user(m.id, "entrance")]
                for new_user in new_users:
                    if new_user.id not in last_chat_alert_users:
                        await send_entrance_alert(new_user, vc.name)

        elif len(non_bot_members) == 1:
            if member.id not in lonely_alert_tasks:
                lonely_alert_tasks[member.id] = bot.loop.create_task(schedule_lonely_alert(member, vc))

# ---------------- FLASK SERVER ----------------
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

# Start Flask server on separate thread, then start bot
if __name__ == "__main__":
    Thread(target=run_web).start()
    bot.run(TOKEN)
