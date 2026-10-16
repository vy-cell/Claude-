"""Hand-written plan exercising every scene kind, for a 20 s synthetic test video (generic content)."""
from tutorial_editor.models import Callout, Entry, Inset, Pointer, Scene, Step, UiText, VideoPlan


def S(kind, **kw) -> Scene:
    base = dict(kind=kind, voiceover="", caption="", heading="", subheading="", section="", step_code="",
                chapter_index=0, clip_start=-1, clip_end=-1, still_time=-1, callouts=[], pointers=[], insets=[],
                entries=[], wrong_time=-1, wrong_label="", right_time=-1, right_label="")
    base.update(kw)
    return Scene(**base)


def plan() -> VideoPlan:
    return VideoPlan(
        title="Iced Tea Batch", series_label="BACK OF HOUSE TRAINING",
        ui=UiText(step="STEP", of="OF", wrong="WRONG", right="RIGHT", equipment="EQUIPMENT", ingredients="INGREDIENTS"),
        steps=[Step(name="Steep", still_time=2), Step(name="Chill", still_time=8), Step(name="Store", still_time=14)],
        scenes=[
            S("title", voiceover="Iced tea batch. Back of house training.", heading="Iced Tea Batch", still_time=1),
            S("overview", voiceover="Three steps: steep, chill, store.", heading="Iced Tea Batch",
              subheading="BACK OF HOUSE", caption="3 steps: steep, chill & store"),
            S("requirements", voiceover="Here is what you need.", heading="What You Need", subheading="ONE BATCH",
              caption="One batch: 2 gallons",
              entries=[Entry(kicker="", title="Large pitcher", detail=""), Entry(kicker="", title="Timer", detail=""),
                       Entry(kicker="", title="Tea leaves", detail="4 oz (113 g)"), Entry(kicker="", title="Hot water", detail="1 gal (3.8 L)")]),
            S("checklist", voiceover="Safety first.", heading="Safety First", subheading="BEFORE YOU START", section="SAFETY",
              caption="Keep it clean · ≤ 41°F (5°C)",
              entries=[Entry(kicker="WASH", title="Hands 20 sec", detail="then gloves on"),
                       Entry(kicker="SANITIZE", title="Clean station", detail="before every batch")]),
            S("chapter", voiceover="Step one. Steep the tea.", heading="Steep the Tea", chapter_index=1, still_time=3),
            S("footage", voiceover="Pour hot water over the leaves and start the timer.", caption="Hot water · start the timer",
              heading="Pour the water", step_code="1A", section="POUR THE WATER", clip_start=4, clip_end=10,
              callouts=[Callout(label="USE", value="HOT WATER", tone="do"), Callout(label="NEVER", value="BOILING", tone="dont"),
                        Callout(label="", value="5 MIN", tone="note")],
              insets=[Inset(x=0.1, y=0.1, w=0.3, h=0.4, position="right", label="TIMER")],
              pointers=[Pointer(text="fill line", x=0.6, y=0.5, side="left")]),
            S("compare", voiceover="Wrong: too long. Right: five minutes.", heading="Steeping Time", subheading="RIGHT VS. WRONG",
              caption="Right: 5 minutes, then remove", wrong_time=12, wrong_label="BITTER", right_time=15, right_label="5 MIN"),
            S("closing", voiceover="Great job.", heading="YAY", still_time=18),
        ],
        facts_used=["5 minutes - notes"], open_questions=[])
