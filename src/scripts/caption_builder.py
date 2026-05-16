HASHTAGS = [
    '#GenerativeArt',
    '#AIArt',
    '#DataArt',
    '#DataVisualization',
    '#CreativeCoding',
    '#GenerativeAI',
    '#AIGenerated',
    '#AlgorithmicArt',
    '#WHOOP',
    '#WHOOPData',
    '#QuantifiedSelf',
    '#SelfTracking',
    '#Biohacking',
    '#Biohacker',
    '#DigitalHealth',
    '#HealthTech',
    '#WearableTech',
    '#DigitalArt',
]


def build_hashtags(date_str: str) -> str:
    return ' '.join(HASHTAGS)


def build_caption(metadata: dict, daily_data: dict, run_date: str | None = None) -> str:
    date_str = daily_data.get('date') or run_date or ''
    date_display = daily_data.get('date_display') or metadata.get('date_display') or date_str
    title = metadata.get('title', 'UNKNOWN TITLE')
    hashtags = build_hashtags(date_str)
    return (
        f"{title} · {date_display}\n\n"
        "What if your daily health data could generate art?\n\n"
        "My daily @whoop data (sleep, recovery, yesterday's strain) runs through a metrics engine "
        "I designed and I layered in Prana Dasha too, a Vedic astrology system that works at a "
        "daily level tuned to my natal chart. I'm skeptical, but it seeds real variation and it's "
        "personal enough that I kept it in.\n\n"
        "Not sure any of this means anything. That's kind of the point.\n\n"
        f"{hashtags}"
    )
