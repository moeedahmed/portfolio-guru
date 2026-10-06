"""Loopback-only Kaizen contract fake, generated from our selector map."""
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from kaizen_form_filer import (
    COMMON_HEADER_FIELD_MAP, FORM_FIELD_MAP, FORM_UUIDS, FORM_SCHEMAS,
    KAIZEN_URL_PATTERNS, STAGE_SELECT_VALUES, QIAT_STAGE_VALUES, filing_form_base,
)
from esle_domains import ESLE_DOMAIN_OPTIONS

USERNAME = "synthetic-doctor@example.invalid"
PASSWORD = "dummy-fake-password"
HOSTS = {"eportfolio.rcem.ac.uk", "auth.kaizenep.com", "kaizenep.com"}


def controls(form_type):
    """One control per DOM id; aliases deliberately share that control."""
    schema = FORM_SCHEMAS.get(form_type, FORM_SCHEMAS.get(filing_form_base(form_type), {}))
    specs = {f["key"]: f for f in schema.get("fields", [])}
    result = {}
    for key, target in {**COMMON_HEADER_FIELD_MAP, **FORM_FIELD_MAP.get(form_type, {})}.items():
        dom_id = target["dom_id"] if isinstance(target, dict) else target
        spec = specs.get(key, {})
        kind = "textarea"
        options = spec.get("options", [])
        if dom_id in {"startDate", "endDate"} or "date" in key:
            kind = "date"
        elif key == "stage_of_training":
            kind = "select"
            mapping = QIAT_STAGE_VALUES if dom_id == "415a72f2-7cf3-420a-bee4-9a7aed746612" else STAGE_SELECT_VALUES
            options = list(mapping.items())
        elif key in {"us_application", "domains_of_performance"}:
            kind = "widget"
            if key == "domains_of_performance":
                options = ESLE_DOMAIN_OPTIONS
            if not options:
                options = next(f["options"] for s in FORM_SCHEMAS.values() for f in s.get("fields", []) if f["key"] == key and f.get("options"))
        elif options:
            kind = "select"
        elif key == "event_description" or spec.get("type") in {"number", "text"}:
            kind = "input" if key == "event_description" or spec.get("type") == "number" else "textarea"
        result.setdefault(dom_id, {"key": key, "kind": kind, "options": options})
    return result


def form_page(form_type, values=None):
    values = values or {}
    elements = []
    for dom_id, spec in controls(form_type).items():
        attrs = f'id="{escape(dom_id)}" name="{escape(dom_id)}"'
        value = values.get(dom_id, "")
        kind = spec["kind"]
        if kind == "select":
            options = ['<option value="">Please select</option>']
            for item in spec["options"]:
                label, val = item if isinstance(item, tuple) else (item, item)
                selected = ' selected' if value == val else ''
                options.append(f'<option value="{escape(val)}"{selected}>{escape(label)}</option>')
            control = f'<select {attrs}>{"".join(options)}</select>'
        elif kind == "widget":
            options = ''.join(f'<label><input type="checkbox" name="{escape(dom_id)}" value="{escape(opt)}"' + (' checked' if opt in value else '') + f'>{escape(opt)}</label>' for opt in spec["options"])
            control = f'<div id="{escape(dom_id)}"><button type="button">Choose options</button>{options}</div>'
        elif kind == "textarea":
            control = f'<textarea {attrs}>{escape(value)}</textarea>'
        else:
            # Kaizen dates are text inputs, not HTML ISO-only date controls.
            control = f'<input {attrs} value="{escape(value)}" type="text">'
        elements.append(f'<div><label for="{escape(dom_id)}">{escape(spec["key"])}</label>{control}</div>')
    return f'''<!doctype html><html><body><p>Saved draft</p>
<form method="post" action="/save/{FORM_UUIDS[form_type]}">
{''.join(elements)}
<button type="submit" formaction="/send">Submit / Send to assessor</button>
<button type="submit">Save as draft</button>
</form></body></html>'''


