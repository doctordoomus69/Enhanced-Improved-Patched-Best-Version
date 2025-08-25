import discord
import os
import time
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from asyncio import sleep

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
VC_CHANNEL_NAME = None  # bot now works for all channels

# Cooldown settings (in seconds)
ENTRANCE_COOLDOWN = 60
CHATTING_COOLDOWN = 300
LONELY_COOLDOWN = 60
LONELY_ALERT_DELAY = 5

# Track cooldowns
user_cooldowns = {"entrance": {}, "chatting": {}, "lonely": {}}
lonely_alert_tasks = {}

intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

def is_on_cooldown(user_id, alert_type):
    return time.time() < user_cooldowns[alert_type].get(user_id, 0)

def update_cooldown(user_id, alert_type, cooldown):
    user_cooldowns[alert_type][user_id] = time.time() + cooldown

async def send_entrance_alert(member, channel):
    if not is_on_cooldown(member.id, "entrance"):
        await channel.send(f"🔔 {member.display_name} has joined {member.voice.channel.name}!")
        update_cooldown(member.id, "entrance", ENTRANCE_COOLDOWN)

async def send_chatting_alert(channel, members):
    if len(members) >= 2:
        now = time.time()
        if now >= user_cooldowns["chatting"].get("global", 0):
            await channel.send("💬 A conversation has started!")
            user_cooldowns["chatting"]["global"] = now + CHATTING_COOLDOWN

async def send_lonely_alert(member, channel):
    if not is_on_cooldown(member.id, "lonely"):
        await sleep(LONELY_ALERT_DELAY)
        if member.voice and len(member.voice.channel.members) == 1:
            await channel.send(f"😔 {member.display_name} is alone in {member.voice.channel.name}...")
            update_cooldown(member.id, "lonely", LONELY_COOLDOWN)

def schedule_lonely_alert(member, channel):
    task = bot.loop.create_task(send_lonely_alert(member, channel))
    lonely_alert_tasks[member.id] = task

@bot.event
async def on_ready():
    print(f"{bot.user} is now running!")

@bot.event
async def on_voice_state_update(member, before, after):
    guild = bot.get_guild(GUILD_ID)
    channel = discord.utils.get(guild.text_channels, name="general")

    if not channel:
        return

    # User joins a channel
    if after.channel and not before.channel:
        await send_entrance_alert(member, channel)
        if len(after.channel.members) == 1:
            schedule_lonely_alert(member, channel)
        elif len(after.channel.members) == 2:
            await send_chatting_alert(channel, after.channel.members)

    # User leaves a channel
    elif before.channel and not after.channel:
        task = lonely_alert_tasks.pop(member.id, None)
        if task:
            task.cancel()

    # User switches channels
    elif before.channel != after.channel:
        task = lonely_alert_tasks.pop(member.id, None)
        if task:
            task.cancel()

        if len(after.channel.members) == 1:
            schedule_lonely_alert(member, channel)
        elif len(after.channel.members) == 2:
            await send_chatting_alert(channel, after.channel.members)

# Flask server for uptime pings
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=run_flask).start()

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
