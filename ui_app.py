import json
import subprocess
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
QUESTIONS_JSON = ROOT / "input" / "questions.json"
RENDER_SETTINGS_JSON = ROOT / "input" / "render_settings.json"
INPUT_IMAGES = ROOT / "input_images"
INPUT_AUDIO = ROOT / "input_audio"
INPUT_VIDEOS = ROOT / "input_videos"


def load_questions():
    if not QUESTIONS_JSON.exists():
        return []
    return json.loads(QUESTIONS_JSON.read_text(encoding="utf-8"))


def save_questions(questions):
    QUESTIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    QUESTIONS_JSON.write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")


def load_render_settings():
    if not RENDER_SETTINGS_JSON.exists():
        return {}
    return json.loads(RENDER_SETTINGS_JSON.read_text(encoding="utf-8"))


def save_render_settings(settings):
    RENDER_SETTINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RENDER_SETTINGS_JSON.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def save_uploaded_file(uploaded_file, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(uploaded_file.getbuffer())


def build_default_question(idx):
    qid = f"q{idx+1}"
    return {
        "id": qid,
        "question": "",
        "a": {"text": "", "image": f"input_images/{qid}_a.png"},
        "b": {"text": "", "image": f"input_images/{qid}_b.png"},
        "voice_text": "",
        "audio": "",
    }


def run_render(mock=True):
    cmd = ["py", "render_cift_oyunu.py"]
    if mock:
        cmd.append("--mock-tts")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


st.set_page_config(page_title="Cift Video Builder", layout="wide")
st.title("Cift Video Builder")
st.caption("Soru, şık, görsel ve soru sesi (audio) dinamik yönetim paneli")

if "questions_working" not in st.session_state:
    st.session_state["questions_working"] = load_questions()
if "render_settings_working" not in st.session_state:
    st.session_state["render_settings_working"] = load_render_settings()

questions = st.session_state["questions_working"]
render_settings = st.session_state["render_settings_working"]

with st.expander("Video Klip Ayarlari (Dinamik)", expanded=True):
    st.caption("Giris ve araya reklam videosunu buradan degistirebilirsin. Bossa default dosyalar kullanilir.")
    intro_current = render_settings.get("intro_video", "VIDEOGIRIS.mp4")
    ad_current = render_settings.get("ad_video", "VIDEOARAREKLAM.mp4")
    outro_current = render_settings.get("outro_video", "")
    bgm_current = render_settings.get("bg_music", "")
    font_family_current = str(render_settings.get("font_family", "rubik") or "rubik").strip().lower()
    bgm_volume_current = float(render_settings.get("bg_music_volume", 0.25) or 0.0)
    bgm_volume_current = max(0.0, min(1.5, bgm_volume_current))
    ad_after_positions = render_settings.get("ad_insert_after", [3])
    st.caption(f"Mevcut giris videosu: {intro_current}")
    intro_upload = st.file_uploader("Giris Videosu Yukle (mp4)", type=["mp4"], key="intro_video_upload")
    st.caption(f"Mevcut araya reklam videosu: {ad_current}")
    ad_upload = st.file_uploader("Araya Reklam Videosu Yukle (mp4)", type=["mp4"], key="ad_video_upload")
    st.caption(f"Mevcut kapanis videosu: {outro_current if outro_current else 'yok'}")
    outro_upload = st.file_uploader("Kapanis Videosu Yukle (mp4)", type=["mp4"], key="outro_video_upload")
    st.caption(f"Mevcut arka plan sarkisi: {bgm_current if bgm_current else 'yok'}")
    bgm_upload = st.file_uploader("Arka Plan Sarkisi Yukle (mp3/wav/m4a)", type=["mp3", "wav", "m4a"], key="bgm_upload")
    bgm_volume = st.slider("Arka Plan Sesi (volume)", min_value=0.0, max_value=1.5, value=bgm_volume_current, step=0.01)
    font_options = {"Rubik": "rubik", "BRLNS": "brlns"}
    font_labels = list(font_options.keys())
    default_font_label = "Rubik" if font_family_current != "brlns" else "BRLNS"
    font_label = st.selectbox("Font Secimi", font_labels, index=font_labels.index(default_font_label))
    font_family = font_options[font_label]

    intro_path = intro_current
    ad_path = ad_current
    outro_path = outro_current
    bgm_path = bgm_current
    # Reklam ekleme noktaları: Soru sayisina gore Q1->Q2 ... listesi
    total_q = max(1, len(questions))
    positions = [i + 1 for i in range(total_q - 1 or 1)]
    labels = [f"Soru {i} → Soru {i+1}" for i in range(1, (total_q - 1) + 1)]
    label_map = {labels[i]: positions[i] for i in range(len(positions))}
    default_labels = [lbl for lbl, pos in label_map.items() if pos in ad_after_positions]
    selected_labels = st.multiselect("Reklam eklenecek aralik(lar)i sec", labels, default=default_labels)
    selected_positions = [label_map[lbl] for lbl in selected_labels]

    if intro_upload:
        intro_target = INPUT_VIDEOS / "intro.mp4"
        save_uploaded_file(intro_upload, intro_target)
        intro_path = str(intro_target.relative_to(ROOT)).replace("\\", "/")
    if ad_upload:
        ad_target = INPUT_VIDEOS / "ad.mp4"
        save_uploaded_file(ad_upload, ad_target)
        ad_path = str(ad_target.relative_to(ROOT)).replace("\\", "/")
    if outro_upload:
        outro_target = INPUT_VIDEOS / "outro.mp4"
        save_uploaded_file(outro_upload, outro_target)
        outro_path = str(outro_target.relative_to(ROOT)).replace("\\", "/")
    if bgm_upload:
        ext = Path(bgm_upload.name).suffix.lower() or ".mp3"
        bgm_target = INPUT_AUDIO / f"bg_music{ext}"
        save_uploaded_file(bgm_upload, bgm_target)
        bgm_path = str(bgm_target.relative_to(ROOT)).replace("\\", "/")

    new_render_settings = {
        "intro_video": intro_path,
        "ad_video": ad_path,
        "outro_video": outro_path,
        "ad_insert_after": selected_positions,
        "bg_music": bgm_path,
        "bg_music_volume": bgm_volume,
        "font_family": font_family,
    }

col_top_1, col_top_2, col_top_3 = st.columns([1, 1, 2])
with col_top_1:
    if st.button("Yeni Soru Ekle"):
        questions.append(build_default_question(len(questions)))
        st.session_state["questions_working"] = questions
with col_top_2:
    if st.button("4 Ornek Soru Doldur (Hizli)"):
        questions = [build_default_question(i) for i in range(4)]
        st.session_state["questions_working"] = questions
with col_top_3:
    st.info(f"Toplam soru: {len(questions)}")

updated = []

for i, q in enumerate(questions):
    qid = q.get("id", f"q{i+1}")
    with st.expander(f"Soru {i+1} ({qid})", expanded=True):
        delete_me = st.checkbox("Bu soruyu sil", key=f"del_{i}")
        new_id = st.text_input("ID", value=qid, key=f"id_{i}")
        new_question = st.text_area("Soru", value=q.get("question", ""), key=f"q_{i}", height=90)

        c1, c2 = st.columns(2)
        with c1:
            a_text = st.text_input("A Sikki", value=q.get("a", {}).get("text", ""), key=f"a_text_{i}")
            a_img_current = q.get("a", {}).get("image", f"input_images/{new_id}_a.png")
            st.caption(f"Mevcut A gorsel: {a_img_current}")
            a_img_upload = st.file_uploader("A Gorsel Yukle", type=["png", "jpg", "jpeg", "webp"], key=f"a_img_{i}")
        with c2:
            b_text = st.text_input("B Sikki", value=q.get("b", {}).get("text", ""), key=f"b_text_{i}")
            b_img_current = q.get("b", {}).get("image", f"input_images/{new_id}_b.png")
            st.caption(f"Mevcut B gorsel: {b_img_current}")
            b_img_upload = st.file_uploader("B Gorsel Yukle", type=["png", "jpg", "jpeg", "webp"], key=f"b_img_{i}")

        v1, v2 = st.columns(2)
        with v1:
            voice_text = st.text_area(
                "voice_text (bos birakirsan soru okunur)",
                value=q.get("voice_text", ""),
                key=f"voice_{i}",
                height=70,
            )
        with v2:
            audio_current = q.get("audio", "")
            st.caption(f"Mevcut soru sesi (audio): {audio_current if audio_current else 'yok'}")
            audio_upload = st.file_uploader("Soru Sesi Yukle (mp3/wav)", type=["mp3", "wav", "m4a"], key=f"audio_{i}")
            clear_audio = st.checkbox("Bu soruda yuklu ozel sesi kaldir", key=f"clear_audio_{i}")
            if audio_current:
                current_audio_abs = (ROOT / audio_current).resolve()
                if current_audio_abs.exists():
                    st.audio(str(current_audio_abs))

        if delete_me:
            continue

        # Paths
        a_img_path = Path(a_img_current)
        b_img_path = Path(b_img_current)
        if a_img_upload:
            ext = Path(a_img_upload.name).suffix.lower() or ".png"
            a_img_path = Path(f"input_images/{new_id}_a{ext}")
            save_uploaded_file(a_img_upload, ROOT / a_img_path)
        if b_img_upload:
            ext = Path(b_img_upload.name).suffix.lower() or ".png"
            b_img_path = Path(f"input_images/{new_id}_b{ext}")
            save_uploaded_file(b_img_upload, ROOT / b_img_path)

        audio_path = audio_current
        if clear_audio:
            audio_path = ""
        if audio_upload:
            ext = Path(audio_upload.name).suffix.lower() or ".mp3"
            audio_target = INPUT_AUDIO / f"{new_id}{ext}"
            save_uploaded_file(audio_upload, audio_target)
            audio_path = str(audio_target.relative_to(ROOT)).replace("\\", "/")

        updated.append(
            {
                "id": new_id,
                "question": new_question,
                "a": {"text": a_text, "image": str(a_img_path).replace("\\", "/")},
                "b": {"text": b_text, "image": str(b_img_path).replace("\\", "/")},
                "voice_text": voice_text,
                "audio": audio_path,
            }
        )

col_save, col_render_mock, col_render_real = st.columns(3)
with col_save:
    if st.button("Kaydet (questions.json)"):
        save_questions(updated)
        save_render_settings(new_render_settings)
        st.session_state["questions_working"] = updated
        st.session_state["render_settings_working"] = new_render_settings
        st.success("Kaydedildi.")

with col_render_mock:
    if st.button("Mock Render Baslat"):
        save_questions(updated)
        save_render_settings(new_render_settings)
        st.session_state["questions_working"] = updated
        st.session_state["render_settings_working"] = new_render_settings
        code, out, err = run_render(mock=True)
        if code == 0:
            st.success("Mock render tamamlandi. output/final_9x16.mp4 guncellendi.")
        else:
            st.error("Mock render hata verdi.")
        if out.strip():
            st.text(out)
        if err.strip():
            st.text(err)

with col_render_real:
    if st.button("Gercek Render Baslat (ElevenLabs)"):
        save_questions(updated)
        save_render_settings(new_render_settings)
        st.session_state["questions_working"] = updated
        st.session_state["render_settings_working"] = new_render_settings
        code, out, err = run_render(mock=False)
        if code == 0:
            st.success("Gercek render tamamlandi. output/final_9x16.mp4 guncellendi.")
        else:
            st.error("Gercek render hata verdi.")
        if out.strip():
            st.text(out)
        if err.strip():
            st.text(err)

st.markdown("---")
st.caption("Not: Her soruya audio yuklersen, render script o audio'yu dogrudan kullanir. Yuklemezsen mevcut TTS akisi devreye girer.")

