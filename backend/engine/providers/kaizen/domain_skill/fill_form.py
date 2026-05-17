"""Kaizen form filler — standalone script, called by KaizenProvider.
Takes form UUID and field data from JSON file, executes in browser-harness.
"""
import json, os, sys, time

cfg = json.loads(os.environ.get("KAIZEN_FILL_CFG", "{}"))
form_uuid = cfg["form_uuid"]
fields = cfg.get("fields", {})

goto_url("https://kaizenep.com/events/new-section/" + form_uuid)
time.sleep(8)

if "startDate" in fields and fields["startDate"]:
    cdp("Runtime.evaluate", expression="var el=document.getElementById('startDate');if(el){el.focus();el.select()}", awaitPromise=False)
    time.sleep(0.3)
    cdp("Input.insertText", text=fields["startDate"])
    time.sleep(0.5)
    for k in ["keyDown","keyUp"]:
        cdp("Input.dispatchKeyEvent", type=k, key="Tab", code="Tab", windowsVirtualKeyCode=9)

if "endDate" in fields and fields["endDate"]:
    cdp("Runtime.evaluate", expression="var el=document.getElementById('endDate');if(el){el.focus();el.select()}", awaitPromise=False)
    time.sleep(0.3)
    cdp("Input.insertText", text=fields["endDate"])
    time.sleep(0.5)
    for k in ["keyDown","keyUp"]:
        cdp("Input.dispatchKeyEvent", type=k, key="Tab", code="Tab", windowsVirtualKeyCode=9)

if "description" in fields and fields["description"]:
    desc = fields["description"].replace("\\", "\\\\").replace("'", "\\'")
    cdp("Runtime.evaluate", expression="var el=document.getElementById('event-description');if(el){var setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;setter.call(el,'" + desc + "');el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));el.blur()}", awaitPromise=False)

if "stage" in fields and fields["stage"]:
    stage_vals = {"higher": "string:0669c338-e695-40f9-8fae-aee2ee7d68e1"}
    av = stage_vals.get(fields["stage"].lower(), "string:0669c338-e695-40f9-8fae-aee2ee7d68e1")
    cdp("Runtime.evaluate", expression="var el=document.getElementById('e0864e88-62cf-43aa-a9e5-51abd98a1cce');if(el){el.value='" + av + "';el.dispatchEvent(new Event('change',{bubbles:true}))}", awaitPromise=False)
    time.sleep(5)

print("filled")
