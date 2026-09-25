# Telegram VC Music Bot (ytmusicapi + py-tgcalls)

Ye bot Telegram group ke **Voice Chat** me `ytmusicapi` se search karke gaane play karta hai.

## Kaise kaam karta hai
1. **Bot account** (BotFather wala) — commands sunta hai (`/play`, `/skip`, etc.)
2. **Assistant account** (aapka normal Telegram account, session string ke through) — ye actual VC me join hota hai kyunki bots khud VC me join nahi ho sakte, isliye ek userbot "assistant" chahiye hota hai.
3. `ytmusicapi` se song search hota hai → best match milta hai.
4. `yt-dlp` us video ka direct audio stream URL nikalta hai.
5. `py-tgcalls` us stream URL ko group ki VC me play kar deta hai.

## Setup Steps

### 1. Dependencies install karo
```bash
pip install -r requirements.txt
```
(FFmpeg bhi system me installed hona chahiye: `sudo apt install ffmpeg`)

### 2. Telegram API credentials lo
[my.telegram.org](https://my.telegram.org) par jaake `API_ID` aur `API_HASH` generate karo.

### 3. Bot banao
[@BotFather](https://t.me/BotFather) se naya bot banao aur `BOT_TOKEN` le lo.

### 4. Assistant session string generate karo
```bash
python generate_session.py
```
Apna normal Telegram account (phone number) use karke login karo — ye print hone wala `SESSION_STRING` copy kar lo.

> ⚠️ Session string kisi ke saath share mat karo — isse pura account access mil jaata hai.

### 5. `.env` file banao
`.env.example` ko `.env` naam se copy karo aur values bharo:
```bash
cp .env.example .env
```

### 6. Bot ko group me add karo
- **Bot account** ko group me add karo aur **Admin** banao (VC manage karne ki permission ke saath).
- **Assistant account** (jiska session banaya) ko bhi usi group me add karo.

### 7. Bot chalao
```bash
python bot.py
```

## Commands

**Playback**
| Command | Kaam |
|---|---|
| `/play <naam>` | Gaana search karke play/queue karta hai |
| `/playnext <naam>` | Gaana search karke queue ke top pe daalta hai |
| `/pause` | Pause |
| `/resume` | Resume |
| `/skip` | Agla gaana |
| `/stop` ya `/end` | Stop + queue clear |
| `/replay` | Current gaana shuru se dobara |

**Queue**
| Command | Kaam |
|---|---|
| `/queue` ya `/q` | Poori queue dikhata hai |
| `/nowplaying` ya `/current` | Abhi kya chal raha hai |
| `/shuffle` | Queue shuffle karta hai |
| `/loop` | Current gaana repeat on/off |

**Volume**
| Command | Kaam |
|---|---|
| `/volume <0-200>` | Awaaz set karta hai |
| `/mute` | Mute |
| `/unmute` | Unmute |

**Extra**
| Command | Kaam |
|---|---|
| `/lyrics` | Current gaane ke lyrics dikhata hai |
| `/ping` | Response time + uptime |
| `/help` | Saare commands ki list |

## Troubleshooting (common problems)

| Error / Problem | Kaaran + Fix |
|---|---|
| `NoActiveGroupCall` / "VC start nahi hai" | Group me pehle **Voice Chat start** karo (group name ke niche se), tab `/play` use karo. |
| Bot kuch reply hi nahi karta | `.env` me `BOT_TOKEN` galat hai, ya bot start hi nahi hua — terminal me `python bot.py` ka output check karo. |
| `AUTH_KEY_UNREGISTERED` / assistant login fail | `generate_session.py` dobara chalao, session expire ho chuki ho sakti hai. |
| Assistant VC join nahi kar pa raha | Assistant (userbot) account us **group ka member** hona chahiye — pehle use group me add karo. |
| `Peer id invalid` | Bot ko pehle group me add karo aur ek baar koi command/msg bhejo taaki peer cache ho jaaye. |
| Awaaz nahi aa rahi / robotic sound | `ffmpeg` installed nahi hai ya purana version hai → `sudo apt install -y ffmpeg` |
| `yt_dlp` error: "Sign in to confirm you're not a bot" | YouTube kabhi-kabhi IP/rate-limit block karta hai; VPS ka IP badlo ya thodi der baad retry karo. `yt-dlp -U` se update bhi karo. |
| Import error: `MediaStream`/`StreamEnded` not found | `pytgcalls` version mismatch — `pip install -U py-tgcalls` karke `requirements.txt` wali version se match karo. |
| Bot admin hone ke baad bhi kaam nahi kar raha | Bot ko group me **"Manage Voice Chats"** permission specifically do (sirf admin banana kaafi nahi). |

Agar upar wale se problem solve na ho, to **poora error message** (terminal se) share karo, exact us line ka fix bata dunga.

## Notes
- Ye code `py-tgcalls` v2.x API follow karta hai — agar library update ho jaye to `bot.py` me `MediaStream` / `StreamEnded` import paths check kar lena (`pytgcalls` GitHub docs dekho).
- Legal: sirf apna khud ka content ya jisme copyright issue na ho, wahi stream karo. YouTube ToS ka bhi dhyaan rakho.
