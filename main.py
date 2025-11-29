# made by end_cry Root

import os
import sqlite3
import secrets
import time
import threading
import asyncio
from urllib.parse import urlencode

from flask import Flask, request, render_template_string, redirect
import requests

import discord
from discord.ext import commands

# ================================================================
# ENVIRONMENT
# ================================================================
def env(name, required=False, default=""):
    v = os.environ.get(name, default)
    if required and not v:
        print(f"[ENV MISSING] {name}")
    return v

BOT_TOKEN = env("BOT_TOKEN", True)
GUILD_ID = int(env("GUILD_ID", True))
ROLE_ID = int(env("ROLE_ID", True))
GEMINI_API_KEY = env("GEMINI_API_KEY", False)

DISCORD_CLIENT_ID = env("DISCORD_CLIENT_ID", True)
DISCORD_CLIENT_SECRET = env("DISCORD_CLIENT_SECRET", True)
REDIRECT_URI = env("RENDER_EXTERNAL_URL") + "/callback"

# ================================================================
# DATABASE
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
# DISCORD BOT
# ================================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=";", intents=intents)


# ================================================================
# Utility Functions
# ================================================================
def ip_info(ip):
    try:
        data = requests.get(f"http://ip-api.com/json/{ip}?fields=66846719").json()
        return (
            data.get("country", "Unknown"),
            data.get("regionName", "Unknown"),
            data.get("city", "Unknown"),
            str(data.get("proxy", False)),
            str(data.get("hosting", False))
        )
    except:
        return ("Unknown", "Unknown", "Unknown", "False", "False")


def oauth_url():
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify"
    }
    return "https://discord.com/api/oauth2/authorize?" + urlencode(params)


# ================================================================
# FLASK WEB SERVER
# ================================================================
app = Flask(__name__)

HOME_HTML = """
<h1>Discord 인증 페이지</h1>
<a href="/start?token={{token}}">인증 시작하기</a>
"""

@app.route("/start")
def start():
    token = request.args.get("token")
    if not token:
        return "토큰 없음"

    return redirect(oauth_url() + f"&state={token}")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    token = request.args.get("state")

    if not code or not token:
        return "코드 또는 상태값 없음"

    # 1) OAuth2 Token 교환
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }

    token_res = requests.post("https://discord.com/api/oauth2/token", data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded"
    }).json()

    access_token = token_res.get("access_token")
    if not access_token:
        return "토큰 발급 실패"

    # 2) OAuth2 유저 정보 가져오기
    user_data = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    discord_id = user_data.get("id")

    # 3) 보안정보 수집
    user_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    user_agent = request.headers.get("User-Agent", "Unknown")

    country, region, city, proxy, hosting = ip_info(user_ip)

    # 4) DB 저장
    cur.execute("""
        INSERT INTO auth_logs (discord_id, ip, user_agent, country, region, city, proxy, hosting, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (discord_id, user_ip, user_agent, country, region, city, proxy, hosting, int(time.time())))
    conn.commit()

    # 5) 디스코드 역할 지급
    async def give_role():
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return

        member = guild.get_member(int(discord_id))
        if not member:
            return

        role = guild.get_role(ROLE_ID)
        if role:
            try:
                await member.add_roles(role)
            except:
                pass

    asyncio.run_coroutine_threadsafe(give_role(), bot.loop)

    return f"""
    <h2>인증 완료!</h2>
    <p>디스코드 ID: {discord_id}</p>
    <p>IP: {user_ip}</p>
    <p>위치: {country} / {region} / {city}</p>
    <p>Proxy: {proxy} / Hosting: {hosting}</p>
    <p>User-Agent: {user_agent}</p>
    """


# ================================================================
# DISCORD COMMANDS
# ================================================================
@bot.command()
async def 인증(ctx):
    token = secrets.token_urlsafe(16)
    url = f"{env('RENDER_EXTERNAL_URL')}/start?token={token}"

    button = discord.ui.Button(label="인증하기", url=url)
    view = discord.ui.View()
    view.add_item(button)

    await ctx.send(f"{ctx.author.mention} 아래 버튼을 눌러 인증하세요.", view=view)


@bot.command()
async def 목록(ctx):
    cur.execute("SELECT discord_id, ip, country, region, city, proxy, hosting, timestamp FROM auth_logs")
    rows = cur.fetchall()

    if not rows:
        return await ctx.send("아직 아무도 인증하지 않음!")

    msg = "📌 ㅣ **인증 로그 목록**\n\n"
    for d, ip, c, r, city, p, h, t in rows[-10:]:
        msg += f"<@{d}> - {ip} ({c}/{r}/{city}) P:{p} H:{h}\n"

    await ctx.send(msg)


@bot.command()
async def 명령어(ctx):
    await ctx.send(
        "  **명령어 목록**\n"
        "• ;인증 - 인증 링크 생성\n"
        "• ;목록 - 인증된 사용자 목록\n"
        "• ;질문 (내용) - Gemini AI 응답\n"
    )


@bot.command()
async def 질문(ctx, *, q):
    if not GEMINI_API_KEY:
        return await ctx.send("Gemini API KEY가 설정되지 않음!")

    data = {
        "contents": [{"parts": [{"text": q}]}]
    }

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}",
        json=data
    ).json()

    try:
        answer = r["candidates"][0]["content"]["parts"][0]["text"]
    except:
        answer = "AI 응답 실패"

    embed = discord.Embed(title="🤖 Gemini 답변", description=answer, color=0x00ffcc)
    await ctx.send(embed=embed)


# ================================================================
# START
# ================================================================
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


threading.Thread(target=run_flask).start()
bot.run(BOT_TOKEN)
