import streamlit as st
import os
from director import AIDirector
from engines import render_production_pipeline

st.set_page_config(page_title="OpenStudio AI", page_icon="🤖", layout="wide")

# إعداد الذاكرة المؤقتة لسجل المحادثة الشات
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [{
        "role": "assistant", 
        "content": "مرحباً بك في استوديو OpenStudio AI الذكي. اكتب لي فكرة فيديو وسيتولى أسطول البوتات إنتاجه بالكامل فوراً."
    }]

# تصميم واجهة الـ Dark SaaS السينمائية الناعمة
st.markdown("""
    <style>
    .stApp { background-color: #09090b; color: #ececf1; }
    .user-bubble { background-color: #27272a; padding: 15px; border-radius: 12px; margin: 10px 0; max-width: 80%; float: right; clear: both; direction: rtl; border-right: 4px solid #5436da; }
    .agent-bubble { background-color: #18181b; padding: 15px; border-radius: 12px; margin: 10px 0; max-width: 80%; float: left; clear: both; direction: rtl; border-left: 4px solid #10a37f; }
    </style>
""", unsafe_with_html=True)

st.title("🎬 OpenStudio AI - المنصة المتكاملة")

with st.sidebar:
    st.markdown("### 🔑 مفاتيح ومحركات النظام")
    user_api_key = st.text_input("ادخل مفتاح Pexels API:", type="password", value=os.getenv("PEXELS_API_KEY", ""))
    st.info("نظام ذكاء اصطناعي تفريعي مقسم إلى بوتات متخصصة ومحركات مونتاج مستقلة.")

# عرض فقاعات المحادثة على الشاشة
for msg in st.session_state.chat_history:
    if msg["role"] == "user":
        st.markdown(f'<div class="user-bubble"><b>أنت:</b><br>{msg["content"]}</div>', unsafe_with_html=True)
    else:
        st.markdown(f'<div class="agent-bubble"><b>OpenStudio AI:</b><br>{msg["content"]}</div>', unsafe_with_html=True)
        if "video" in msg: st.video(msg["video"])

st.write("---")
user_input = st.chat_input("أرسل فكرة إنتاج فيديو سينمائي...")

if user_input:
    if not user_api_key:
        st.warning("الرجاء إدخال مفتاح Pexels API في الشاشة الجانبية للبدء!")
    else:
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        st.rerun()

# معالجة الطلب عبر استدعاء الطبقات الخلفية المنفصلة
if st.session_state.chat_history[-1]["role"] == "user":
    current_topic = st.session_state.chat_history[-1]["content"]
    
    with st.spinner("🧠 يقوم الـ AI Director الآن بتوجيه وتلقين أسطول البوتات وتدقيق الجودة..."):
        # أ. استدعاء طبقة العقل المركزي (Director) لتوليد مواصفات المشاهد الموحدة UPS
        orchestrator = AIDirector()
        ups_result = orchestrator.build_project_specification(current_topic)
        
    with st.spinner("🎬 جاري ترحيل ملف المواصفات إلى محركات المونتاج والهندسة الصوتية..."):
        # ب. ترحيل ملف الـ UPS النظيف إلى محرك الإنتاج والمونتاج (Production Engine)
        video_output = render_production_pipeline(ups_result, user_api_key)
        
        if video_output:
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": f"✨ تم الإنتاج بنجاح! إليك فيديو الشورتس الاحترافي المتناسق حول موضوع: ({current_topic})",
                "video": video_output
            })
            st.rerun()
