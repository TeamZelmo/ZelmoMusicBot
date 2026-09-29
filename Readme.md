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
