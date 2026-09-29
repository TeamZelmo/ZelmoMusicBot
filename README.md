# 🎵 Simple Telegram Voice Chat Music Bot

Ye bot Telegram group ke **Voice Chat / Live Stream** me **Helper (Assistant) ID** ke through gaane bajata hai.

- **Bot** (BotFather wala) → commands leta hai (`/play`, `/skip`...)
- **Helper ID** (normal Telegram account) → voice chat me join hokar audio stream karta hai

---

## ✨ Features

- YouTube se naam ya link daal ke gaana bajao
- Queue system (ek ke baad ek gaana)
- Skip, Pause, Resume, Stop
- Gaana khatam hone par apne aap agla gaana

## 📁 Files

| File | Kaam |
|------|------|
| `main.py` | Bot ka main code |
| `gen_session.py` | Helper ID ka SESSION_STRING banane ke liye |
| `requirements.txt` | Python libraries |
| `.env.example` | Config ka namuna |

## ⚙️ Requirements

- Python 3.9 ya usse upar
- `ffmpeg` installed
- Telegram `API_ID` aur `API_HASH` → https://my.telegram.org
- `BOT_TOKEN` → @BotFather
- Ek alag Telegram account (helper ID ke liye)

## 🚀 Setup

### 1. ffmpeg install karo
```bash
# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg -y

# Windows: ffmpeg download karke PATH me add karo
# Termux:  pkg install ffmpeg
```

### 2. Libraries install karo
```bash
pip install -r requirements.txt
```

### 3. Helper ID ka SESSION_STRING banao
```bash
python gen_session.py
```
API_ID, API_HASH, phir helper account ka phone number aur OTP daalo.
Jo string mile use copy kar lo.

### 4. `.env` file banao
`.env.example` ko copy karke naam `.env` rakho aur values bharo:
```env
API_ID=1234567
API_HASH=your_api_hash
BOT_TOKEN=123456:ABC-your-bot-token
SESSION_STRING=your_helper_account_session_string
```

### 5. Bot chalao
```bash
python main.py
```
Terminal me `Bot chalu ho gaya ✅` dikhe to sab theek hai.

## 👥 Group me use kaise kare

1. Bot ko group me add karo aur **admin** banao
2. Helper ID ko bhi group me add karo
3. Group me **Voice Chat / Live Stream start** karo
4. Likho: `/play tum hi ho`

## 🎛 Commands

| Command | Kaam |
|---------|------|
| `/start` | Help message |
| `/play <naam ya link>` | Gaana bajao / queue me daalo |
| `/skip` | Agla gaana |
| `/pause` | Gaana roko |
| `/resume` | Dobara chalao |
| `/queue` | Queue dekho |
| `/stop` | Band karo aur voice chat chhodo |

## 🛠 Problems aur Solutions

| Problem | Solution |
|---------|----------|
| `Play nahi hua` error | Check karo voice chat ON hai aur helper ID group me hai |
| Gaana nahi mil raha | `pip install -U yt-dlp` karo |
| YouTube block kar raha hai | yt-dlp me cookies file use karo ya VPS IP badlo |
| Awaaz nahi aa rahi | `ffmpeg -version` chala ke check karo ffmpeg installed hai |
| PyTgCalls error | `py-tgcalls` ka version 2.1.x hi rakho |

## ☁️ 24/7 chalane ke liye (VPS)

```bash
sudo apt install screen -y
screen -S musicbot
python main.py
# Ctrl+A phir D dabao (background me chalta rahega)
```

## ⚠️ Zaroori baatein

- Helper ke liye **apna main account use mat karo**, alag account rakho.
- `.env` aur `SESSION_STRING` kisi ko share mat karo (isse account access ho sakta hai).
- Sirf un gaano ka use karo jinka use karna aapke liye allowed hai.

---

## 🚀 Render par Deploy kaise kare

### 1. GitHub par upload karo
Poora folder ek **private** GitHub repo me push karo (`.env` upload mat karna, `.gitignore` isse rok deta hai).

### 2. Render par service banao
1. https://render.com par login karo
2. **New +** → **Blueprint** chuno (ya **Web Service**) aur apna repo connect karo
3. `render.yaml` apne aap detect ho jayegi (Runtime: **Docker**)

### 3. Environment Variables daalo
Render dashboard → **Environment** me ye 4 values daalo:

| Key | Value |
|-----|-------|
| `API_ID` | my.telegram.org wala |
| `API_HASH` | my.telegram.org wala |
| `BOT_TOKEN` | BotFather wala |
| `SESSION_STRING` | `gen_session.py` se bana hua |

### 4. Deploy
**Deploy** dabao. Logs me `Bot chalu ho gaya ✅` dikhe to bot ready hai.

### 5. Bot ko sone se bachao (Free plan)
Render ka free Web Service **15 minute** tak koi HTTP request na aaye to so jata hai.
- https://uptimerobot.com par free account banao
- **HTTP(s) monitor** banao, URL me apna Render URL daalo (jaise `https://telegram-music-bot.onrender.com`)
- Interval **5 minutes** rakho

### ⚠️ Render par dhyan rakhne wali baatein
- **YouTube Render ke IP ko aksar block karta hai.** Agar `Sign in to confirm you're not a bot` error aaye, to yt-dlp cookies use karo ya VPS (Oracle/Contabo/Hetzner) par chalao.
- Free plan me 512 MB RAM hoti hai. Kam groups ke liye theek hai, zyada load par paid plan lo.
- Render par `SESSION_STRING` sirf Environment Variables me rakho, code me kabhi nahi.
