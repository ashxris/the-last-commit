"""AST-level code understanding via tree-sitter.

A language registry maps file extensions to grammars; the Python
implementation extracts symbols, stubs, docstrings, and TODO-bearing
comments.  JavaScript/TypeScript get the same treatment with a lighter
heuristic set.  Everything here reflects the repository's *final* state
(as of HEAD) — the history layer covers the time dimension.
"""

from __future__ import annotations

import re
from pathlib import Path

from tree_sitter import Language, Node, Parser

from ..types import SymbolInfo, TodoComment
from .git_history import MANIFEST_NAMES

# --------------------------------------------------------------------------
# Language registry
# --------------------------------------------------------------------------

try:
    import tree_sitter_python as _tspy

    _PY = Language(_tspy.language())
except ImportError:  # pragma: no cover
    _PY = None

try:
    import tree_sitter_javascript as _tsjs

    _JS = Language(_tsjs.language())
except ImportError:  # pragma: no cover
    _JS = None

try:
    import tree_sitter_typescript as _tsts

    _TS = Language(_tsts.language_typescript())
    _TSX = Language(_tsts.language_tsx())
except ImportError:  # pragma: no cover
    _TS = _TSX = None

_LANGUAGES: dict[str, Language] = {}
if _PY:
    _LANGUAGES["python"] = _PY
if _JS:
    _LANGUAGES["javascript"] = _JS
if _TS:
    _LANGUAGES["typescript"] = _TS

EXT_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

parsers: dict[str, Parser] = {}


def _parser(language: str) -> Parser | None:
    lang = _LANGUAGES.get(language)
    if lang is None:
        return None
    if language not in parsers:
        p = Parser()
        p.language = lang
        parsers[language] = p
    return parsers[language]


# --------------------------------------------------------------------------
# TODO / FIXME comments (language-agnostic, line regex)
# --------------------------------------------------------------------------

TODO_RE = re.compile(r"(TODO|FIXME|HACK|XXX)\b[:\s]?(.*)", re.IGNORECASE)
SKIP_RE = re.compile(
    r"(@pytest\.mark\.(skip|xfail)|@unittest\.skip|(it|test|describe)\.skip|\.skip\(|xfail\b)",
    re.IGNORECASE,
)
COMMENT_PREFIX_RE = re.compile(r"^\s*(#|//|/\*|\*|<!--|--|;)")


def extract_todos(path: str, text: str) -> list[TodoComment]:
    todos: list[TodoComment] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = TODO_RE.search(line)
        if m and COMMENT_PREFIX_RE.match(line):
            todos.append(
                TodoComment(path=path, line=i, kind=m.group(1).upper(), text=m.group(2).strip()[:200])
            )
            continue
        if SKIP_RE.search(line) and TEST_FILE_RE.search(path):
            todos.append(
                TodoComment(path=path, line=i, kind="SKIP",
                            text=f"test skipped/disabled here: {line.strip()[:180]}")
            )
    return todos


# --------------------------------------------------------------------------
# Python symbol extraction
# --------------------------------------------------------------------------


def _node_text(src: bytes, node: Node) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _py_docstring(body: Node, src: bytes) -> str | None:
    if body.type != "block":
        return None
    for child in body.children:
        if child.type == "expression_statement" and child.children:
            expr = child.children[0]
            if expr.type == "string":
                text = _node_text(src, expr)
                text = re.sub(r'"""|\'\'\'|^["\']|["\']$', "", text.strip())
                return text.strip()[:500] or None
        break  # only the first statement can be a docstring
    return None


_STUB_PY = ("raise NotImplementedError", "raise NotImplemented", "...  # pragma")


def _py_is_stub(body: Node, src: bytes) -> str | None:
    """Why is this function a stub?  Returns a reason or None."""
    text = _node_text(src, body)
    if "NotImplementedError" in text or "NotImplemented" in text:
        return "raises NotImplementedError"
    # a body that is only a docstring / pass / ellipsis
    meaningful = [
        c for c in body.children
        if c.type not in ("comment",) and not (c.type == "expression_statement" and _node_text(src, c).strip() in ('...',))
    ]
    non_doc = [
        c for c in meaningful
        if not (c.type == "expression_statement" and c.children and c.children[0].type == "string")
    ]
    if all(c.type == "pass_statement" for c in non_doc) and non_doc:
        return "body is only `pass`"
    if not non_doc and meaningful:
        return "body is only a docstring"
    if all(
        c.type in ("pass_statement", "expression_statement") and _node_text(src, c).strip() in ("...", "pass")
        for c in meaningful
    ):
        return "body is only `...`"
    return None


def _py_signature(node: Node, body: Node, src: bytes) -> str:
    header = src[node.start_byte : body.start_byte].decode("utf-8", errors="replace")
    lines = [ln for ln in header.strip().splitlines() if ln.strip() and not ln.strip().startswith("@")]
    return " ".join(ln.strip() for ln in lines)[:200]


def extract_python_symbols(path: str, src_bytes: bytes) -> tuple[list[SymbolInfo], str | None]:
    parser = _parser("python")
    if parser is None:
        return [], None
    tree = parser.parse(src_bytes)
    symbols: list[SymbolInfo] = []
    class_stack: list[str] = []

    def walk(node: Node) -> None:
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                name = _node_text(src_bytes, name_node)
                symbols.append(
                    SymbolInfo(
                        name=name, kind="class", path=path, language="python",
                        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        signature=_py_signature(node, body, src_bytes),
                        docstring=_py_docstring(body, src_bytes),
                    )
                )
                class_stack.append(name)
                for child in node.children:
                    walk(child)
                class_stack.pop()
            return
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                name = _node_text(src_bytes, name_node)
                kind = "method" if class_stack else "function"
                stub = _py_is_stub(body, src_bytes)
                sig = _py_signature(node, body, src_bytes)
                symbols.append(
                    SymbolInfo(
                        name=name, kind=kind, path=path, language="python",
                        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        signature=sig,
                        docstring=_py_docstring(body, src_bytes),
                        is_stub=stub is not None,
                        stub_reason=stub,
                    )
                )
                for child in body.children:
                    walk(child)
            return
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    err = None
    if tree.root_node.has_error:
        err = f"{path}: parse warnings"
    return symbols, err


