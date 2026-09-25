"""Validation and narrow decoration for author-supplied HTML fragments."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from html import escape

from .palette import ALLOWED_COLORS, NEUTRALS

COMMON_TAGS = frozenset(
    "p br strong em b i u sup sub code pre blockquote hr ul ol li h3 h4 a table thead tbody tr th td img".split()
)
PAGE_TAGS = COMMON_TAGS | {"iframe"}
GLOBAL_ATTRS = {"a": {"href", "target"}, "th": {"colspan", "rowspan"}, "td": {"colspan", "rowspan"}, "img": {"src", "alt"}, "iframe": {"src", "title"}}
FORBIDDEN_TAGS = {"h1", "h2", "script", "style", "font", "center", "div", "span", "details"}
PLACEHOLDER_RE = re.compile(r"\{\{\s*(?:file|page)\s*:", re.IGNORECASE)
HEX_COLOR_RE = re.compile(r"#[0-9a-f]{3,8}\b", re.I)
COLOR_FUNCTION_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix|device-cmyk)\s*\(", re.I)
COLOR_PROPERTIES = {
    "color", "background", "background-color", "background-image",
    "border", "border-color", "border-top", "border-right", "border-bottom", "border-left",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "outline", "outline-color", "text-shadow", "box-shadow", "text-decoration",
    "text-decoration-color", "text-emphasis", "text-emphasis-color", "column-rule",
    "column-rule-color", "fill", "stroke", "caret-color", "accent-color", "flood-color",
    "lighting-color", "stop-color", "scrollbar-color", "border-image", "border-image-source",
    "mask", "mask-image", "shape-outside",
}
CSS_COLOR_NAMES = frozenset("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet
brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue
darkcyan darkgoldenrod darkgray darkgrey darkgreen darkkhaki darkmagenta darkolivegreen darkorange
darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise
darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia
 gainsboro ghostwhite gold goldenrod gray grey green greenyellow honeydew hotpink indianred indigo ivory
 khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
 lightgray lightgrey lightgreen lightpink lightsalmon lightseagreen lightskyblue lightslategray
 lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
 mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen mediumturquoise
 mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
 orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru
 pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen
 seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle
 tomato turquoise violet wheat white whitesmoke yellow yellowgreen transparent currentcolor canvas canvastext
 buttonface buttontext field fieldtext highlight highlighttext linktext activetext visitedtext selecteditem
 selecteditemtext graytext mark marktext accentcolor
""".split())
NON_PALETTE_COLOR_KEYWORDS = CSS_COLOR_NAMES | {"inherit", "initial", "unset", "revert", "revert-layer"}


