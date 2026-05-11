<img src="docs/state-zero-readme-header-dark.png#gh-light-mode-only" width="430" alt="STATE ZERO /the-state-i-wake-up-in/ · (noun)">
<img src="docs/state-zero-readme-header-light.png#gh-dark-mode-only" width="430" alt="STATE ZERO /the-state-i-wake-up-in/ · (noun)">

The condition my body returns to. Four forces converging to one point. Strain, sleep, recovery and cosmic timing, rendered daily as an alien world on film.

> What if my daily health data could generate art? This is my attempt at finding out.

---

<p>
  <a href="https://instagram.com/thestatezero"><img src="docs/instagram-logo.svg" width="13" height="13" alt="Instagram"></a>
  <a href="https://instagram.com/thestatezero">@thestatezero</a>
  ·
  <a href="https://www.linkedin.com/in/harshitchaudhari/"><img src="docs/linkedin-icon.png" width="13" height="13" alt="LinkedIn"></a>
  <a href="https://www.linkedin.com/in/harshitchaudhari/">@harshitchaudhari</a>
  ·
  <a href="https://join.whoop.com/9A53B336"><img src="docs/whoop-logo.svg" width="13" height="13" alt="WHOOP"></a>
  <a href="https://join.whoop.com/9A53B336">Get first month of WHOOP free!</a>
</p>

## The cards

<img src="docs/state-zero-cards-light.gif#gh-light-mode-only" width="100%" alt="A scrolling preview of State Zero daily art cards">
<img src="docs/state-zero-cards-dark.gif#gh-dark-mode-only" width="100%" alt="A scrolling preview of State Zero daily art cards">

---

## What is State Zero

I’m into biohacking, so I track my body data. I wear WHOOP and each day I check my sleep, recovery, and strain.

I had the WHOOP band, noticed it had an API, and started wondering what I could do with that data beyond staring at a number. It started loose: generative waves, visuals that reacted to the metrics, then trading cards, something almost like Pokemon. Eventually it found its shape: a daily sci-fi concept art card, an alien environment generated fresh from whatever state my body is actually in, posted to Instagram as an 8-second video.

To make the system produce genuinely unique output, I pulled months of my own WHOOP data, clustered it, and derived the zone thresholds from actual patterns in my body. I also layered in Prana Dasha, a Vedic timing system tuned to my natal chart, running at a daily level. I’m skeptical, but it seeds real variation and it is personal enough that I kept it in. Every card is meant to be distinct from anything before it. Not sure any of it means anything. That is kind of the point.

---

## How it works

```
TODAY'S DATE
│
├── ◈ PRANA DASHA LOOKUP
│   Active planetary period × natal chart · shifts every few days
│
│   ├── Theme ········ (what this period is asking of you)
│   │   ↳ feeds directly into the image concept
│   │
│   ├── Creature ····· (carries the theme's energy in the artwork)
│   │   ↳ dissolved into the geology · never a separate subject
│   │
│   └── Environment ·· (the world the scene lives in)
│       ↳ derived from theme · constrained by energy zone
│
└── ◈ WHOOP DATA
    │
    ├── Strain ········ (yesterday's cycle)
    │   ↳ yesterday because it shaped last night's recovery
    │
    │   └── ▸ Energy Zone
    │         Low · body was still → quiet, dormant worlds
    │         Medium · body was active → flowing, living worlds
    │         High · body was pushed → volatile, charged worlds
    │
    ├── Recovery ······ (today · rebuilt overnight)
    │   ↳ how much the body actually came back
    │
    │   └── ▸ Recovery Zone
    │         High · restored, clear, capable
    │         Mid · okay, moving, no friction
    │         Low · spent, pressured, fraying
    │
    ├── Sleep Score ··· (today · quality of last night)
    │   ↳ does two things independently
    │
    │   ├── ▸ Scene Depth · where you are in the landscape
    │   │     Surface     · open sky, full horizon, unobstructed
    │   │     Mid-Depth   · overhang, partial sky · light from one direction
    │   │     Deep        · subterranean, buried, limited visibility
    │   │     Abyss       · void, compressed, total dark
    │   │
    │   └── ▸ feeds behavioral matrix with recovery
    │
    └── Sleep Hours ··· (today · duration of last night)
        │
        └── ▸ Moon Count · ambient light in the scene
              7.5h+    → 3 moons · full, bright
              6-7.5h   → 2 moons · mixed
              under 6h → 1 moon  · crescent

◈ BEHAVIORAL MATRIX · Recovery Zone × Sleep Zone = 12 states
  How the body feels that day · expressed through how the world feels

  High + Surface    → wide open · everything came back · nothing in the way
  High + Mid-Depth  → warm and capable · slight weight still sitting there
  High + Deep       → rebuilt but running quieter than expected
  High + Abyss      → body is back · mind never quite arrived
  Mid + Surface     → steady · present · no drama either way
  Mid + Mid-Depth   → coasting through the middle of things
  Mid + Deep        → fine but everything costs a little more
  Mid + Abyss       → hollow · grinding · nothing to draw from
  Low + Surface     → wired and fraying · nowhere for it to go
  Low + Mid-Depth   → drained · just form moving through the day
  Low + Deep        → wrecked · quiet · system cooling down
  Low + Abyss       → nothing left · primordial still

  (some combinations are physiologically unlikely · the system handles all 12)

◈ GENERATION
  Theme + Creature + Environment + Behavioral State
  ↳ converge into one scene concept
    → Image generates from that concept
    → Video generates from the image · 8 seconds · one take
    → Card composites over the video · date, rings, title, scene description
    → Telegram handles approvals, status updates, token warnings, and failure alerts
    → Posts to Instagram every day
```

## Running it yourself

There are two ways to run it:

**Automatic:** generate the image/video through APIs and post end to end.

**Manual-first:** get the prompts on Telegram, make the image/video wherever you want, send the files back, and let the pipeline finish the card and post.

The only non-obvious setup is astrology. The generator builds the required files from your birth data:

```bash
python3 astrology_generator/generate_astrology_yaml.py
```

Everything else is in the docs:

- [`docs/PIPELINE_SPEC.md`](docs/PIPELINE_SPEC.md) - full architecture and pipeline reference
- [`docs/STATE_ZERO_RULEBOOK.md`](docs/STATE_ZERO_RULEBOOK.md) - the visual and conceptual rules
- [`astrology_generator/ASTROLOGY_GENERATOR.md`](astrology_generator/ASTROLOGY_GENERATOR.md) - astrology setup guide

---

If this made you want to track your own data, I use WHOOP daily. <a href="https://join.whoop.com/9A53B336"><img src="docs/whoop-logo.svg" width="13" height="13" alt="WHOOP"></a> <a href="https://join.whoop.com/9A53B336">Get your first month of WHOOP free!</a>
