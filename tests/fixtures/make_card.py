"""Generic SOP card for the 20 s synthetic test video (photos come from raw-footage seconds)."""
from tutorial_editor.card import (BatchItem, CardStep, CardUi, Crop, NeverItem, PhotoRef, SOPCard, TimelineNode)


def ref(t: float) -> PhotoRef:
    return PhotoRef(file="", scene=-1, time=t, crop=Crop(x=0.1, y=0.1, w=0.7, h=0.75))


def card() -> SOPCard:
    return SOPCard(
        kicker="how to make", title="Iced Tea Batch", short_title="Iced Tea", flow=["Steep", "Chill", "Store"], hero=ref(1),
        batch_label="One batch", batch=[BatchItem(value="2 gal", detail="finished tea"), BatchItem(value="4 oz", detail="tea leaves (113 g)")],
        tools=["Large pitcher", "Timer", "Strainer"],
        steps=[CardStep(title=f"Step {i + 1}", body="Do the **first thing**, then the second thing.", check="Looks right",
                        phase=min(i // 3, 2), photo=ref(1 + i * 2)) for i in range(8)],
        before_you_start=["wash hands for 20 sec", "sanitize the station"],
        batch_flow_label="Work in batches", batch_flow=["Steep all", "Chill all", "Label"], back_kicker="almost done",
        timeline_title="Ready in 2 hours", timeline_note="chill before serving",
        timeline=[TimelineNode(kicker="Day 0", title="Brew", detail="steep 5 min", tone="green"),
                  TimelineNode(kicker="+2 hrs", title="Ready", detail="fully chilled", tone="green"),
                  TimelineNode(kicker="Day 1", title="Use by", detail="end of day", tone="caramel")],
        never_title="Never", never=[NeverItem(title="Over-steep", detail="set a timer", photo=ref(12), icon="none"),
                                    NeverItem(title="Leave it out", detail="keep it cold", photo=ref(-1), icon="thermometer")],
        ui=CardUi(tools="Tools", before_you_start="Before you start:", turn_over="Turn over for steps {first}–{last} →",
                  watch_title="Watch it", watch_note="scan for the training video", confidential="Confidential", sop="SOP",
                  effective="Effective", approved_by="Approved by"))