class _Validator(HTMLParser):
    def __init__(self, *, page: bool, freeform: bool, field_path: str):
        super().__init__(convert_charrefs=True)
        self.page, self.freeform, self.field_path = page, freeform, field_path
        self.problems: list[str] = []
        self.stack: list[str] = []

    def problem(self, message: str) -> None:
        text = f"{self.field_path}: {message}"
        if text not in self.problems:
            self.problems.append(text)

    def handle_starttag(self, tag, attrs):
        allowed_tags = PAGE_TAGS if self.page else COMMON_TAGS
        if tag in FORBIDDEN_TAGS or tag not in allowed_tags:
            self.problem(f"<{tag}> is not allowed")
        allowed = GLOBAL_ATTRS.get(tag, set())
        seen = set()
        for name, value in attrs:
            seen.add(name)
            if name.startswith("on"):
                self.problem(f"event attribute {name} is not allowed")
            elif name in {"style", "class", "id", "width", "height"}:
                if not (name == "style" and self.page and self.freeform):
                    self.problem(f"{name} attribute not allowed")
                elif value:
                    self._style(value)
            elif name not in allowed:
                self.problem(f"attribute {name} is not allowed on <{tag}>")
        if tag == "img":
            if "alt" not in seen:
                self.problem("img requires alt")
            if "src" not in seen:
                self.problem("img requires src")
        if tag == "iframe":
            for required in ("src", "title"):
                if required not in seen:
                    self.problem(f"iframe requires {required}")
        if tag not in {"br", "hr", "img"}:
            self.stack.append(tag)

    def _style(self, style: str) -> None:
        # CSS comments can split an otherwise recognizable color token.
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        for declaration in style.split(";"):
            if not declaration.strip():
                continue
            if ":" not in declaration:
                self.problem("invalid style declaration")
                continue
            prop, value = (part.strip().lower() for part in declaration.split(":", 1))
            if prop in {"width", "min-width", "max-width"} and value != "100%":
                self.problem(f"{prop} must be 100%")
            color_property = prop in COLOR_PROPERTIES or "-color" in prop or prop.startswith(("background", "border", "outline", "text-shadow", "box-shadow", "text-decoration", "text-emphasis", "column-rule", "mask", "--"))
            if color_property or HEX_COLOR_RE.search(value) or COLOR_FUNCTION_RE.search(value):
                self._validate_colors(value)

    def _validate_colors(self, value: str) -> None:
        hex_colors = [color.lower() for color in HEX_COLOR_RE.findall(value)]
        words = set(re.findall(r"[a-z-]+", value.lower()))
        has_color_syntax = bool(
            hex_colors or COLOR_FUNCTION_RE.search(value)
            or words & NON_PALETTE_COLOR_KEYWORDS or "var(" in value
            or "\\" in value
        )
        if not has_color_syntax:
            return
        if (any(color not in ALLOWED_COLORS for color in hex_colors)
                or COLOR_FUNCTION_RE.search(value)
                or words & NON_PALETTE_COLOR_KEYWORDS
                or "var(" in value or "\\" in value):
            self.problem("color is outside the Forge palette")

    def handle_endtag(self, tag):
        allowed_tags = PAGE_TAGS if self.page else COMMON_TAGS
        if tag in FORBIDDEN_TAGS or tag not in allowed_tags:
            self.problem(f"</{tag}> is not allowed")
            return
        if tag not in self.stack:
            self.problem(f"unmatched </{tag}>")
        elif self.stack[-1] != tag:
            self.problem(f"unbalanced </{tag}>")
            self.stack.remove(tag)
        else:
            self.stack.pop()

    def handle_data(self, data):
        if PLACEHOLDER_RE.search(data):
            self.problem("file/page placeholders are not supported")

    def handle_comment(self, data):
        self.problem("HTML comments are not allowed")

    def handle_decl(self, decl):
        self.problem("HTML declarations are not allowed")

    def unknown_decl(self, data):
        self.problem("HTML declarations are not allowed")

    def handle_pi(self, data):
        self.problem("processing instructions are not allowed")

    def close(self):
        super().close()
        for tag in reversed(self.stack):
            self.problem(f"unclosed <{tag}>")


def validate_author_html(value: str, *, field_path: str, page: bool = False, freeform: bool = False) -> list[str]:
    """Return field-path-specific problems for an author HTML fragment."""
    if not isinstance(value, str):
        return [f"{field_path}: must be a string"]
    parser = _Validator(page=page, freeform=freeform, field_path=field_path)
    if PLACEHOLDER_RE.search(value):
        parser.problem("file/page placeholders are not supported")
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        parser.problem("invalid HTML")
    return parser.problems


class _Decorator(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table":
            attributes["style"] = _merge_style(attributes.get("style"), "width:100%;max-width:100%;border-collapse:collapse")
        elif tag in {"th", "td"}:
            attributes["style"] = _merge_style(attributes.get("style"), f"padding:8px;border:1px solid {NEUTRALS['rule']};text-align:left")
        elif tag == "img":
            attributes["style"] = _merge_style(attributes.get("style"), "max-width:100%;height:auto")
        elif tag == "iframe":
            attributes["style"] = _merge_style(attributes.get("style"), "width:100%;height:360px;border:0")
        self.parts.append("<" + tag + "".join(f' {name}="{escape(value or "", quote=True)}"' for name, value in attributes.items()) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.parts[-1] = self.parts[-1][:-1] + " />"

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def unknown_decl(self, data):
        self.parts.append(f"<![{data}]>")


def _merge_style(existing: str | None, extra: str) -> str:
    return ";".join(part for part in (existing.strip(" ;") if existing else "", extra) if part)


def decorate_author_html(value: str, *, page: bool = False, freeform: bool = False) -> str:
    """Add responsive Canvas presentation to validated author HTML."""
    parser = _Decorator()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)
