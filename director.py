class ResearchAgent:
    def process(self, topic):
        return f"بيانات موثقة ومعالجة بدقة حول: {topic}"

class ScriptwriterAgent:
    def process(self, research_data):
        return "هل تعلم أن الابتكار يولد دائماً من قلب الأزمات؟ نصنع اليوم حلاً ذكياً يغير قواعد اللعبة تماماً."

class StoryboardAgent:
    def process(self, script_text):
        # تفكيك النص وتقسيمه إلى مشاهد بنائية منظمة
        return [
            {"scene_id": 1, "text": "هل تعلم أن الابتكار يولد دائماً من قلب الأزمات؟", "context": "الأزمات", "duration": 5},
            {"scene_id": 2, "text": "نصنع اليوم حلاً ذكياً يغير قواعد اللعبة تماماً.", "context": "الجزري", "duration": 6}
        ]

class AIDirector:
    def __init__(self):
        self.researcher = ResearchAgent()
        self.scriptwriter = ScriptwriterAgent()
        self.storyboarder = StoryboardAgent()

    def build_project_specification(self, user_prompt):
        """
        تقوم بالتنسيق المتتابع وتوليد ملف مواصفات المشروع الموحد (UPS)
        """
        r_data = self.researcher.process(user_prompt)
        script = self.scriptwriter.process(r_data)
        scenes = self.storyboarder.process(script)
        
        # ملف الـ UPS الموحد المركزي النظيف لمنع خسارة السياق تواصل بروتوكولي
        ups_specification = {
            "metadata": {"topic": user_prompt, "mood": "cinematic"},
            "scenes": scenes
        }
        return ups_specification