# --------------------------------------------------------------------------
# JavaScript / TypeScript symbol extraction (lighter heuristics)
# --------------------------------------------------------------------------

JS_FUNC_TYPES = {"function_declaration", "generator_function_declaration", "method_definition"}
JS_STUB_PAT = re.compile(r"not\s+implemented|TODO|FIXME|no-op|placeholder", re.IGNORECASE)
TEST_FILE_RE = re.compile(r"(^|/)(tests?/|test_.*\.py$|.*_test\.py$|.*\.test\.[jt]sx?$|.*\.spec\.[jt]sx?$)", re.IGNORECASE)


def extract_js_symbols(path: str, src_bytes: bytes, language: str) -> tuple[list[SymbolInfo], str | None]:
    parser = _parser(language)
    if parser is None:
        return [], None
    tree = parser.parse(src_bytes)
    symbols: list[SymbolInfo] = []
    src = src_bytes
    class_stack: list[str] = []

    def body_text(node: Node) -> str:
        body = node.child_by_field_name("body")
        return _node_text(src, body) if body else ""

    def walk(node: Node) -> None:
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _node_text(src, name_node)
                symbols.append(
                    SymbolInfo(name=name, kind="class", path=path, language=language,
                               start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                               signature=f"class {name}")
                )
                class_stack.append(name)
                for child in node.children:
                    walk(child)
                class_stack.pop()
            return
        if node.type in JS_FUNC_TYPES:
            name_node = node.child_by_field_name("name")
            name = _node_text(src, name_node) if name_node else "<anonymous>"
            kind = "method" if class_stack else "function"
            text = body_text(node)
            stub = None
            if JS_STUB_PAT.search(text):
                stub = "contains a not-implemented marker"
            elif len(text.splitlines()) <= 1 and text.strip() in ("{", "{}"):
                stub = "empty body"
            symbols.append(
                SymbolInfo(name=name, kind=kind, path=path, language=language,
                           start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                           signature=f"{kind} {name}", is_stub=stub is not None, stub_reason=stub)
            )
            return
        if node.type == "variable_declarator" and node.children:
            name_node = node.children[0]
            value = node.child_by_field_name("value")
            if value and value.type in ("arrow_function", "function_expression") and name_node.type == "identifier":
                name = _node_text(src, name_node)
                text = _node_text(src, value)
                m = JS_STUB_PAT.search(text)
                stub = "contains a not-implemented marker" if m else None
                symbols.append(
                    SymbolInfo(name=name, kind="function", path=path, language=language,
                               start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                               signature=f"const {name} = ...", is_stub=stub is not None, stub_reason=stub)
                )
                return
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return symbols, None


# --------------------------------------------------------------------------
# Dead-code candidates (Python heuristic: name never referenced anywhere)
# --------------------------------------------------------------------------


def find_dead_code_candidates(symbols: list[SymbolInfo], all_sources: dict[str, str]) -> list[SymbolInfo]:
    """Symbols whose name appears exactly once across all Python sources
    (its own definition).  A coarse heuristic — candidates, not verdicts."""
    py_sources = {p: t for p, t in all_sources.items() if p.endswith(".py")}
    if not py_sources:
        return []
    corpus = "\n".join(py_sources.values())
    candidates = []
    for sym in symbols:
        if sym.language != "python" or sym.name.startswith("__"):
            continue
        occurrences = len(re.findall(rf"\b{re.escape(sym.name)}\b", corpus))
        if occurrences <= 1:
            candidates.append(sym)
    return candidates


# --------------------------------------------------------------------------
# Driver: parse the whole HEAD tree
# --------------------------------------------------------------------------


def parse_repository(repo_path: str, snapshot) -> None:
    """Populate snapshot.symbols / snapshot.todos / snapshot.parse_errors."""
    from .git_history import read_blob_at

    root = Path(repo_path)
    skip_dirs = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".lastcommit"}
    files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if any(part in skip_dirs for part in p.relative_to(root).parts):
            continue
        files.append(p)

    sources: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        sources[rel] = text

    for rel, text in sources.items():
        snapshot.todos.extend(extract_todos(rel, text))

    for rel, text in sources.items():
        language = EXT_LANGUAGE.get(Path(rel).suffix)
        if language is None:
            continue
        try:
            if language == "python":
                syms, err = extract_python_symbols(rel, text.encode("utf-8"))
            else:
                syms, err = extract_js_symbols(rel, text.encode("utf-8"), language)
        except Exception as exc:  # grammar crash on odd input must not kill ingest
            snapshot.parse_errors.append(f"{rel}: {exc}")
            continue
        if err:
            snapshot.parse_errors.append(err)
        snapshot.symbols.extend(syms)

    for dead in find_dead_code_candidates(snapshot.symbols, sources):
        if not dead.stub_reason:
            dead.stub_reason = "never referenced elsewhere (candidate dead code)"

    # manifest / readme / changelog capture
    for rel, text in sources.items():
        base = Path(rel).name
        if base in MANIFEST_NAMES:
            snapshot.manifests[rel] = text
        low = base.lower()
        if low.startswith("readme"):
            snapshot.readme_text = text
        elif low.startswith("changelog"):
            snapshot.changelog_text = text
