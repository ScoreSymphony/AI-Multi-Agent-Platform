"""Audit broad exception handling at platform error boundaries.

The audit is deliberately narrower than a general lint or complexity rule. It inventories broad
``except`` handlers and ``contextlib.suppress`` calls, classifies common boundary/cleanup shapes,
and can fail only for clearly prohibited production patterns. It does not replace the canonical
``ContractError`` / ``ErrorCode`` model.

Reviewed broad catches may carry an explicit source comment immediately above the handler::

    # error-boundary: allow-broad-catch=boundary provider SDK outer boundary
    except Exception as exc:
        ...

Allowed justification kinds are ``boundary``, ``cleanup`` and ``translation``. The marker is a
review assertion, not a blanket suppression mechanism. It can never authorize swallowing process
signals such as ``KeyboardInterrupt`` or ``SystemExit``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Classification = Literal[
    "boundary catch",
    "cleanup / best effort",
    "domain error translation",
    "unexpected swallowing",
    "test / fixture",
    "generated code",
    "development tool",
    "needs review",
]
Severity = Literal["allowed", "review", "prohibited"]

_ALLOW_MARKER = "error-boundary: allow-broad-catch="
_ALLOWED_JUSTIFICATIONS = frozenset({"boundary", "cleanup", "translation"})
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})
_TELEMETRY_HINTS = (
    "telemetry",
    "metric",
    "counter",
    "histogram",
    "span",
    "trace",
    "record_exception",
    "observe",
)


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    file: str
    line: int
    scope: str
    domain: str
    exception_form: str
    current_action: str
    reraise: bool
    translated: bool
    logged: bool
    telemetry: bool
    retryable_semantics: str
    cancellation_risk: bool
    shutdown_risk: bool
    diagnostic_text_risk: bool
    canonical_boundary: bool
    recommended_classification: Classification
    recommended_action: str
    source_class: str
    justification: str | None
    severity: Severity


class _SourceScanner(ast.NodeVisitor):
    def __init__(self, *, display_path: str, source: str) -> None:
        self.display_path = display_path
        self.source = source
        self.lines = source.splitlines()
        self.scope: list[str] = []
        self.handler_stack: list[tuple[str, ast.ExceptHandler]] = []
        self.findings: list[Finding] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        form = _exception_form(node.type) or "bare except"
        if _is_broad_form(form):
            self.findings.append(self._handler_finding(node, form))
        self.handler_stack.append((form, node))
        for statement in node.body:
            self.visit(statement)
        self.handler_stack.pop()

    def visit_With(self, node: ast.With) -> None:
        self._visit_with_items(node)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with_items(node)
        self.generic_visit(node)

    def _visit_with_items(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            suppress_types = _suppress_types(item.context_expr)
            if suppress_types is None:
                continue

            source_class = _source_class(self.display_path, self.source)
            broad = any(_is_broad_form(name) for name in suppress_types)
            justification = self._justification(node.lineno)
            form = f"suppress({', '.join(suppress_types)})"
            catches_process_signals = any(
                name == "bare except" or _form_contains(name, "BaseException")
                for name in suppress_types
            )

            classification: Classification = "cleanup / best effort"
            severity: Severity = "allowed"
            action = "retain typed suppression only for documented cleanup obligations"

            if source_class == "test / fixture":
                classification = "test / fixture"
            elif source_class == "generated code":
                classification = "generated code"
            elif source_class == "development tool":
                classification = "development tool"
                severity = "review" if broad else "allowed"
            elif catches_process_signals:
                classification = "unexpected swallowing"
                severity = "prohibited"
                action = (
                    "never suppress BaseException/process signals; narrow the suppression to "
                    "ordinary runtime failures"
                )
            elif broad and justification is None:
                severity = "prohibited"
                action = "replace broad suppress(...) or add an explicit reviewed justification"
            elif broad:
                severity = "allowed"

            self.findings.append(
                self._build_finding(
                    line=node.lineno,
                    form=form,
                    current_action="suppress",
                    reraise=False,
                    translated=False,
                    logged=False,
                    telemetry=False,
                    retryable_semantics="not applicable",
                    cancellation_risk=catches_process_signals,
                    shutdown_risk=catches_process_signals,
                    diagnostic_text_risk=False,
                    canonical_boundary=False,
                    classification=classification,
                    action=action,
                    source_class=source_class,
                    justification=justification,
                    severity=severity,
                )
            )

    def _handler_finding(self, node: ast.ExceptHandler, form: str) -> Finding:
        source_class = _source_class(self.display_path, self.source)
        justification = self._justification(node.lineno)
        reraise = _guaranteed_bare_reraise(node.body)
        translated = _contains_translating_raise(node.body)
        logged = _contains_logging(node.body)
        telemetry = _contains_telemetry(node.body)
        canonical_boundary = _contains_name(node.body, "ContractError")
        silent = _silent_swallow_action(node.body)
        diagnostic_text_risk = _uses_exception_text(node.body, node.name)
        retryable = _retryable_semantics(node.body)
        catches_process_signals = form == "bare except" or _form_contains(form, "BaseException")
        cancellation_settlement = silent == "pass" and self._inside_cancellation_reraise()
        cancellation_risk = catches_process_signals and not reraise
        shutdown_risk = catches_process_signals and not reraise

        classification: Classification
        severity: Severity
        action: str

        if source_class == "test / fixture":
            classification = "test / fixture"
            severity = "allowed"
            action = "keep separate from production enforcement"
        elif source_class == "generated code":
            classification = "generated code"
            severity = "allowed"
            action = "enforce at the generator/input rather than generated source"
        elif source_class == "development tool":
            classification = "development tool"
            severity = "review"
            action = "review independently from production error-boundary enforcement"
        elif catches_process_signals and not reraise:
            classification = "unexpected swallowing"
            severity = "prohibited"
            action = (
                "propagate cancellation/SystemExit/KeyboardInterrupt before containing "
                "ordinary runtime failures"
            )
        elif reraise and catches_process_signals:
            classification = "cleanup / best effort"
            severity = "allowed"
            action = "retain only when cleanup must run for every BaseException path"
        elif justification is not None:
            mapping: dict[str, Classification] = {
                "boundary": "boundary catch",
                "cleanup": "cleanup / best effort",
                "translation": "domain error translation",
            }
            classification = mapping[justification]
            severity = "allowed"
            action = "retain only while the explicit justification remains accurate"
        elif cancellation_settlement:
            classification = "cleanup / best effort"
            severity = "allowed"
            action = (
                "retain only inside cancellation settlement where the outer handler "
                "unconditionally re-raises cancellation"
            )
        elif translated:
            classification = "domain error translation"
            severity = "review"
            action = (
                "verify canonical ErrorCode/retryable/details and preserve cause with "
                "raise ... from exc"
            )
        elif silent is not None:
            classification = "unexpected swallowing"
            severity = "prohibited"
            action = "narrow the catch or make containment and observability explicit"
        else:
            classification = "needs review"
            severity = "review"
            action = "classify as boundary/cleanup/translation or narrow expected exceptions"

        return self._build_finding(
            line=node.lineno,
            form=form,
            current_action=_handler_action(node.body, silent=silent),
            reraise=reraise,
            translated=translated,
            logged=logged,
            telemetry=telemetry,
            retryable_semantics=retryable,
            cancellation_risk=cancellation_risk,
            shutdown_risk=shutdown_risk,
            diagnostic_text_risk=diagnostic_text_risk,
            canonical_boundary=canonical_boundary,
            classification=classification,
            action=action,
            source_class=source_class,
            justification=justification,
            severity=severity,
        )

    def _inside_cancellation_reraise(self) -> bool:
        return any(
            _form_contains(form, "CancelledError") and _guaranteed_bare_reraise(parent.body)
            for form, parent in self.handler_stack
        )

    def _build_finding(
        self,
        *,
        line: int,
        form: str,
        current_action: str,
        reraise: bool,
        translated: bool,
        logged: bool,
        telemetry: bool,
        retryable_semantics: str,
        cancellation_risk: bool,
        shutdown_risk: bool,
        diagnostic_text_risk: bool,
        canonical_boundary: bool,
        classification: Classification,
        action: str,
        source_class: str,
        justification: str | None,
        severity: Severity,
    ) -> Finding:
        scope = self._qualname()
        identity = f"{self.display_path}:{scope}:{line}:{form}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        return Finding(
            id=f"EB-{digest}",
            file=self.display_path,
            line=line,
            scope=scope,
            domain=_domain(self.display_path),
            exception_form=form,
            current_action=current_action,
            reraise=reraise,
            translated=translated,
            logged=logged,
            telemetry=telemetry,
            retryable_semantics=retryable_semantics,
            cancellation_risk=cancellation_risk,
            shutdown_risk=shutdown_risk,
            diagnostic_text_risk=diagnostic_text_risk,
            canonical_boundary=canonical_boundary,
            recommended_classification=classification,
            recommended_action=action,
            source_class=source_class,
            justification=justification,
            severity=severity,
        )

    def _qualname(self) -> str:
        return ".".join(self.scope) if self.scope else "<module>"

    def _justification(self, line: int) -> str | None:
        for index in (line - 2, line - 3):
            if not 0 <= index < len(self.lines):
                continue
            text = self.lines[index]
            marker = text.find(_ALLOW_MARKER)
            if marker < 0:
                continue
            tail = text[marker + len(_ALLOW_MARKER) :].strip()
            kind = tail.split(maxsplit=1)[0] if tail else ""
            if kind in _ALLOWED_JUSTIFICATIONS:
                return kind
        return None


def _exception_form(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_attribute_name(node.value)}.{node.attr}".lstrip(".")
    if isinstance(node, ast.Tuple):
        return "(" + ", ".join(_exception_form(item) or "?" for item in node.elts) + ")"
    return "<dynamic exception expression>"


def _attribute_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_attribute_name(node.value)}.{node.attr}".lstrip(".")
    return ""


def _form_contains(form: str, name: str) -> bool:
    tokens = form.replace("(", " ").replace(")", " ").replace(",", " ").split()
    return any(token == name or token.endswith(f".{name}") for token in tokens)


def _is_broad_form(form: str) -> bool:
    return (
        form == "bare except"
        or _form_contains(form, "Exception")
        or _form_contains(form, "BaseException")
    )


def _walk_body(body: list[ast.stmt]) -> list[ast.AST]:
    return [node for statement in body for node in ast.walk(statement)]


def _guaranteed_bare_reraise(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], ast.Raise) and body[-1].exc is None


def _contains_translating_raise(body: list[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Raise) and node.exc is not None for node in _walk_body(body))


def _contains_name(body: list[ast.stmt], name: str) -> bool:
    return any(isinstance(node, ast.Name) and node.id == name for node in _walk_body(body))


def _contains_logging(body: list[ast.stmt]) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LOG_METHODS
        for node in _walk_body(body)
    )


def _contains_telemetry(body: list[ast.stmt]) -> bool:
    for node in _walk_body(body):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func).lower()
        if any(hint in name for hint in _TELEMETRY_HINTS):
            return True
    return False


def _silent_swallow_action(body: list[ast.stmt]) -> str | None:
    meaningful = [
        statement
        for statement in body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    if not meaningful:
        return "empty"
    if all(isinstance(statement, ast.Pass) for statement in meaningful):
        return "pass"
    if len(meaningful) == 1 and isinstance(meaningful[0], ast.Return):
        value = meaningful[0].value
        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            return "return None"
    return None


def _handler_action(body: list[ast.stmt], *, silent: str | None) -> str:
    if silent is not None:
        return silent
    nodes = _walk_body(body)
    actions: list[str] = []
    if any(isinstance(node, ast.Raise) and node.exc is None for node in nodes):
        actions.append("reraise")
    if any(isinstance(node, ast.Raise) and node.exc is not None for node in nodes):
        actions.append("raise translated")
    if _contains_logging(body):
        actions.append("log")
    if any(isinstance(node, ast.Return) for node in nodes):
        actions.append("return fallback")
    return ", ".join(actions) if actions else "contain / mutate / cleanup"


def _uses_exception_text(body: list[ast.stmt], exception_name: str | None) -> bool:
    if not exception_name:
        return False
    for node in _walk_body(body):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "str"
            and any(isinstance(arg, ast.Name) and arg.id == exception_name for arg in node.args)
        ):
            return True
        if (
            isinstance(node, ast.FormattedValue)
            and isinstance(node.value, ast.Name)
            and node.value.id == exception_name
        ):
            return True
    return False


def _retryable_semantics(body: list[ast.stmt]) -> str:
    for node in _walk_body(body):
        if not isinstance(node, ast.Call) or not _call_name(node.func).endswith("ContractError"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "retryable":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool):
                return f"explicit {keyword.value.value}"
            return "explicit expression"
        return "canonical error; retryable default/implicit"
    return "not explicit in handler"


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_attribute_name(node.value)}.{node.attr}".lstrip(".")
    return ""


def _suppress_types(node: ast.expr) -> tuple[str, ...] | None:
    if not isinstance(node, ast.Call) or _call_name(node.func) not in {
        "suppress",
        "contextlib.suppress",
    }:
        return None
    return tuple(_exception_form(argument) or "?" for argument in node.args)


def _source_class(path: str, source: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("tests/") or "/tests/" in normalized:
        return "test / fixture"
    lower = source[:500].lower()
    if "generated file" in lower or "do not edit" in lower or normalized.startswith("generated/"):
        return "generated code"
    if normalized.startswith(("scripts/", "experiments/", "tools/")):
        return "development tool"
    return "production"


def _domain(path: str) -> str:
    normalized = path.replace("\\", "/")
    marker = "ai_multi_agent_platform/"
    if marker in normalized:
        tail = normalized.split(marker, 1)[1]
        return tail.split("/", 1)[0]
    parts = normalized.split("/")
    return parts[1] if normalized.startswith("tests/") and len(parts) > 1 else parts[0]


def scan_path(root: Path, *, repository_root: Path | None = None) -> list[Finding]:
    root = root.resolve()
    repository_root = (repository_root or Path.cwd()).resolve()
    paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise RuntimeError(f"cannot scan {path}: {type(exc).__name__}") from exc
        try:
            display_path = path.relative_to(repository_root).as_posix()
        except ValueError:
            display_path = path.as_posix()
        scanner = _SourceScanner(display_path=display_path, source=source)
        scanner.visit(tree)
        findings.extend(scanner.findings)
    return findings


def _safe_finding_payload(item: Finding) -> dict[str, object]:
    """Return only allowlisted audit metadata suitable for CI output.

    Source text, exception objects and arbitrary exception messages are intentionally absent.
    """

    return {
        "id": item.id,
        "file": item.file,
        "line": item.line,
        "scope": item.scope,
        "domain": item.domain,
        "exception_form": item.exception_form,
        "current_action": item.current_action,
        "reraise": item.reraise,
        "translated": item.translated,
        "logged": item.logged,
        "telemetry": item.telemetry,
        "retryable_semantics": item.retryable_semantics,
        "cancellation_risk": item.cancellation_risk,
        "shutdown_risk": item.shutdown_risk,
        "diagnostic_text_risk": item.diagnostic_text_risk,
        "canonical_boundary": item.canonical_boundary,
        "recommended_classification": item.recommended_classification,
        "recommended_action": item.recommended_action,
        "source_class": item.source_class,
        "justification": item.justification,
        "severity": item.severity,
    }


def _markdown(findings: list[Finding]) -> str:
    header = (
        "| ID | File | Scope | Form | Action | Re-raise | Translate | Log | Telemetry | "
        "Retryable | Cancel risk | Shutdown risk | Diagnostic risk | Canonical boundary | "
        "Classification | Recommended action | Severity |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
        "--- | --- | --- | --- |\n"
    )
    rows: list[str] = []
    for item in findings:
        values = [
            item.id,
            f"{item.file}:{item.line}",
            item.scope,
            item.exception_form,
            item.current_action,
            str(item.reraise),
            str(item.translated),
            str(item.logged),
            str(item.telemetry),
            item.retryable_semantics,
            str(item.cancellation_risk),
            str(item.shutdown_risk),
            str(item.diagnostic_text_risk),
            str(item.canonical_boundary),
            item.recommended_classification,
            item.recommended_action,
            item.severity,
        ]
        rows.append("| " + " | ".join(value.replace("|", "\\|") for value in values) + " |")
    return header + "\n".join(rows) + ("\n" if rows else "")


def _text(findings: list[Finding]) -> str:
    totals = Counter(item.severity for item in findings)
    classes = Counter(item.recommended_classification for item in findings)
    lines = [
        f"findings={len(findings)} prohibited={totals['prohibited']} review={totals['review']} "
        f"allowed={totals['allowed']}"
    ]
    lines.extend(f"classification[{name}]={count}" for name, count in sorted(classes.items()))
    lines.extend(
        f"{item.severity.upper():10} {item.id} {item.file}:{item.line} "
        f"{item.exception_form} {item.recommended_classification} [{item.scope}]"
        for item in findings
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        help="Python file/directory to scan. Repeatable. Defaults to src/ai_multi_agent_platform.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for stable relative finding paths.",
    )
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 only when clearly prohibited broad-catch shapes are present.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = args.root or [args.repository_root / "src" / "ai_multi_agent_platform"]
    findings = [
        finding
        for root in roots
        for finding in scan_path(root, repository_root=args.repository_root)
    ]
    findings.sort(key=lambda item: (item.file, item.line, item.id))

    if args.format == "json":
        payload = [_safe_finding_payload(item) for item in findings]
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    elif args.format == "markdown":
        sys.stdout.write(_markdown(findings))
    else:
        sys.stdout.write(_text(findings))

    return int(args.check and any(item.severity == "prohibited" for item in findings))


if __name__ == "__main__":
    sys.exit(main())
