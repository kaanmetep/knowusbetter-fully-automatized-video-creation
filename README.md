# Çift Oyun Video Otomasyonu

Bu repo, `input/questions.json` içindeki sorulara göre her soru için:
1) ElevenLabs'tan sesi üretir (cache'ler)
2) Görseli (PIL) render eder (eksik görseller varsa placeholder üretir)
3) `audio süresi + 5sn` uzunlukta segment video üretir
4) Segmentleri sırayla concat edip tek `output/final_9x16.mp4` üretir
5) `clock.mp3` varsa, soru bitince timer bar dolarken arka planda tik-tak çalar

## Gerekenler
- `ffmpeg` ve `ffprobe` kurulu olmalı (PATH'te erişilebilir)
- Python 3
- `ELEVENLABS_API_KEY` ortam değişkeni
- `ELEVENLABS_VOICE_ID` ortam değişkeni
- İsteğe bağlı: `ELEVENLABS_MODEL_ID`

## Kurulum
```powershell
py -m pip install -r requirements.txt
setx ELEVENLABS_API_KEY "YOUR_KEY_HERE"
setx ELEVENLABS_VOICE_ID "YOUR_VOICE_ID_HERE"
```

## Çalıştırma
```powershell
py render_cift_oyunu.py
```

Mock (ElevenLabs olmadan) için ses dosyalarını repo ana dizinine koyabilirsin:
- `ses1.mp3`, `ses2.mp3` (soru sırasına göre 1.->ses1, 2.->ses2)
- sonra `--mock-tts` ile çalıştır: `py render_cift_oyunu.py --mock-tts`

Çıktı:
- `output/final_9x16.mp4`

