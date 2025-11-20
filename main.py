import os
import sqlite3
import secrets
import time
import threading
import requests
from flask import Flask, request, render_template_string
import discord
from discord.ext import commands

# ---------------------------- CONFIG ----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GUILD_ID = os.environ.get("GUILD_ID")
ROLE_ID = os.environ.get("ROLE_ID")
ROLE_ID
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
        ip TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ---------------------------- FLASK ----------------------------
app = Flask(__name__)

VERIFY_HTML = """
<h2>인증페이지</h2>
<form action="/complete" method="post">
  <input type="hidden" name="token" value="{{token}}">
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

def db_use(token, ip):
    now = int(time.time())
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE verify_tokens SET used=1, used_at=?, ip=? WHERE token=?", (now, ip, token))
    conn.commit()
    conn.close()

@app.route("/")
def home():
    return "<h1>Discord 인증서버 실행 중</h1>"

@app.route("/verify")
def page_verify():
    token = request.args.get("token")
    row = db_get(token)
    if not row:
        return render_template_string(FAIL_HTML, reason="토큰 없음")
    if row[2] == 1:
        return render_template_string(FAIL_HTML, reason="이미 인증됨")
    return render_template_string(VERIFY_HTML, token=token)

@app.route("/complete", methods=["POST"])
def complete():
    token = request.form.get("token")
    row = db_get(token)
    if not row:
        return render_template_string(FAIL_HTML, reason="잘못된 토큰")
    if row[2] == 1:
        return render_template_string(FAIL_HTML, reason="이미 사용됨")

    ip = request.remote_addr
    discord_id = row[1]
    db_use(token, ip)

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

    # DB에 토큰 저장
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO verify_tokens (token, discord_id, created_at) VALUES (?, ?, ?)",
        (token, ctx.author.id, created)
    )
    conn.commit()
    conn.close()

    base_url = os.environ.get("RENDER_EXTERNAL_URL")
    url = f"{base_url}/verify?token={token}"

    # 버튼 생성
    button = discord.ui.Button(label="인증하기", url=url)
    view = discord.ui.View()
    view.add_item(button)

    # 서버 채널에 버튼 전송
    await ctx.send(f"{ctx.author.mention} 아래 버튼을 눌러 인증하세요.", view=view)

@bot.command()
async def 목록(ctx):
    # 관리자(너) Discord ID
    ADMIN_ID = 1352770328342040651

    if ctx.author.id != ADMIN_ID:
        await ctx.send("❌ 권한 없음")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT discord_id, ip, used_at FROM verify_tokens WHERE used=1")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await ctx.send("인증 기록이 없습니다.")
        return

    msg = ""
    for row in rows:
        user_id, ip, used_at = row
        msg += f"<@{user_id}> - {ip}\n"

    await ctx.send(f"✅ 인증 사용자 목록:\n{msg}")


def run_web():
    port = int(os.environ.get("PORT", 5000))  # Render 환경변수 PORT 사용
    app.run(host="0.0.0.0", port=port)

# 웹서버를 별 스레드로 실행
import threading
threading.Thread(target=run_web).start()

# Discord 봇 실행
bot.run(BOT_TOKEN)
