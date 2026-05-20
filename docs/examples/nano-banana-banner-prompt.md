# Nano Banana: промпты для баннера Agent Driver

Четыре радикально разных направления. Выберите одно или скомбинируйте.

Рекомендуемая модель для текста на изображении: **Nano Banana 2** (Gemini 3.1 Flash Image) —
наиболее точный рендер текста, поддерживает до 14 reference images.

---

## Направление 1 — «Cockpit» (скорость, напряжение, в движении)

Эксплуатирует слово **Driver**: гонщик, кокпит, момент старта. Совершенно нестандартно для developer tool.

```text
Cinematic wide-angle hero banner for a Python developer tool called "Agent Driver".

Scene: interior of a sleek, fictional racing cockpit viewed from just behind the driver's seat.
The cockpit is not mechanical — it is made of glowing dark glass and floating holographic panels
displaying real-time event logs, tool call traces, and checkpoint graphs in electric cyan and white.
Through the curved windshield: a night city circuit, motion-blurred streaks of cyan and blue light,
rain-slicked asphalt reflecting neon, a dramatic low-horizon vanishing point.

No driver face visible. No steering wheel. No real-world car branding.
The cockpit panels show abstract code traces and node graphs, not readable text.

Light sources: panels glow cyan (#22D3EE) and ice blue (#93C5FD); windshield reflects amber and magenta
from street lamps; deep shadow fills 60% of the frame.

Mood: kinetic, controlled, dangerous precision. Like a feature film still, not a game loading screen.
Photography-level realism with a subtle color grade: teal shadows, warm highlights.

No typography. No logos. No HUD UI chrome. No racing helmets. Leave clear space in the lower-left for a text overlay.
Aspect ratio 16:9, ultra-wide cinematic crop, 4K sharp.
```

---

## Направление 2 — «Control Room» (командный пункт, масштаб)

Несколько агентов-операторов управляют сложной инфраструктурой — визуализация multi-agent orchestration.

```text
Dramatic editorial photo-illustration for a Python agent runtime library banner.

Wide-angle view of a vast, near-future mission control room, seen from a slightly elevated angle.
Rows of curved dark desks lit only by monitor glow. On each monitor: abstract node graphs, event
streams, tool call queues — no readable code, only visual patterns of cyan and white lines.

The enormous back wall is floor-to-ceiling screens showing a live graph of agent checkpoints:
glowing nodes, green "healthy" pulses, amber "pending" indicators. The graph shifts and breathes
like a living organism. No human operators visible — the room operates itself.

Color palette: near-black environment (#080C14), electric cyan (#22D3EE) dominant accent,
cold white secondary, single amber node for tension, zero warmth otherwise.

Fog/haze near the ceiling. Dramatic depth of field: front desk sharp, background soft.
Film grain texture, cinematic color grade (teal-orange complementary, heavy teal pull).
No text, no logos, no readable labels on screens. Photorealistic.

Leave the left third of the banner relatively empty (dark) for text overlay.
Aspect ratio 1.91:1 (Open Graph), 1200×630.
```

---

## Направление 3 — «Deep Space Launch» (масштаб, одиночество, бесконечность)

Агент как зонд, отправляемый в неизвестность — метафора autonomous execution.

```text
Full-bleed space photography banner for developer tool "Agent Driver".

Macro view of a glowing cyan-blue sphere (not Earth, not a real planet — abstract, geometric, perfect)
against absolute black space. The sphere fills 55% of the frame, left-offset. Its surface is not rocky —
it is a smooth membrane of deep navy glass lit from within, with faint hexagonal grid lines that pulse
like a heartbeat: the checkpoint lattice of a running agent system.

From the sphere's equator: a single ultra-thin ring of electric cyan light (like Saturn's ring but sharper,
more precise) extends into empty space. Along the ring: tiny glowing dots at irregular intervals
— in-flight tool calls and events.

Background: pure void with a faint dust nebula smear of dark indigo and cobalt, no stars, no galaxy clichés.
A hairline horizon glow behind the sphere: cold white-blue rim light.

Mood: solitary, vast, quietly powerful. Like a still frame from a prestige sci-fi film (Arrival, Blade Runner 2049),
not a video game cutscene. No astronauts. No spaceships. No text.

Right third: nearly empty black, space for text overlay.
Ultra-high detail, cinematic 2.39:1 aspect ratio cropped to 16:9 for banner use. IMAX quality.
```

---

## Направление 4 — «Terminal Noir» (атмосфера, ирония, авторский стиль)

Самое нестандартное. Эстетика неонуара — детектив в мире, где агенты работают сами по себе.

```text
Noir-cinematic editorial illustration for a Python AI agent runtime called "Agent Driver".

Scene: A dark office at 3am. Raining outside. On the desk: a single glowing terminal monitor
(no screen content visible, just a diffuse green-cyan glow illuminating the desk surface).
No human present — the chair is empty. The agent ran without anyone watching.

On the desk surface, light from the monitor illuminates:
- a coffee cup with cold coffee,
- a tangle of ethernet cables,
- a faded sticky note (blank, unreadable),
- ambient reflections in rain-slicked glass from a city outside.

Through the window: blurred neon signs in cyan and amber, rain streaks, a low foggy skyline.
Deep shadow with a single hard light source from the monitor only.

Film noir aesthetic: heavy contrast, deep blacks, single cyan key light, warm amber background glow.
Photorealistic or high-end digital painting — neither cartoon nor flat illustration.
Subtle film grain. Anamorphic lens flare from the monitor edge only.

No people. No text in the image. No AI robot imagery. No floating UI panels.
Leave upper-right corner in relative darkness for headline text overlay.
Aspect ratio 16:9, widescreen, moody editorial quality.
```

---

## Добавьте к любому промпту для текста на баннере (Nano Banana 2)

Вставьте в конец выбранного промпта перед генерацией:

```text
TYPOGRAPHY OVERLAY (render in image, do not skip):
- Top-left area (avoid the main scene focal point):
  Large bold sans-serif: "Agent Driver"
  Smaller regular weight below: "Durable agent runtime for Python"
- Use a geometric sans-serif typeface (Inter, Geist, or similar).
- Text color: pure white with a 2px blur drop shadow in deep navy.
- Perfect spelling, no warped letters, no repeated words, no extra text.
```

---

## Негативный промпт

```text
generic tech startup gradient, purple and pink gradient, rainbow, stock photo people,
robot mascot, cartoon, clipart, floating UI windows, browser chrome, lens flare storm,
lens distortion, watermark, blurry text, misspelled words, AI brain illustration,
circuit board pattern, typical "dark tech banner", planets with continents, readable code,
holographic woman, cyborg, power suit
```
