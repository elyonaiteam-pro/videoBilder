from aiogram.fsm.state import State, StatesGroup


class GenerationFlow(StatesGroup):
    choosing_idea_source = State()     # шаблонная / своя / от Gemini
    choosing_template     = State()    # список шаблонных идей
    entering_custom_idea  = State()    # пользователь печатает свою идею
    confirming_idea       = State()    # подтверждение выбранной/сгенерированной идеи

    choosing_theme         = State()   # тёмный / светлый фон
    choosing_background    = State()   # номер фона по превью

    choosing_sticker_pack  = State()   # выбор пака стикеров

    choosing_song          = State()   # фоновая музыка
    choosing_banner        = State()   # баннер-концовка

    confirming_generation  = State()   # финальное подтверждение перед рендером
