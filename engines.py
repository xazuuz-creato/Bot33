# engines.py - النسخة الاحترافية المصححة لحماية الـ RAM على Render
import os
import requests
import asyncio
import gc
import arabic_reshaper
from bidi.algorithm import get_display
from moviepy.editor import VideoFileClip, TextClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips
import edge_tts

FONT_NAME = "Cairo-Bold.ttf"

def fix_arabic_text(text):
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
    prompt_dict = {
        "الأزمات": "cinematic dramatic storm portrait",
        "الجزري": "intricate moving mechanical gears animation portrait"
    }
    query = prompt_dict.get(context, "cinematic abstract portrait")
    headers = {"Authorization": api_key}
    url = f"https://pexels.com{query}&per_page=1&orientation=portrait"
    try:
        res = requests.get(url, headers=headers).json()
        if res.get("videos"):
            v_url = res["videos"]["video_files"]["link"]
            filename = f"video_{scene_id}.mp4"
            with open(filename, 'wb') as f: f.write(requests.get(v_url).content)
            return filename
    except: return None

def render_production_pipeline(ups_data, api_key):
    download_font_if_missing()
    assembled_clips = []
    opened_files = [] # تتبع الملفات لإغلاقها فوراً وحماية الذاكرة
    
    for scene in ups_data["scenes"]:
        v_file = fetch_visual_broll(scene["context"], scene["scene_id"], api_key)
        a_file = asyncio.run(generate_vocal_track(scene["text"], scene["scene_id"]))
        
        if v_file and a_file:
            v_clip = VideoFileClip(v_file).subclip(0, scene["duration"])
            a_clip = AudioFileClip(a_file)
            v_clip = v_clip.set_audio(a_clip)
            
            # حفظ الإشارات لإغلاقها لاحقاً
            opened_files.extend([v_clip, a_clip])
            
            clean_text = fix_arabic_text(scene["text"])
            txt_clip = TextClip(
                clean_text, fontsize=40, color='yellow', font=FONT_NAME,
                method='caption', size=(v_clip.w - 100, None), stroke_color='black', stroke_width=3
            )
            txt_clip = txt_clip.set_pos(('center', v_clip.h - 220)).set_duration(scene["duration"])
            
            composite = CompositeVideoClip([v_clip, txt_clip])
            assembled_clips.append(composite)
            opened_files.append(txt_clip)
            
    if assembled_clips:
        output_path = "openstudio_final_video.mp4"
        final_movie = concatenate_videoclips(assembled_clips, method="compose")
        
        # حماية معالج سيرفر رندر بتقييد عدد الـ threads لـ 1 والـ fps لـ 24 لمنع انهيار الخادم
        final_movie.write_videofile(
            output_path, 
            fps=24, 
            codec="libx264", 
            audio_codec="aac", 
            threads=1, 
            logger=None
        )
        
        # تفريغ الذاكرة القسري وإغلاق الملفات المفتوحة (Garbage Collection Hook)
        final_movie.close()
        for clip in opened_files:
            try: clip.close()
            except: pass
        del assembled_clips, opened_files
        gc.collect() # استدعاء جامع القمامة البرمي لتنظيف الـ RAM فوراً
        
        # تنظيف الملفات المؤقتة من القرص الصلب
        for scene in ups_data["scenes"]:
            if os.path.exists(f"video_{scene['scene_id']}.mp4"): os.remove(f"video_{scene['scene_id']}.mp4")
            if os.path.exists(f"audio_{scene['scene_id']}.mp3"): os.remove(f"audio_{scene['scene_id']}.mp3")
        return output_path
    return None
