This Python script fully automates the creation of videos for TikTok and Instagram for my game KnowUsBetter. It dynamically assembles all components of the video, including the opening clip, questions, answers, images, audio, the advertisement video inserted between questions (and its exact placement), and the closing outro (and more). The result is a ready-to-publish video optimized for social media.

Currently, the script retrieves audio files manually, but it can be extended to send direct requests to the ElevenLabs API. (The ElevenLabs API integration has not been tested yet.)

## requirements
- `ffmpeg` ve `ffprobe` have to be installed.
- Python 3
- `ELEVENLABS_API_KEY` env (if using api, -not tested-)
- `ELEVENLABS_VOICE_ID` env (if using api, -not tested-)
- optional: `ELEVENLABS_MODEL_ID` env (if using api, -not tested-)

## installatin
```powershell
py -m pip install -r requirements.txt
setx ELEVENLABS_API_KEY "YOUR_KEY_HERE"
setx ELEVENLABS_VOICE_ID "YOUR_VOICE_ID_HERE"
```

## Run
```powershell
py render_cift_oyunu.py
```

output will be:
- `output/final_9x16.mp4`

