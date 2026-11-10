import os
import requests
import asyncio
import arabic_reshaper
from bidi.algorithm import get_display
from moviepy.editor import VideoFileClip, TextClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips
import edge_tts

FONT_NAME = "Cairo-Bold.ttf"

def fix_arabic_text(text):
    """مهارة تصفية وتوصيل خطوط الحروف العربية من اليمين لليسار"""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

def download_font_if_missing():
    if not os.path.exists(FONT_NAME):
        url = "https://github.com"
        with open(FONT_NAME, "wb") as f: f.write(requests.get(url).content)

async def generate_vocal_track(text, scene_id):
    filename = f"audio_{scene_id}.mp3"
    await edge_tts.Communicate(text, "ar-SA-HamedNeural").save(filename)
    return filename

def fetch_visual_broll(context, scene_id, api_key):
    # قاموس الذكاء الاستعاري لربط الصور بالمعاني البشرية الحقيقية
    prompt_dict = {
        "الأزمات": "cinematic dramatic storm portrait",
        "الجزري": "intricate moving mechanical gears portrait"
    }
    query = prompt_dict.get(context, "cinematic abstract portrait")
    headers = {"Authorization": api_key}
    url = f"https://pexels.com{query}&per_page=1&orientation=portrait"
    try:
        res = requests.get(url, headers=headers).json()
        if res.get("videos"):
            v_url = res["videos"][0]["video_files"][0]["link"]
            filename = f"video_{scene_id}.mp4"
            with open(filename, 'wb') as f: f.write(requests.get(v_url).content)
            return filename
    except: return None

def render_production_pipeline(ups_data, api_key):
    download_font_if_missing()
    assembled_clips = []
    
    for scene in ups_data["scenes"]:
        v_file = fetch_visual_broll(scene["context"], scene["scene_id"], api_key)
        a_file = asyncio.run(generate_vocal_track(scene["text"], scene["scene_id"]))
        
        if v_file and a_file:
            video_clip = VideoFileClip(v_file).subclip(0, scene["duration"])
            audio_clip = AudioFileClip(a_file)
            video_clip = video_clip.set_audio(audio_clip)
            
            clean_text = fix_arabic_text(scene["text"])
            txt_clip = TextClip(
                clean_text, fontsize=42, color='yellow', font=FONT_NAME,
                method='caption', size=(video_clip.w - 100, None), stroke_color='black', stroke_width=3
            )
            txt_clip = txt_clip.set_pos(('center', video_clip.h - 220)).set_duration(scene["duration"])
            assembled_clips.append(CompositeVideoClip([video_clip, txt_clip]))
            
    if assembled_clips:
        output_path = "openstudio_final_video.mp4"
        final_movie = concatenate_videoclips(assembled_clips, method="compose")
        final_movie.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac", threads=2, logger=None)
        
        # تنظيف تلقائي فوري للملفات المؤقتة
        for scene in ups_data["scenes"]:
            if os.path.exists(f"video_{scene['scene_id']}.mp4"): os.remove(f"video_{scene['scene_id']}.mp4")
            if os.path.exists(f"audio_{scene['scene_id']}.mp3"): os.remove(f"audio_{scene['scene_id']}.mp3")
        return output_path
    return None
