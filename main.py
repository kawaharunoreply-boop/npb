import discord
from discord.ext import tasks
import requests
from bs4 import BeautifulSoup
from flask import Flask
import threading
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- Flask設定 (Renderのスリープ防止) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_flask(): app.run(host='0.0.0.0', port=8080)

# --- Google Sheets API設定 ---
def get_sheet():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    # Renderの環境変数からJSONを読み込む
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds = Credentials.from_service_account_info(json.loads(env_json), scopes=scopes)
    gc = gspread.authorize(creds)
    # スプレッドシートIDまたはURLで開く
    return gc.open_by_url(os.environ.get("SHEET_URL")).sheet1

# --- カラー設定 ---
COLOR_MAP = {
    "三振": 0xFFFF00, "四球": 0x00FF00, "死球": 0x3CB371, "飛": 0xFF0000, 
    "ゴロ": 0xFF0000, "直": 0xFF0000, "犠": 0xFFA500, "併": 0xFFC0CB, "失": 0x00FFFF
}

# --- Discord Bot設定 ---
TOKEN = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))
client = discord.Client(intents=discord.Intents.default())

# 重複処理防止用キャッシュ (打席IDや結果を保持)
last_at_bat_key = ""

@tasks.loop(minutes=2) # 打席ごとなので2分間隔で十分
async def fetch_npb_data():
    global last_at_bat_key
    
    # 試合時間外（深夜〜午前）はリクエストしない
    if not (13 <= datetime.now().hour <= 23): return

    try:
        # Yahoo!一打席速報のURL (例: 巨vs神)
        # ※実際にはその日の試合URLを動的に取得するロジックが必要
        url = "https://baseball.yahoo.co.jp/npb/game/2021006501/text" 
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')

        # 最新の打席ブロックを取得 (クラス名はYahooの仕様変更に合わせて要調整)
        latest_card = soup.select_one('.live-text-item')
        if not latest_card: return

        batter = latest_card.select_one('.batter').text.strip()
        pitcher = latest_card.select_one('.pitcher').text.strip()
        result = latest_card.select_one('.result').text.strip()
        
        # カウント・塁状況などの取得（一打席速報の構造から抽出）
        # ※ここでは簡易的に「打者_結果」をキーにして重複判定
        current_key = f"{batter}_{result}"
        if current_key == last_at_bat_key: return

        # --- スプレッドシートへ記録 ---
        sheet = get_sheet()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        sheet.append_row([timestamp, batter, pitcher, result])

        # --- Discord通知 (Embed) ---
        channel = client.get_channel(CHANNEL_ID)
        color = next((v for k, v in COLOR_MAP.items() if k in result), 0x808080)

        embed = discord.Embed(title=f"** 打者 ({batter}) **", color=color)
        embed.add_field(name="【選手】", value=f"・打者：{batter}\n・投手：{pitcher}", inline=False)
        embed.add_field(name="【結果】", value=f"**{result}**", inline=False)
        # ※カウントやスコアもsoupから抽出してadd_field可能
        embed.set_footer(text=f"{timestamp} @Yahoo実況データ引用")

        await channel.send(embed=embed)
        last_at_bat_key = current_key

    except Exception as e:
        print(f"Error during fetch: {e}")

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    fetch_npb_data.start()

# Flaskスレッド開始
threading.Thread(target=run_flask).start()
# Bot開始
client.run(TOKEN)
