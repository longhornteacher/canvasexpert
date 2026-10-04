# Engine

The engine parses canonical QuizForge JSON, builds quiz models, and renders printable PDF and DOCX artifacts. Canvas New Quiz publishing is handled by `api/qf_pusher.py` and `api/transform.py`; it does not use an engine Canvas export pipeline.
