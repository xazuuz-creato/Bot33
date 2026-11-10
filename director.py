# director.py - النسخة الاحترافية المصححة
class ResearchAgent:
    def process(self, topic):
        return f"تقرير موثق ومصفى حول: {topic}. الفكرة ترتبط بحلول متطورة وإبداعية تكسر الجمود السائد."

class ScriptwriterAgent:
    def process(self, topic, research_data):
        # جعل النص يتغير ديناميكياً بناءً على فكرة المستخدم ومصطلحات البحث
        return f"هل تعلم كيف يغير الـ {topic} قواعد اللعبة تماماً؟ من قلب التحديات الكبرى، نصنع اليوم ابتكاراً ذكياً يبهر العالم بتفاصيله."

class StoryboardAgent:
    def process(self, script_text, topic):
        return [
            {"scene_id": 1, "text": f"هل تعلم كيف يغير الـ {topic} قواعد اللعبة تماماً؟", "context": "الأزمات", "duration": 5},
            {"scene_id": 2, "text": "من قلب التحديات الكبرى، نصنع اليوم ابتكاراً ذكياً يبهر العالم بتفاصيله.", "context": "الجزري", "duration": 6}
        ]

class AIDirector:
    def build_project_specification(self, user_prompt):
        researcher = ResearchAgent()
        scriptwriter = ScriptwriterAgent()
        storyboarder = StoryboardAgent()
        
        r_data = researcher.process(user_prompt)
        script = scriptwriter.process(user_prompt, r_data)
        scenes = storyboarder.process(script, user_prompt)
        
        return {
            "metadata": {"topic": user_prompt, "mood": "cinematic"},
            "scenes": scenes
        }
