"""Parser ekspresi kata kunci: AND, OR, tanda kutip, dan tanda kurung.

Contoh:
    ekonomi
    "energi terbarukan"
    PLTS OR PLTB
    "pajak karbon" AND (PLTU OR batu bara)
    JETP AND transisi

Kata yang berdampingan tanpa operator dianggap AND (implisit).
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r'"([^"]*)"|(\bAND\b|\bOR\b)|([()])|(\S+)', re.I)


def _tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    for m in _TOKEN_RE.finditer(expr or ""):
        if m.group(1) is not None:
            tokens.append(("PHRASE", m.group(1).strip()))
        elif m.group(2) is not None:
            tokens.append(("OP", m.group(2).upper()))
        elif m.group(3) is not None:
            tokens.append(("LPAREN" if m.group(3) == "(" else "RPAREN", m.group(3)))
        elif m.group(4) is not None:
            tokens.append(("TERM", m.group(4)))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.toks = tokens
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        if not self.toks:
            raise ValueError("Kata kunci kosong.")
        node = self.parse_or()
        if self.peek() is not None:
            raise ValueError(f"Ekspresi tidak terduga di dekat {self.peek()[1]!r}.")
        return node

    def parse_or(self):
        children = [self.parse_and()]
        while (t := self.peek()) and t[0] == "OP" and t[1] == "OR":
            self.take()
            children.append(self.parse_and())
        return children[0] if len(children) == 1 else {"type": "or", "children": children}

    def parse_and(self):
        children = [self.parse_primary()]
        while True:
            t = self.peek()
            if t is None:
                break
            if t[0] == "OP" and t[1] == "AND":
                self.take()
                children.append(self.parse_primary())
            elif t[0] in ("PHRASE", "TERM", "LPAREN"):  # AND implisit
                children.append(self.parse_primary())
            else:
                break
        return children[0] if len(children) == 1 else {"type": "and", "children": children}

    def parse_primary(self):
        t = self.take()
        if t is None:
            raise ValueError("Ekspresi tidak lengkap.")
        if t[0] == "PHRASE":
            if not t[1]:
                raise ValueError("Frasa kosong di dalam tanda kutip.")
            return {"type": "phrase", "value": t[1]}
        if t[0] == "TERM":
            return {"type": "term", "value": t[1]}
        if t[0] == "LPAREN":
            node = self.parse_or()
            if not (self.peek() and self.peek()[0] == "RPAREN"):
                raise ValueError("Tanda kurung tidak seimbang.")
            self.take()
            return node
        raise ValueError(f"Token tidak terduga: {t[1]!r}.")


def parse_expression(expr: str) -> dict:
    """Ubah teks ekspresi menjadi AST sederhana."""
    return _Parser(_tokenize(expr)).parse()


def matches(node: dict, text: str) -> bool:
    """True bila `text` memenuhi ekspresi."""
    t = (text or "").lower()
    kind = node["type"]
    if kind in ("term", "phrase"):
        return node["value"].lower() in t
    if kind == "and":
        return all(matches(c, t) for c in node["children"])
    if kind == "or":
        return any(matches(c, t) for c in node["children"])
    return False


def discovery_queries(node: dict) -> list[str]:
    """Daftar query sederhana untuk dikirim ke portal.

    OR dipecah menjadi beberapa query; AND memakai operand pertama (superset),
    lalu penyaringan penuh dilakukan dengan `matches()`.
    """
    kind = node["type"]
    if kind in ("term", "phrase"):
        return [node["value"]] if node["value"] else []
    if kind == "or":
        out: list[str] = []
        for child in node["children"]:
            for q in discovery_queries(child):
                if q not in out:
                    out.append(q)
        return out
    return discovery_queries(node["children"][0])


def describe(node: dict) -> str:
    kind = node["type"]
    if kind == "phrase":
        return f'"{node["value"]}"'
    if kind == "term":
        return node["value"]
    op = " AND " if kind == "and" else " OR "
    return "(" + op.join(describe(c) for c in node["children"]) + ")"
