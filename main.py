# made by end_cry

import os
import sqlite3
import secrets
import time
import threading
import asyncio
from urllib.parse import urlencode

from flask import Flask, request, redirect
import requests

import discord
from discord.ext import commands

# ================================================================
# ENVIRONMENT VARIABLES
# ================================================================
def env(name, required=False, default=None):
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"[ENV MISSING] {name} is required but not set!")
    return value

BOT_TOKEN = env("BOT_TOKEN", True)
GUILD_ID = int(env("GUILD_ID", True))
ROLE_ID = int(env("ROLE_ID", True))
GEMINI_API_KEY = env("GEMINI_API_KEY", False)

DISCORD_CLIENT_ID = env("DISCORD_CLIENT_ID", True)
DISCORD_CLIENT_SECRET = env("DISCORD_CLIENT_SECRET", True)
RENDER_URL = env("RENDER_EXTERNAL_URL", True)
REDIRECT_URI = f"{RENDER_URL}/callback"

# ================================================================
# DATABASE SETUP
# ================================================================
conn = sqlite3.connect("auth.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS auth_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id TEXT,
    ip TEXT,
    user_agent TEXT,
    country TEXT,
    region TEXT,
    city TEXT,
    proxy TEXT,
    hosting TEXT,
    timestamp INTEGER
)
""")
conn.commit()

# ================================================================
# DISCORD BOT SETUP
# ================================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=";", intents=intents)

# ================================================================
# UTILITY FUNCTIONS
# ================================================================
def ip_info(ip):
    try:
        data = requests.get(f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,proxy,hosting").json()
        country = data.get("country", "Unknown")
        region = data.get("regionName", "Unknown")
        city = data.get("city", "Unknown")
        proxy = str(data.get("proxy", False))
        hosting = str(data.get("hosting", False))
        return country, region, city, proxy, hosting
    except:
        return "Unknown", "Unknown", "Unknown", "False", "False"

def build_oauth_url(state_token):
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state_token
    }
    return "https://discord.com/api/oauth2/authorize?" + urlencode(params)

# ================================================================
# FLASK SETUP
# ================================================================
app = Flask(__name__)

@app.route("/start")
def start():
    token = request.args.get("token")
    if not token:
        return "토큰 없음"
    return redirect(build_oauth_url(token))

@app.route("/callback")
def callback():
    code = request.args.get("code")
    token = request.args.get("state")

    if not code or not token:
        return "코드 또는 state 값 없음"

    # OAuth2 토큰 요청
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers).json()
    access_token = token_res.get("access_token")
    if not access_token:
        return "토큰 발급 실패"

    # 유저 정보 요청
    user_res = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    discord_id = user_res.get("id")
    if not discord_id:
        return "디스코드 ID를 가져오지 못함"

    # IP, User-Agent, 위치 정보
    user_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    user_agent = request.headers.get("User-Agent", "Unknown")
    country, region, city, proxy, hosting = ip_info(user_ip)

    # DB 저장
    cur.execute("""
        INSERT INTO auth_logs (discord_id, ip, user_agent, country, region, city, proxy, hosting, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (discord_id, user_ip, user_agent, country, region, city, proxy, hosting, int(time.time())))
    conn.commit()

    # 역할 부여
    async def give_role():
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            print("[WARN] 길드 없음")
            return
        try:
            member = await guild.fetch_member(int(discord_id))
            role = guild.get_role(ROLE_ID)
            if member and role:
                await member.add_roles(role)
                print(f"[INFO] 역할 부여 완료: {discord_id}")
        except Exception as e:
            print(f"[ERROR] 역할 부여 실패: {e}")

    asyncio.run_coroutine_threadsafe(give_role(), bot.loop)

    return f"""
    <h2>✅ 인증 완료!</h2>
    <p>디스코드 ID: {discord_id}</p>
    <p>IP: {user_ip}</p>
    <p>위치: {country} / {region} / {city}</p>
    <p>Proxy: {proxy} / Hosting: {hosting}</p>
    <p>User-Agent: {user_agent}</p>
    """

# ================================================================
# BOT COMMANDS
# ================================================================
@bot.command()
async def 인증(ctx):
    token = secrets.token_urlsafe(16)
    url = f"{RENDER_URL}/start?token={token}"
    button = discord.ui.Button(label="인증하기", url=url)
    view = discord.ui.View()
    view.add_item(button)
    await ctx.send(f"{ctx.author.mention} 아래 버튼을 눌러 인증하세요.", view=view)

@bot.command()
async def 목록(ctx):
    cur.execute("SELECT discord_id, ip, country, region, city, proxy, hosting, timestamp FROM auth_logs ORDER BY id DESC LIMIT 10")
    rows = cur.fetchall()
    if not rows:
        return await ctx.send("아직 인증 기록 없음!")

    msg = "📌 **최근 인증 로그 10명**\n\n"
    for d, ip, c, r, city, p, h, t in rows:
        msg += f"<@{d}> - {ip} ({c}/{r}/{city}) P:{p} H:{h}\n"
    await ctx.send(msg)

@bot.command()
async def 명령어(ctx):
    await ctx.send(
        "**명령어 목록**\n"
        "• ;인증 - 인증 버튼 생성\n"
        "• ;목록 - 최근 인증 로그 확인\n"
        "• ;질문 (내용) - Gemini AI에게 질문\n"
    )

@bot.command()
async def 질문(ctx, *, q):
    if not GEMINI_API_KEY:
        return await ctx.send("⚠ Gemini API KEY가 설정되지 않음!")
    data = {"contents": [{"parts": [{"text": q}]}]}
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
            json=data
        ).json()
        answer = r["candidates"][0]["content"]["parts"][0]["text"]
    except:
        answer = "⚠ AI 응답 실패"
    embed = discord.Embed(title="🤖 Gemini 답변", description=answer, color=0x00ffcc)
    await ctx.send(embed=embed)

# ================================================================
# START FLASK + BOT
# ================================================================
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()
bot.run(BOT_TOKEN)
