import discord
from discord.ext import tasks
from discord import app_commands # 追加
import requests
from bs4 import BeautifulSoup
from flask import Flask
import threading
import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- Flask設定 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_flask(): 
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- Google Sheets API設定 ---
def get_gs_client():
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    env_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not env_json:
        print("Error: GOOGLE_SERVICE_ACCOUNT_JSON not found.")
        return None
    creds = Credentials.from_service_account_info(json.loads(env_json), scopes=scopes)
    return gspread.authorize(creds)

# --- カラー設定 ---
COLOR_MAP = {
    "三振": 0xFFFF00, "四球": 0x00FF00, "死球": 0x3CB371, "飛": 0xFF0000, 
    "ゴロ": 0xFF0000, "直": 0xFF0000, "犠": 0xFFA500, "併": 0xFFC0CB, "失": 0x00FFFF
}

# --- Discord Bot設定 ---
TOKEN = os.environ.get("DISCORD_TOKEN")

# ★修正：CHANNEL_IDを最初はNoneにして、エラー落ちを防ぐ
CHANNEL_ID = None

# ★Clientのクラスを少し拡張してコマンドツリーを使えるようにする
class MyClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # 起動時にスラッシュコマンドを同期
        await self.tree.sync()

client = MyClient()

# 重複処理防止用キャッシュ
last_at_bat_key = ""

@tasks.loop(minutes=2)
async def fetch_npb_data():
    global last_at_bat_key, CHANNEL_ID
    
    # チャンネルIDが設定されていない場合は何もしない
    if CHANNEL_ID is None:
        return

    # 試合時間外（深夜〜午前）はリクエストしない
    if not (13 <= datetime.now().hour <= 23): return

    try:
        url = "https://baseball.yahoo.co.jp/npb/game/2021006501/text" 
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')

        latest_card = soup.select_one('.live-text-item')
        if not latest_card: return

        batter = latest_card.select_one('.batter').text.strip()
        pitcher = latest_card.select_one('.pitcher').text.strip()
        result = latest_card.select_one('.result').text.strip()
        
        current_key = f"{batter}_{result}"
        if current_key == last_at_bat_key: return

        # --- スプレッドシートへ記録 ---
        gc = get_gs_client()
        sheet = gc.open_by_url(os.environ.get("SHEET_URL")).sheet1
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        sheet.append_row([timestamp, batter, pitcher, result])

        # --- Discord通知 (Embed) ---
        channel = client.get_channel(CHANNEL_ID)
        if channel:
            color = next((v for k, v in COLOR_MAP.items() if k in result), 0x808080)
            embed = discord.Embed(title=f"** 打者 ({batter}) **", color=color)
            embed.add_field(name="【選手】", value=f"・打者：{batter}\n・投手：{pitcher}", inline=False)
            embed.add_field(name="【結果】", value=f"**{result}**", inline=False)
            embed.set_footer(text=f"{timestamp} @Yahoo実況データ引用")
            await channel.send(embed=embed)
            last_at_bat_key = current_key

    except Exception as e:
        print(f"Error during fetch: {e}")

# /set_channel コマンド
@client.tree.command(name="set_channel", description="NPB速報を流すチャンネルを指定します")
@app_commands.describe(target_channel="速報を流したいチャンネルを選択してください")
@app_commands.checks.has_permissions(manage_channels=True)
async def set_channel(interaction: discord.Interaction, target_channel: discord.TextChannel):
    global CHANNEL_ID
    CHANNEL_ID = target_channel.id
    
    try:
        gc = get_gs_client()
        # "config" という名前のシートが既にある前提
        config_sheet = gc.open_by_url(os.environ.get("SHEET_URL")).worksheet("config")
        config_sheet.update_acell('B1', str(target_channel.id))
        await interaction.response.send_message(f"✅ 速報チャンネルを {target_channel.mention} に設定しました！", ephemeral=True)
    except Exception as e:
        # シートがない場合は、単にメモリ上だけで更新
        await interaction.response.send_message(f"⚠️ メモリ上のみ更新しました（シート'config'が見つかりません）: {e}", ephemeral=True)

@client.event
async def on_ready():
    global CHANNEL_ID
    print(f'Logged in as {client.user}')
    
    # 起動時にスプレッドシートから前回のチャンネルIDを読み込む
    try:
        gc = get_gs_client()
        config_sheet = gc.open_by_url(os.environ.get("SHEET_URL")).worksheet("config")
        saved_id = config_sheet.acell('B1').value
        if saved_id:
            CHANNEL_ID = int(saved_id)
            print(f"Loaded Channel ID from Sheets: {CHANNEL_ID}")
    except:
        print("No saved channel ID found. Please use /set_channel")

    if not fetch_npb_data.is_running():
        fetch_npb_data.start()

# Flask開始
threading.Thread(target=run_flask, daemon=True).start()
# Bot開始
client.run(TOKEN)