class FakeKaizen:
    def __init__(self):
        self.login_attempts = 0
        self.submit_clicks = 0
        self.drafts = []
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, body="", status=200, **headers):
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name.replace("_", "-"), value)
                self.end_headers()
                self.wfile.write(data)

            def redirect(self, url, **headers):
                self.respond(status=302, Location=url, **headers)

            def do_GET(self):
                host = self.headers.get("X-Fake-Host")
                path = urlsplit(self.path).path
                owner.requests.append((host, path))
                if host == "eportfolio.rcem.ac.uk":
                    return self.redirect("https://auth.kaizenep.com/sign-in")
                if host == "auth.kaizenep.com":
                    return self.respond('<form method="post" action="/username"><input name="login"><button type="submit">Sign in</button></form>')
                if host != "kaizenep.com":
                    return self.respond(status=403)
                if "fake_session=synthetic" not in self.headers.get("Cookie", ""):
                    return self.redirect("https://auth.kaizenep.com/sign-in")
                if path == "/activities":
                    links = ''.join(f'<a href="{d["url"]}">{d["form_type"]} saved draft</a>' for d in owner.drafts)
                    return self.respond(f'<h1>Activities</h1>{links}')
                if path.startswith("/events/new-section/"):
                    uuid = path.rsplit("/", 1)[1]
                    form_type = next((ft for ft, form_uuid in FORM_UUIDS.items() if form_uuid == uuid), None)
                    if form_type:
                        return self.respond(form_page(form_type))
                if path.startswith("/events/fillin/"):
                    doc_id = path.rsplit("/", 1)[1]
                    draft = next((d for d in owner.drafts if d["doc_id"] == doc_id), None)
                    if draft:
                        return self.respond(form_page(draft["form_type"], draft["values"]))
                self.respond(status=404)

            def do_POST(self):
                host = self.headers.get("X-Fake-Host")
                values = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode(), keep_blank_values=True)
                if host == "auth.kaizenep.com" and self.path == "/username":
                    login = escape(values.get("login", [""])[0])
                    return self.respond(f'<form method="post" action="/sign-in"><input type="hidden" name="login" value="{login}"><input name="password" type="password"><button type="submit">Sign in</button></form>')
                if host == "auth.kaizenep.com" and self.path == "/sign-in":
                    owner.login_attempts += 1
                    if values.get("login") == [USERNAME] and values.get("password") == [PASSWORD]:
                        return self.redirect("https://kaizenep.com/activities", Set_Cookie="fake_session=synthetic; Domain=kaizenep.com; Path=/; Secure; HttpOnly")
                    return self.respond('<p id="error-message">Authentication failed: invalid credentials.</p>')
                if host != "kaizenep.com" or "fake_session=synthetic" not in self.headers.get("Cookie", ""):
                    return self.respond(status=403)
                if self.path == "/send":
                    owner.submit_clicks += 1
                    return self.respond("Sent to assessor")
                if self.path.startswith("/save/"):
                    uuid = self.path.rsplit("/", 1)[1]
                    form_type = next(ft for ft, form_uuid in FORM_UUIDS.items() if form_uuid == uuid)
                    specs = controls(form_type)
                    received = {key: vals if specs[key]["kind"] == "widget" else vals[0].replace("\r\n", "\n") for key, vals in values.items()}
                    for key, spec in specs.items():
                        if spec["kind"] == "widget":
                            received.setdefault(key, [])
                    doc_id = f"synthetic-document-{len(owner.drafts) + 1}"
                    url = KAIZEN_URL_PATTERNS["edit_draft"].format(doc_id=doc_id, autosave_id=len(owner.drafts) + 1)
                    owner.drafts.append(dict(form_type=form_type, values=received, doc_id=doc_id, url=url))
                    return self.redirect(url)
                self.respond(status=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
