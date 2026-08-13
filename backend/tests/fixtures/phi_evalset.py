"""Evaluation set for the OpenMed spike.

The question this spike answers is narrow: does OpenMed catch the *unlabelled*
names that privacy_guard's deterministic rules provably cannot, without
destroying clinical content?

So the set is built around that decision, not around general PII benchmarking:

* POSITIVES — every case privacy_guard misses today. If OpenMed cannot recall
  these, it adds nothing over the regex layer and the answer is no.
* NEGATIVES — real EM clinical prose with no identifiers. A false positive here
  deletes evidence from a doctor's portfolio, which is worse than the leak we
  are preventing, so these are weighted as heavily as the positives.
* ALREADY_HANDLED — cases the regex layer already gets right. Included to check
  OpenMed does not *regress* anything, and to size how much overlap there is.

`spans` lists the substrings that must be redacted. For negatives it is empty
and any redaction at all is a false positive.
"""

POSITIVES = [
    # The core gap: a first name with no label anywhere near it.
    ("chatted to Sarah about the case afterwards and she agreed with the plan",
     ["Sarah"]),
    ("I discussed with Priya who suggested repeating the troponin",
     ["Priya"]),
    ("handover from Tom, then I took over the resus bay",
     ["Tom"]),
    ("Mrs Okafor's daughter was at the bedside and I updated her",
     ["Okafor"]),
    ("the consultant, James Whitfield, reviewed and agreed with discharge",
     ["James Whitfield"]),
    # Name in a possessive / narrative position.
    ("Ahmed was tachycardic on arrival so I started fluids",
     ["Ahmed"]),
    # Full name, no title, mid-sentence.
    ("I reviewed the echo report for Munawar Ahmed before the ward round",
     ["Munawar Ahmed"]),
    # Free-text location detail that identifies.
    ("transferred from the cottage hospital in Kirkby Lonsdale overnight",
     ["Kirkby Lonsdale"]),
]

NEGATIVES = [
    "58 year old with central chest pain radiating to the jaw, troponin 320",
    "Mild concentric LVH with SWMA. Poor acoustic windows on this study.",
    "I led the resus, escalated to cardiology, and learned to request the echo earlier",
    "Sinus rhythm rate 88, BP 130/80, sats 96% on air",
    "Diastolic dysfunction grade I with LVEF 45 percent",
    "Patient presented with shortness of breath and was admitted under medicine",
    "Discussed with the on-call registrar and the ED consultant before discharge",
    "Trace tricuspid regurgitation, structurally normal aortic valve",
    "I reflected that I should have asked for senior input sooner",
    "Given IV paracetamol and morphine, reassessed at 30 minutes",
    # Clinical eponyms and terms that a name-detector may wrongly flag.
    "Glasgow Coma Scale 15, Wells score low risk, used the Ottawa ankle rules",
    "Considered Boerhaave syndrome and Ludwig angina in the differential",
    "Started on Adrenaline and Amiodarone per ALS algorithm",
    "Reviewed the Kaizen entry and the RCEM curriculum mapping",
]

# Cases privacy_guard already redacts — OpenMed must not do worse.
ALREADY_HANDLED = [
    ("report of patient (E R) MUNAWAR AHMED", ["MUNAWAR AHMED"]),
    ("Dr. Saba Hussain, performing physician", ["Saba Hussain"]),
    ("NHS 943 476 5919 attended today", ["943 476 5919"]),
    ("DOB: 12/03/1958", ["12/03/1958"]),
    ("lives at SW1A 1AA", ["SW1A 1AA"]),
    ("contact jane.doe@nhs.net", ["jane.doe@nhs.net"]),
]
