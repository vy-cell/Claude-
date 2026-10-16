from tutorial_editor.card import CardPlan, CardStep, Crop, Fact, Mark


def card() -> CardPlan:
    icons = ["drop", "scale", "clock", "cup", "tag", "thermometer"]
    return CardPlan(
        title="Iced Tea Batch", subtitle="Back of house · quick reference",
        key_facts=[Fact(label="Batch", value="2 gallons"), Fact(label="Tea", value="4 oz · 113 g")],
        steps=[CardStep(title=f"Step {i + 1}", photo_time=1 + i * 3, photo_crop=Crop(x=0.2, y=0.2, w=0.5, h=0.6),
                        marks=[Mark(kind=["ring", "arrow", "box"][i % 3], x=0.45, y=0.5, w=0.1, h=0.1, label="fill line")] if i % 2 == 0 else [],
                        instructions=["Do the first thing", "Then the second thing"], key_value="5 min" if i % 2 else "",
                        icon=icons[i], warning="Never skip this" if i % 3 == 0 else "") for i in range(6)],
        rules=[Fact(label="Wash", value="Hands 20 sec, then gloves"), Fact(label="Store", value="≤ 41°F (5°C), use within 24 hrs")],
        footer_note="Questions? Ask your shift lead.")
