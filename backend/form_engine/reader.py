"""Offline structural reading with HTMLParser; scripts and values are never run."""
import hashlib
import re
from html.parser import HTMLParser

from .models import Field, FormMap, Option

ALIASES = {
    "date": {"date", "date of event", "date occurred on", "event date"},
    "description": {"description", "description optional", "case description", "case to be discussed", "case observed"},
    "reflection": {"reflection", "reflection of event"},
    "stage_of_training": {"stage of training", "training stage"},
}


def meaning(label):
    normal = " ".join(re.findall(r"\w+", label.lower()))
    return next((key for key, labels in ALIASES.items() if normal in labels), None)


class Node:
    def __init__(self, tag, attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def text(self):
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c for c in self.children).split())

    def ancestors(self):
        current = self.parent
        while current:
            yield current
            current = current.parent


class DOM(HTMLParser):
    VOID = {"input", "br", "hr", "img", "meta", "link", "area", "base", "embed", "param", "source", "track", "wbr", "col"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node("root")
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for node in (self.current, *self.current.ancestors()):
            if node.tag == tag and node.parent:
                self.current = node.parent
                break

    def handle_data(self, data):
        self.current.children.append(data)


def _label(node, nodes):
    def label_text(label):
        def parts(n):
            for c in n.children:
                if isinstance(c, str):
                    yield c
                elif c.tag not in {"input", "textarea", "select", "script", "style"}:
                    yield from parts(c)
        return " ".join(" ".join(parts(label)).split())
    attrs = node.attrs
    if attrs.get("aria-label"):
        return attrs["aria-label"].strip()
    if attrs.get("aria-labelledby"):
        return " ".join(label_text(n) for key in attrs["aria-labelledby"].split()
                        for n in nodes if n.attrs.get("id") == key)
    labels = [label_text(n) for n in nodes if n.tag == "label" and attrs.get("id") and n.attrs.get("for") == attrs["id"]]
    if labels:
        return " ".join(labels)
    return next((label_text(p) for p in node.ancestors() if p.tag == "label"), "")


def _section(node):
    for parent in node.ancestors():
        if parent.tag in {"fieldset", "section"}:
            title = parent.attrs.get("aria-label") or next(
                (c.text() for c in parent.walk() if c.tag in {"legend", "h1", "h2", "h3", "h4"}), "")
            if title:
                return title
    return ""


def _hints(node, label):
    return {**{k: node.attrs[k] for k in ("id", "name") if node.attrs.get(k)}, **({"label": label} if label else {})}


def parse_form(html, platform, form_id):
    nodes = list(DOM(html).root.walk())
    has_forms = any(n.tag == "form" for n in nodes)
    controls, seen_radios, fields = {}, set(), []
    for node in nodes:
        if node.tag not in {"input", "textarea", "select"}:
            continue
        if has_forms and not any(p.tag == "form" for p in node.ancestors()):
            continue
        kind = node.attrs.get("type", "text").lower() if node.tag == "input" else node.tag
        if kind not in {"text", "textarea", "select", "radio", "checkbox", "date", "file", "email", "tel", "url", "search", "number"}:
            continue
        kind = kind if kind in {"textarea", "select", "radio", "checkbox", "date", "file"} else "text"
        group = [node]
        if kind == "radio" and node.attrs.get("name"):
            name = node.attrs["name"]
            owner = next((p for p in node.ancestors() if p.tag == "form"), None)
            if (name, owner) in seen_radios:
                continue
            seen_radios.add((name, owner))
            group = [n for n in nodes if n.tag == "input" and n.attrs.get("type", "").lower() == "radio" and n.attrs.get("name") == name
                     and next((p for p in n.ancestors() if p.tag == "form"), None) is owner]
        label, section = _label(node, nodes), _section(node)
        if kind == "radio":
            label = node.attrs.get("aria-label") or section or label
        hints = {"name": node.attrs["name"], **({"label": label} if label else {})} if len(group) > 1 else _hints(node, label)
        key = (node.attrs.get("name") if kind == "radio" else node.attrs.get("id")) or node.attrs.get("id") or node.attrs.get("name")
        key = key or "auto_" + hashlib.sha256(f"{section}|{label}|{kind}".encode()).hexdigest()[:16]
        options = ()
        if kind in {"select", "radio"}:
            choices = [n for n in node.walk() if n.tag == "option"] if kind == "select" else group
            options = tuple(Option(n.attrs.get("value", n.text() if kind == "select" else "on"),
                                   n.text() if kind == "select" else _label(n, nodes),
                                   "disabled" in n.attrs or any("disabled" in p.attrs for p in n.ancestors()),
                                   _hints(n, _label(n, nodes)) if kind == "radio" else {}) for n in choices)
        restrictions = tuple(k for k in ("disabled", "readonly", "multiple") if any(
            k in n.attrs or (k == "disabled" and any(k in p.attrs for p in n.ancestors())) for n in group))
        concept = meaning(label)
        fields.append(Field(key, label, kind, options, any("required" in n.attrs or n.attrs.get("aria-required") == "true" for n in group),
                            hints, section, restrictions, "candidate" if concept else "unmapped", concept))
        controls[key] = group
    return FormMap(platform, form_id, tuple(fields)), controls


def read_html(html, platform, form_id):
    return parse_form(html, platform, form_id)[0]


def import_kaizen(data, form_id):
    """Import existing field metadata conservatively; absent options stay absent."""
    fields = []
    for key, meta in data[form_id]["fields"].items():
        tag = meta.get("tag", "INPUT").lower()
        kind = (meta.get("type") or "text") if tag == "input" else tag
        if kind not in {"text", "textarea", "select", "radio", "checkbox", "date", "file"}:
            continue
        label = meta.get("label", "")
        concept = meaning(label)
        options = tuple(Option(**o) if isinstance(o, dict) else Option(str(o), str(o)) for o in meta.get("options", []))
        fields.append(Field(key, label, kind, options, bool(meta.get("required", False)),
                            {"id": key, **({"label": label} if label else {})}, meta.get("section", ""),
                            state="candidate" if concept else "unmapped", concept=concept))
    return FormMap("kaizen", form_id, tuple(fields))
