import os
import sqlite3
import secrets
import time
import threading
import requests
import json
from flask import Flask, request, render_template_string

import discord
from discord.ext import commands

# ---------------------------- CONFIG ----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GUILD_ID = int(os.environ.get("GUILD_ID"))
ROLE_ID = int(os.environ.get("ROLE_ID"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

PREFIX = ";"
DB_PATH = "verify.db"

# ---------------------------- DB 초기화 ----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS verify_tokens (
        token TEXT PRIMARY KEY,
        discord_id INTEGER,
        created_at INTEGER,
        used INTEGER DEFAULT 0,
        used_at INTEGER,
        ip TEXT,
        country TEXT,
        proxy TEXT,
        tor TEXT,
        risk_level TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ---------------------------- SECURITY CHECK ----------------------------

def get_ip_info(ip):
    """
    IP 정보 조회 (국가, 프록시 여부, Tor 여부)
    무료 API 사용
    """
    try:
        url = f"http://ip-api.com/json/{ip}?fields=66846719"
        data = requests.get(url, timeout=3).json()

        country = data.get("country", "Unknown")
        proxy = data.get("proxy", False)
        hosting = data.get("hosting", False)

        tor = "Yes" if hosting else "No"

        risk = "낮음"
        if proxy:
            risk = "의심"
        if hosting:
            risk = "높음"

        return country, str(proxy), tor, risk

    except:
        return "Unknown", "False", "No", "Unknown"

# ---------------------------- FLASK ----------------------------
app = Flask(__name__)

VERIFY_HTML = """
<h2>인증페이지</h2>
<form action="/complete" method="post">
  <input type="hidden" name="token" value="{{token}}">
  <input type="hidden" name="discord_id" value="{{discord_id}}">
  <button type="submit">인증 완료하기</button>
</form>
"""

SUCCESS_HTML = "<h2>✅ 인증 완료됨!</h2>"
FAIL_HTML = "<h3>❌ 오류: {{reason}}</h3>"

def db_get(token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT token, discord_id, used FROM verify_tokens WHERE token=?", (token,))
    row = c.fetchone()
    conn.close()
    return row

def db_update(token, ip, country, proxy, tor, risk):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    UPDATE verify_tokens 
    SET used=1, used_at=?, ip=?, country=?, proxy=?, tor=?, risk_level=?
    WHERE token=?
    """, (now, ip, country, proxy, tor, risk, token))
    conn.commit()
    conn.close()

@app.route("/")
def home():
    return "<h1>Discord 인증 서버 실행 중</h1>"

@app.route("/verify")
def page_verify():
    token = request.args.get("token")
    row = db_get(token)
    if not row:
        return render_template_string(FAIL_HTML, reason="토큰 없음")
    if row[2] == 1:
        return render_template_string(FAIL_HTML, reason="이미 인증됨")

    discord_id = row[1]
    return render_template_string(VERIFY_HTML, token=token, discord_id=discord_id)

@app.route("/complete", methods=["POST"])
def complete():
    token = request.form.get("token")
    discord_id = request.form.get("discord_id")

    row = db_get(token)
    if not row:
        return render_template_string(FAIL_HTML, reason="잘못된 토큰")
    if row[2] == 1:
        return render_template_string(FAIL_HTML, reason="이미 사용됨")

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    country, proxy, tor, risk = get_ip_info(ip)
    db_update(token, ip, country, proxy, tor, risk)

    url = f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{discord_id}/roles/{ROLE_ID}"
    r = requests.put(url, headers={"Authorization": f"Bot {BOT_TOKEN}"})

    if r.status_code == 204:
        return SUCCESS_HTML

    return render_template_string(FAIL_HTML, reason=f"역할 부여 실패: {r.status_code}")

# ---------------------------- DISCORD BOT ----------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user}")

def make_token():
    return secrets.token_urlsafe(16)

@bot.command()
async def 인증(ctx):
    token = make_token()
    created = int(time.time())

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO verify_tokens (token, discord_id, created_at) VALUES (?, ?, ?)",
              (token, ctx.author.id, created))
    conn.commit()
    conn.close()

    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    url = f"{base_url}/verify?token={token}"

    button = discord.ui.Button(label="인증하기", url=url)
    view = discord.ui.View()
    view.add_item(button)

    await ctx.send(f"{ctx.author.mention} 아래 버튼을 눌러 인증하세요.", view=view)

@bot.command()
async def 목록(ctx):
    ADMIN_ID = ctx.author.id  # 너 원하면 특정 ID로 잠글 수 있음

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT discord_id, ip, country, proxy, tor, risk_level FROM verify_tokens WHERE used=1")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.author.send("인증 기록이 없습니다.")
        return

    msg = "✅ **인증된 사용자 목록**\n\n"
    for user_id, ip, country, proxy, tor, risk in rows:
        msg += f"<@{user_id}> - IP: `{ip}` / {country} / 프록시:{proxy} / Tor:{tor} / 위험도:{risk}\n"

    await ctx.author.send(msg)

@bot.command()
async def 질문(ctx, *, question):
    """
    Gemini API 연결 - AI 자동 답변
    """
    headers = {
        "Content-Type": "application/json",
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"

    data = {
        "contents": [{
            "parts": [{"text": question}]
        }]
    }

    response = requests.post(url, headers=headers, json=data).json()

    try:
        answer = response["candidates"][0]["content"]["parts"][0]["text"]
    except:
        answer = "⚠️ AI 응답을 가져오지 못했습니다."

    embed = discord.Embed(
        title="🤖 Gemini AI 답변",
        description=answer,
        color=0x00ffcc
    )

    await ctx.send(embed=embed)

@bot.command()
async def 명령어(ctx):
    msg = (
        "🤖 **명령어 목록:**\n"
        "• ;인증 - 인증 버튼 생성\n"
        "• ;목록 - 인증된 사용자 목록 + 보안 정보\n"
        "• ;질문 (내용) - Gemini AI에게 질문하기\n"
    )
    await ctx.send(msg)

# ---------------------------- RUN SERVER ----------------------------
def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()
bot.run(BOT_TOKEN)
