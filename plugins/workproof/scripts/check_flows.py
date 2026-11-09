#!/usr/bin/env python
"""The flow-map engine: recomputes the status of every flow in a project's flow map (docs/FLOWS.md).

A project keeps its map at docs/FLOWS.md (or docs/FLUXOS.md, a Brazilian Portuguese map: see "Languages"), its
state at docs/.flows-state.json and its configuration at scripts/flows.json. The engine runs from anywhere:
    check-flows <mode>                      (the plugin's bin/ command, from inside the project)
    python -B check_flows.py [--project <project root>] <mode>

Why it exists: "validated" must never be something a person or an agent types. Each flow names its proof; a
run executes the test classes those proofs need, reads what passed from the result reports, and writes the
status back. docs/.flows-state.json remembers per flow the status, the validation date, the proofs used and
a fingerprint of the source files its Entry cell cites, so a validated flow stays validated until its
source changes (it then drops to `revalidate` and the note names the file).

Proofs a flow may cite (the Proof cell, separated by <br>):
    Suite/Test case           an automated test (XCTest `Class/method()`, Swift Testing `Suite name/Test name`,
                              a bare case name when it is unique, `Target/Suite/case` for a swift-test package;
                              a runner project: `<test file stem>/<describe > ... > test>`)
    print:<path>              a committed screenshot, for a flow that is only what it looks like
    manual:YYYY-MM-DD:<who>   checked by hand on a device; expires after MANUAL_TTL_DAYS; a future date is broken
    device:<reason>           automating the flow is impossible; not a pass (`device only`); the reason has
                              12 characters or more, and it never sits beside another proof

Languages: a map is English (docs/FLOWS.md; statuses validated, revalidate, no proof, device only, broken;
headers Flow, Entry, Proof, Status, Validated on; a scoreboard block) or Brazilian Portuguese (docs/FLUXOS.md;
validado, revalidar, sem prova, so em aparelho, quebrado; Fluxo, Entrada, Prova, Status, Validado em; placar).
The engine reads the language from the map (its scoreboard markers, its header row, the status words in its
rows, then its file name) and writes the map back in it. A map with no such mark yet takes --language
(default en); the same flag picks the language of a map written by --init. The state file stores each status
in the map's language and reads either.

Modes:
    --only-stale               the daily driver: run only the flows in revalidate, no proof or broken
    --from-cache, --invalidate recompute from the stored state and the sources; no test runs, no device
    --invalidate-classes A,B   demote every validated flow whose proofs all resolve into these classes;
                               alone it only recomputes, with --only-stale it reruns them
    --check                    verify and write nothing; exit 1 on a broken flow or a typed Status cell
    --reuse <bundle.xcresult>...  judge the flows whose every automated proof is in the union of the existing
                               bundles given, each read with its own date (a runner project: JUnit XML files
                               or folders of them)
    --resume                   continue an interrupted run
    --changed [--since REF]    run the flows whose cited files or test types git reports as changed
    --ui                       add the UI classes to a full or --changed run (and to every mode when the
                               configuration sets ui_needs_flag)
    --prune-state              drop the state entries of flows no longer in the map
    --init                     write a starter map in the chosen language and exit; never overwrites a map
    no mode flag               a full run of the unit and package classes
    --language, --device, --derived-data, --result-path, --batch-size, --today: see --help

Configuration (scripts/flows.json in the project): project (required), xcodeproj, scheme, platform (ios,
macos, docker or rust), device_kind, devices, device_classes, unit_targets, ui_targets, packages, sources,
source_patterns, derived_data, results_dir, ui_needs_flag, refuse_mixed_ui_unit; a docker or rust project adds
runners (its test commands, which write JUnit XML). Skill workproof:mapping-flows holds their meaning
(references/configuration.md).

Exit codes: 0 clean; 1 a flow is broken, a batch produced no result, or --check found a difference;
2 the input is unusable (configuration, map, state, arguments) and nothing was written.

Every subprocess (xcodebuild, swift test, xcresulttool, simctl, git, a runner project's test commands)
and the clock go through one runtime object, so its tests replace them.
Python 3.10, standard library.
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

STATE_VERSION = 2
MANUAL_TTL_DAYS = 90
DEVICE_REASON_MIN = 12

# The statuses, by their canonical (English) name: every comparison inside the engine uses these.
OK = "validated"
MISSING = "no proof"
DEVICE = "device only"
BROKEN = "broken"
REVALIDATE = "revalidate"
STATUSES = (OK, REVALIDATE, MISSING, DEVICE, BROKEN)
FREE = ("print:", "manual:", "device:")
NO_DATE = "—"
DEFAULT_LANGUAGE = "en"


class Language:
    """Everything a map says in words: status labels (shown in the map), stored words (kept in the state file),
    the header row, the scoreboard block and the file name."""

    def __init__(self, code, map_name, labels, stored, headers, markers, board, footer):
        self.code, self.map_name = code, map_name
        self.labels, self.stored = labels, stored
        self.headers = headers          # ID, flow, entry, proof, status, validated on
        self.start, self.end = markers
        self.board = board              # the scoreboard heading after Total, in STATUSES order
        self.footer = footer            # format string with {date}

    def header_row(self):
        return "| " + " | ".join(self.headers) + " |\n|---|---|---|---|---|---|\n"

    def words(self):
        return set(self.labels.values()) | set(self.stored.values())


LANGUAGES = {
    "en": Language(
        "en", "FLOWS.md",
        labels={OK: "validated", REVALIDATE: "revalidate", MISSING: "no proof", DEVICE: "device only",
                BROKEN: "broken"},
        stored={OK: "validated", REVALIDATE: "revalidate", MISSING: "no proof", DEVICE: "device only",
                BROKEN: "broken"},
        headers=("ID", "Flow", "Entry", "Proof", "Status", "Validated on"),
        markers=("<!-- scoreboard:start -->", "<!-- scoreboard:end -->"),
        board=("validated", "revalidate", "no proof", "device only", "broken"),
        footer="*Recomputed on {date} by `check-flows`. This scoreboard is not written by hand.*"),
    "pt-BR": Language(
        "pt-BR", "FLUXOS.md",
        labels={OK: "validado", REVALIDATE: "revalidar", MISSING: "sem prova", DEVICE: "so em aparelho",
                BROKEN: "quebrado"},
        stored={OK: "validado", REVALIDATE: "revalidar", MISSING: "sem prova", DEVICE: "só em aparelho",
                BROKEN: "quebrado"},   # the device word keeps its accent on disk, as these state files hold it
        headers=("ID", "Fluxo", "Entrada", "Prova", "Status", "Validado em"),
        markers=("<!-- placar:inicio -->", "<!-- placar:fim -->"),
        board=("validados", "revalidar", "sem prova", "so em aparelho", "quebrados"),
        footer="*Recalculado em {date} por `check-flows`. Este placar não é escrito à mão.*"),
}
WORD_TO_STATUS = {word: status for lang in LANGUAGES.values()
                  for status, word in list(lang.labels.items()) + list(lang.stored.items())}


def detect_language(text, file_name=None):
    """The language a map is written in, or None when it carries no mark of either: the scoreboard markers first,
    then the header row, then the status words of its rows, then its file name."""
    for code, lang in LANGUAGES.items():
        if lang.start in text or lang.end in text:
            return code
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = tuple(c.strip() for c in line.strip().strip("|").split("|"))
        for code, lang in LANGUAGES.items():
            if cells[:6] == lang.headers:
                return code
    votes = {code: 0 for code in LANGUAGES}
    for match in ROW_RE.finditer(text):
        cells = [c.strip() for c in match.group("rest").split("|")]
        word = cells[3] if len(cells) > 3 else ""
        owners = [code for code, lang in LANGUAGES.items() if word in lang.words()]
        if len(owners) == 1:
            votes[owners[0]] += 1
    best = max(votes.values())
    if best and list(votes.values()).count(best) == 1:
        return next(code for code, count in votes.items() if count == best)
    for code, lang in LANGUAGES.items():
        if file_name == lang.map_name and code != DEFAULT_LANGUAGE:
            return code
    return None


DOCKER = "docker"
RUST = "rust"     # a Rust workspace: the runner path of a docker project, with `cargo nextest` and its own test scan
# What a runner appends to its command to run only the named tests, per framework: `{pattern}` is one regular
# expression of the cited tests, `{files}` the basenames of their test files (one argument each).
FRAMEWORK_ONLY_ARGS = {"vitest": ["-t", "{pattern}"], "playwright": ["--grep", "{pattern}"],
                       "nextest": ["-E", "test(/{pattern}/)"]}
DOCKER_TEST_PATTERNS = ["*.spec.ts", "*.test.ts", "*.spec.tsx", "*.test.tsx", "*.e2e.ts"]
DOCKER_SOURCE_PATTERNS = ["*.ts", "*.tsx", "*.json"]
RUST_TEST_PATTERNS = ["*.rs"]
RUST_SOURCE_PATTERNS = ["*.rs", "*.toml", "*.json"]
DOCKER_SKIPPED_DIRS = {"node_modules", ".next", ".turbo", ".git", ".build", "dist", "coverage", "test-results",
                       ".vitest", "playwright-report", "__pycache__", "target"}
RUNNER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
RUNNER_CLASS = "run"   # the "class" of a docker runner: `<runner>/run`
ABSENT = "Absent"      # a stored result: selected by name in a narrowed run, and the run's report does not hold it
UNHANDLED_ERRORS = "vitest unhandled errors"   # the testsuite (and classname) Vitest's JUnit reporter gives to errors no test owns
MAX_COMMAND_LINE = 8000   # characters: under cmd.exe's 8191 and CreateProcess's 32767; a longer narrowed run goes whole
NARROWED_FILE = "narrowed.json"   # in a runner's archive folder: the proofs a narrowed run selected

FLOW_ID_RE = re.compile(r"F-[A-Z]+-\d+")
# Any table row whose first cell starts with "F-". `[ \t]*` at the end, never `\s*`: under MULTILINE `\s*`
# also eats the newline after the last row and a rewrite glues the next line onto the table.
ROW_RE = re.compile(r"^\|[ \t]*(?P<id>F-[^|]*?)[ \t]*\|(?P<rest>.*)\|[ \t]*$", re.MULTILINE)
CODE_PROOF_RE = re.compile(r"[A-Z][A-Za-z0-9_]*/[A-Za-z_][A-Za-z0-9_]*\(\)")
UDID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
RUN_MOMENT_RE = re.compile(r"(\d{8}T\d{6})-")


class Stop(Exception):
    """An unusable input: the engine prints the message and exits with the code, having written nothing."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Runtime:
    """The real subprocesses and clock. The tests replace this object."""

    def run(self, cmd, cwd=None, env=None):
        try:
            return subprocess.run([str(c) for c in cmd], cwd=None if cwd is None else str(cwd), env=env,
                                  capture_output=True, text=True)
        except OSError as error:
            return subprocess.CompletedProcess(cmd, 127, "", str(error))

    def now(self):
        return datetime.datetime.now()

    def machine(self):
        return platform.machine()


def mac_arch(machine):
    """The `arch=` of a macOS destination for this machine's processor: Apple silicon unless it reports Intel."""
    return "x86_64" if (machine or "").lower() in ("x86_64", "amd64", "i386") else "arm64"


def canon(status):
    """The canonical status of a word in either language, a stored or a shown one; anything else comes back as is."""
    return WORD_TO_STATUS.get(status, status)


def is_free(proof):
    return proof.startswith(FREE)


def needs_no_run(proofs):
    """A flow judged without any test run: only print:/manual:/device: proofs, or a device: proof at all
    (a device: proof beside a test is broken by rule, so its test is never planned)."""
    return all(is_free(p) for p in proofs) or any(p.startswith("device:") for p in proofs)


def split_proofs(cell):
    """Proofs are separated by <br> and by nothing else: test names are sentences, punctuation included."""
    found = []
    for part in re.split(r"<br\s*/?>", cell):
        proof = part.strip().strip("`").strip()
        if proof and proof != NO_DATE:
            found.append(proof)
    return found


def run_moment(run_id):
    """When a run happened, read from its id; None when the id carries no time ("no-run")."""
    stamped = RUN_MOMENT_RE.match(run_id or "")
    if stamped:
        return datetime.datetime.strptime(stamped.group(1), "%Y%m%dT%H%M%S")
    return None


def date_of_mtime(path):
    return datetime.date.fromtimestamp(os.path.getmtime(str(path)))


def write_atomically(path, text):
    """A temporary file in the same folder, then os.replace: a kill never leaves half a file."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as handle:  # LF on every platform: a text-mode write on Windows made CRLF
        handle.write(text)
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------- configuration


class Config:
    def __init__(self, root, args):
        path = root / "scripts" / "flows.json"
        if not path.exists():
            raise Stop(2, f"configuration not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise Stop(2, f"configuration unreadable: {path} ({error})")
        if not isinstance(data, dict) or not data.get("project"):
            raise Stop(2, f"configuration needs a \"project\" key: {path}")
        self.project = data["project"]
        self.xcodeproj = data.get("xcodeproj") or f"{self.project}.xcodeproj"
        self.scheme = data.get("scheme") or self.project
        self.platform = data.get("platform", "ios")
        if self.platform not in ("ios", "macos", DOCKER, RUST):
            raise Stop(2, f"configuration: platform must be ios, macos, docker or rust, not {self.platform!r}")
        # A Rust workspace walks the docker path: named runners that write JUnit XML into a folder.
        self.is_rust = self.platform == RUST
        if self.is_rust:
            self.platform = DOCKER
        docker = self.platform == DOCKER
        self.device_kind = data.get("device_kind", "phone")
        # The simulator each device kind runs on (a name or a UDID), used when --device is not given.
        self.devices = data.get("devices") or {}
        if not isinstance(self.devices, dict) or not all(
                isinstance(kind, str) and isinstance(device, str) and device for kind, device in self.devices.items()):
            raise Stop(2, "configuration: \"devices\" must map a device kind (phone, ipad, ...) to a simulator "
                          "name or UDID")
        self.device_classes = dict(data.get("device_classes") or {})
        self.unit_targets = [(t["name"], t.get("dir", t["name"])) for t in data.get("unit_targets") or []]
        self.ui_targets = [(t["name"], t.get("dir", t["name"])) for t in data.get("ui_targets") or []]
        if docker:
            foreign = [key for key in ("unit_targets", "ui_targets", "packages", "device_classes") if data.get(key)]
            if foreign:
                raise Stop(2, "configuration: " + ", ".join(foreign) + " do not apply to a docker project; "
                              "its tests are declared in \"runners\"")
        self.runners = self._read_runners(data.get("runners"), self.is_rust) if docker else {}
        self.packages = []
        for pkg in data.get("packages") or []:
            runner = pkg.get("runner", "xcodebuild")
            if runner not in ("xcodebuild", "swift-test"):
                raise Stop(2, f"configuration: package runner must be xcodebuild or swift-test, not {runner!r}")
            if runner == "xcodebuild" and not pkg.get("scheme"):
                raise Stop(2, f"configuration: package {pkg.get('target')} runs through xcodebuild and needs a scheme")
            self.packages.append({"target": pkg["target"], "dir": pkg["dir"],
                                  "tests_dir": pkg.get("tests_dir", pkg["dir"] + "/Tests"),
                                  "runner": runner, "scheme": pkg.get("scheme")})
        self.source_patterns = list(data.get("source_patterns")
                                    or (RUST_SOURCE_PATTERNS if self.is_rust else DOCKER_SOURCE_PATTERNS if docker
                                        else ["*.swift", "*.xcstrings"]))
        self.sources = []
        # Without a "sources" key the app folder and the test folders are indexed: an empty index would
        # turn every flow that cites a file into "cited file not found".
        default_sources = [self.project] + [t.get("dir", t["name"]) for t in
                                            (data.get("unit_targets") or []) + (data.get("ui_targets") or [])]
        if docker and not data.get("sources"):
            raise Stop(2, "configuration: a docker project needs a \"sources\" key, the folders its Entry cells "
                          "cite (a default would index node_modules or miss a package)")
        for entry in data.get("sources") or default_sources:
            if isinstance(entry, str):
                self.sources.append((entry, self.source_patterns))
            else:
                self.sources.append((entry["dir"], list(entry.get("patterns") or self.source_patterns)))
        self.ui_needs_flag = bool(data.get("ui_needs_flag", False))
        self.refuse_mixed = bool(data.get("refuse_mixed_ui_unit", False))

        if docker:
            self.derived_data = None   # no build folder: the runners build inside their containers
        elif args.derived_data:
            self.derived_data = pathlib.Path(args.derived_data)
        elif data.get("derived_data"):
            self.derived_data = self._path(root, data["derived_data"])
        else:
            home = pathlib.Path(os.path.expanduser("~"))
            self.derived_data = home / "Library" / "Developer" / "Xcode" / "DerivedData" / f"flows-{self.project}"
        if data.get("results_dir"):
            self.results_dir = self._path(root, data["results_dir"])
        elif docker:
            self.results_dir = root / "test-results" / "flows"
        else:
            self.results_dir = self.derived_data / "results"

        self.ui_names = {name for name, _ in self.ui_targets} | {n for n, r in self.runners.items() if r["ui"]}
        self.swift_test_targets = {p["target"] for p in self.packages if p["runner"] == "swift-test"}
        self.xcode_packages = {p["target"]: p for p in self.packages if p["runner"] == "xcodebuild"}
        extensions = []
        for _, patterns in self.sources:
            for pattern in patterns + self.source_patterns:
                if pattern.startswith("*.") and pattern[2:] not in extensions:
                    extensions.append(pattern[2:])
        self.patterns = sorted({p for _, ps in self.sources for p in ps} | set(self.source_patterns)
                               | {p for r in self.runners.values() for _, ps in r["tests"] for p in ps})
        alternatives = "|".join(re.escape(e) for e in extensions) or "swift"
        if docker:
            # a TypeScript project names `crypto.service.ts` (dots inside the name) and tells `page.tsx` apart by
            # the folders before it: `apps/web/src/app/(dashboard)/providers/page.tsx`
            self.source_re = re.compile(r"(?<![A-Za-z0-9_+.@/-])((?:[A-Za-z0-9_@.()\[\]+-]+/)*[A-Za-z0-9_+.-]+\.(?:"
                                        + alternatives + r"))(?![A-Za-z0-9_])")
        else:
            self.source_re = re.compile(r"(?<![A-Za-z0-9_+-])([A-Za-z0-9_+-]+\.(?:" + alternatives
                                        + r"))(?![A-Za-z0-9_])")

    @staticmethod
    def _read_runners(raw, rust=False):
        """The docker project's test runners, by name: each one command that writes JUnit XML into a folder."""
        if not isinstance(raw, list) or not raw:
            raise Stop(2, "configuration: a docker project needs a non-empty \"runners\" list")
        runners = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise Stop(2, f"configuration: a runner must be an object, not {entry!r}")
            name = entry.get("name")
            if not isinstance(name, str) or not RUNNER_NAME_RE.fullmatch(name):
                raise Stop(2, f"configuration: a runner needs a \"name\" of letters, digits, - and _ (got {name!r})")
            if name in runners:
                raise Stop(2, f"configuration: runner {name!r} is declared twice")
            command = entry.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
                raise Stop(2, f"configuration: runner {name} needs \"command\", a list of argv strings")
            framework = entry.get("framework")
            if framework not in FRAMEWORK_ONLY_ARGS:
                raise Stop(2, f"configuration: runner {name} needs \"framework\": " + " or ".join(FRAMEWORK_ONLY_ARGS))
            junit_dir = entry.get("junit_dir")
            if (not isinstance(junit_dir, str) or not junit_dir.strip()
                    or pathlib.PurePosixPath(junit_dir).is_absolute() or ".." in pathlib.PurePosixPath(junit_dir).parts):
                raise Stop(2, f"configuration: runner {name} needs \"junit_dir\", a folder inside the project")
            only_args = entry.get("only_args", FRAMEWORK_ONLY_ARGS[framework])
            if not isinstance(only_args, list) or not all(isinstance(a, str) for a in only_args):
                raise Stop(2, f"configuration: runner {name}: \"only_args\" must be a list of strings")
            ui = entry.get("ui", False)
            if not isinstance(ui, bool):
                raise Stop(2, f"configuration: runner {name}: \"ui\" must be true or false")
            tests = []
            for spec in entry.get("tests") or []:
                folder = spec if isinstance(spec, str) else (spec or {}).get("dir")
                if not isinstance(folder, str) or not folder:
                    raise Stop(2, f"configuration: runner {name}: every \"tests\" entry needs a \"dir\"")
                patterns = [] if isinstance(spec, str) else list(spec.get("patterns") or [])
                tests.append((folder, patterns or list(RUST_TEST_PATTERNS if rust else DOCKER_TEST_PATTERNS)))
            if not tests:
                raise Stop(2, f"configuration: runner {name} needs \"tests\", the folders scanned for declared tests")
            runners[name] = {"name": name, "command": list(command), "framework": framework, "ui": ui,
                             "junit_dir": junit_dir, "only_args": list(only_args), "tests": tests}
        return runners

    @staticmethod
    def _path(root, value):
        path = pathlib.Path(os.path.expanduser(value))
        return path if path.is_absolute() else root / path

    def test_dirs(self):
        """(target, folder, kind) for every test target the engine scans for declarations."""
        if self.platform == DOCKER:
            return [(name, folder, DOCKER) for name, spec in self.runners.items() for folder, _ in spec["tests"]]
        dirs = [(name, folder, "unit") for name, folder in self.unit_targets]
        dirs += [(name, folder, "ui") for name, folder in self.ui_targets]
        dirs += [(p["target"], p["tests_dir"], p["runner"]) for p in self.packages]
        return dirs


def walk_files(base, pattern):
    """Files under base matching pattern, sorted, with every `.build` folder skipped."""
    if not base.is_dir():
        return []
    found = []
    for path in base.rglob(pattern):
        if ".build" in path.relative_to(base).parts or not path.is_file():
            continue
        found.append(path)
    return sorted(found)


def walk_files_pruned(base, patterns):
    """Files under base matching any pattern, sorted, without descending into dependency, build or report folders
    (a docker project's node_modules would take minutes to walk)."""
    if not base.is_dir():
        return []
    found = []
    for folder, dirs, names in os.walk(str(base)):
        dirs[:] = sorted(d for d in dirs if d not in DOCKER_SKIPPED_DIRS)
        for name in sorted(names):
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                found.append(pathlib.Path(folder) / name)
    return sorted(found)


# ---------------------------------------------------------------- tests declared in TypeScript sources

JS_REGEX_AFTER = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield",
                  "await", "instanceof"}
JS_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
TS_TEST_BASES = {"describe": "describe", "suite": "describe", "xdescribe": "describe", "fdescribe": "describe",
                 "it": "test", "test": "test", "xit": "test", "xtest": "test", "fit": "test"}
TS_MODIFIERS = {"skip", "only", "todo", "fixme", "fails", "fail", "slow", "concurrent", "sequential", "serial",
                "parallel", "shuffle", "each", "for", "skipIf", "runIf", "describe"}
TS_CURRIED = {"each", "for", "skipIf", "runIf"}
TITLE_SEPARATOR = " > "


def norm_title(text):
    """Playwright writes a title path with a guillemet (U+203A) between names, Vitest with ` > `: the engine keeps one form, the ASCII one."""
    return text.replace(" \u203a ", TITLE_SEPARATOR)


def js_unescape(text):
    """A JavaScript string literal's content as the reporter prints it."""
    out, pos = [], 0
    while pos < len(text):
        char = text[pos]
        if char != "\\" or pos + 1 >= len(text):
            out.append(char)
            pos += 1
            continue
        nxt = text[pos + 1]
        if nxt == "x" and re.fullmatch(r"[0-9A-Fa-f]{2}", text[pos + 2:pos + 4]):
            out.append(chr(int(text[pos + 2:pos + 4], 16)))
            pos += 4
        elif nxt == "u" and text[pos + 2:pos + 3] == "{" and "}" in text[pos + 3:]:
            end = text.index("}", pos + 3)
            try:
                out.append(chr(int(text[pos + 3:end], 16)))
            except (ValueError, OverflowError):
                out.append(text[pos:end + 1])
            pos = end + 1
        elif nxt == "u" and re.fullmatch(r"[0-9A-Fa-f]{4}", text[pos + 2:pos + 6]):
            out.append(chr(int(text[pos + 2:pos + 6], 16)))
            pos += 6
        elif nxt == "\n":
            pos += 2   # a line continuation
        else:
            out.append(JS_SIMPLE_ESCAPES.get(nxt, nxt))
            pos += 2
    return "".join(out)


def skip_quoted(text, pos):
    """Index after the '...' or "..." literal that opens at pos (an unterminated one ends at its line)."""
    quote, pos = text[pos], pos + 1
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
        elif text[pos] == quote:
            return pos + 1
        elif text[pos] == "\n":
            return pos
        else:
            pos += 1
    return len(text)


def skip_template(text, pos):
    """(index after the template literal opening at pos, whether it holds a ${...} expression)."""
    pos, has_expr = pos + 1, False
    while pos < len(text):
        char = text[pos]
        if char == "\\":
            pos += 2
        elif char == "`":
            return pos + 1, has_expr
        elif char == "$" and text[pos + 1:pos + 2] == "{":
            has_expr, depth, pos = True, 1, pos + 2
            while pos < len(text) and depth:
                inner = text[pos]
                if inner in "'\"":
                    pos = skip_quoted(text, pos)
                    continue
                if inner == "`":
                    pos = skip_template(text, pos)[0]
                    continue
                depth += 1 if inner == "{" else -1 if inner == "}" else 0
                pos += 1
        else:
            pos += 1
    return len(text), has_expr


JSX_TAG_RE = re.compile(r"[A-Za-z_$][\w$.:-]*")
JSX_CLOSE_RE = re.compile(r"</\s*([^>\s]*)\s*>")
JSX_BEFORE = "(=,?:{[&|!;>"   # punctuation after which `<` and a letter (or `>`) open an element, not a comparison


def jsx_may_open(text, pos):
    """True when the `<` at pos can open a JSX element: a tag name or `>` follows, and what comes before is not a
    value (an identifier, a number, a closing bracket), so `a < b` and `useState<string>` are not elements."""
    if text[pos + 1:pos + 2] != ">" and not JSX_TAG_RE.match(text, pos + 1):
        return False
    before = pos - 1
    while before >= 0 and text[before].isspace():
        before -= 1
    if before < 0 or text[before] in JSX_BEFORE:
        return True
    start = before
    while start >= 0 and (text[start].isalnum() or text[start] in "_$"):
        start -= 1
    return text[start + 1:before + 1] in JS_REGEX_AFTER


def skip_braces(text, pos):
    """Index after the {...} that opens at pos (strings, templates, comments and elements inside it skipped), or
    None when it never closes."""
    depth, size = 0, len(text)
    while pos < size:
        char = text[pos]
        if char in "'\"":
            pos = skip_quoted(text, pos)
        elif char == "`":
            pos = skip_template(text, pos)[0]
        elif text.startswith("//", pos):
            end = text.find("\n", pos)
            pos = size if end < 0 else end
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = size if end < 0 else end + 2
        elif char == "<" and jsx_may_open(text, pos):
            end = skip_jsx(text, pos)
            pos = pos + 1 if end is None else end
        else:
            depth += 1 if char == "{" else -1 if char == "}" else 0
            pos += 1
            if char == "}" and not depth:
                return pos
    return None


def skip_jsx(text, pos):
    """Index after the JSX element that opens at pos (`<`): attributes, `{...}` expressions and the text between
    the tags are skipped as they are, so a quote or `//` in the text starts nothing. None when the element does not
    close (then it was not an element, a generic arrow `<T,>(x) => x` for one)."""
    size = len(text)
    pos += 1
    if text[pos:pos + 1] == ">":
        name, pos = "", pos + 1
    else:
        match = JSX_TAG_RE.match(text, pos)
        if not match:
            return None
        name, pos = match.group(), match.end()
        while True:
            while pos < size and text[pos].isspace():
                pos += 1
            if text.startswith("/>", pos):
                return pos + 2
            if text[pos:pos + 1] == ">":
                pos += 1
                break
            if text[pos:pos + 1] == "{":   # {...spread}
                pos = skip_braces(text, pos)
            else:
                attribute = JSX_TAG_RE.match(text, pos)
                if not attribute:
                    return None
                pos = attribute.end()
                while pos < size and text[pos].isspace():
                    pos += 1
                if text[pos:pos + 1] == "=":
                    pos += 1
                    while pos < size and text[pos].isspace():
                        pos += 1
                    value = text[pos:pos + 1]
                    if value in ("'", '"'):
                        close = text.find(value, pos + 1)
                        pos = None if close < 0 else close + 1
                    elif value == "{":
                        pos = skip_braces(text, pos)
                    elif value == "<":
                        pos = skip_jsx(text, pos)
                    else:
                        return None
            if pos is None:
                return None
    while pos < size:
        char = text[pos]
        if text.startswith("</", pos):
            close = JSX_CLOSE_RE.match(text, pos)
            return close.end() if close else None
        if char == "{":
            pos = skip_braces(text, pos)
        elif char == "<" and jsx_may_open(text, pos):
            pos = skip_jsx(text, pos)
        else:
            pos += 1
        if pos is None:
            return None
    return None


def ts_tokens(text, jsx=False):
    """[(kind, value)] for a TypeScript or JavaScript source: `id`, `str` (a literal: its content), `dyn` (a template
    with an expression), `num`, `re`, `jsx` (a whole JSX element, only with `jsx=True`, for a .tsx file) and `p`
    (one punctuation character). Comments are dropped."""
    tokens, pos, size = [], 0, len(text)

    def regex_allowed():
        if not tokens:
            return True
        kind, value = tokens[-1]
        if kind in ("str", "dyn", "num", "re", "jsx"):
            return False
        if kind == "id":
            return value in JS_REGEX_AFTER
        if value == "<":
            return False   # `</`: the closing tag of an element, never `a < /re/`
        if len(tokens) > 1 and tokens[-2] == tokens[-1] and value in "+-":
            return False   # `i++ / 2`
        return value not in (")", "]", "}")

    while pos < size:
        char = text[pos]
        two = text[pos:pos + 2]
        element = skip_jsx(text, pos) if (jsx and char == "<" and jsx_may_open(text, pos)) else None
        if char.isspace():
            pos += 1
        elif element is not None:
            tokens.append(("jsx", ""))
            pos = element
        elif two == "//":
            end = text.find("\n", pos)
            pos = size if end < 0 else end
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            pos = size if end < 0 else end + 2
        elif char in "'\"":
            end = skip_quoted(text, pos)
            closed = end - 1 > pos and text[end - 1] == char
            tokens.append(("str", text[pos + 1:end - 1 if closed else end]))
            pos = end
        elif char == "`":
            end, has_expr = skip_template(text, pos)
            tokens.append(("dyn", "") if has_expr else ("str", text[pos + 1:end - 1]))
            pos = end
        elif char == "/" and regex_allowed():
            pos += 1
            in_class = False
            while pos < size and text[pos] != "\n":
                if text[pos] == "\\":
                    pos += 2
                    continue
                if text[pos] == "[":
                    in_class = True
                elif text[pos] == "]":
                    in_class = False
                elif text[pos] == "/" and not in_class:
                    pos += 1
                    break
                pos += 1
            while pos < size and text[pos].isalpha():
                pos += 1
            tokens.append(("re", ""))
        elif char.isalpha() or char in "_$":
            end = pos + 1
            while end < size and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            tokens.append(("id", text[pos:end]))
            pos = end
        elif char.isdigit():
            end = pos + 1
            while end < size and (text[end].isalnum() or text[end] in "._"):
                end += 1
            tokens.append(("num", text[pos:end]))
            pos = end
        else:
            tokens.append(("p", char))
            pos += 1
    return tokens


def extend_bases(tokens):
    """{name: (kind, base)} for the names assigned from `<something>.extend(...)` (Playwright's and Vitest's
    `test.extend`, written `const myTest = base.extend<T>({...})`): each is a test base used with the forms of
    `test`. `base` is the built-in name the forms and the x/f prefixes are read from."""
    found = {}
    for i in range(1, len(tokens) - 4):
        if tokens[i] != ("p", "=") or tokens[i - 1][0] != "id" or tokens[i + 1][0] != "id":
            continue
        if tokens[i + 2] != ("p", ".") or tokens[i + 3] != ("id", "extend") or tokens[i + 4] not in (("p", "("), ("p", "<")):
            continue
        name, root = tokens[i - 1][1], tokens[i + 1][1]
        if name in TS_TEST_BASES:
            continue
        found[name] = ("test", root if root in TS_TEST_BASES else found.get(root, ("test", "test"))[1])
    return found


def declared_ts_tests(text, jsx=False):
    """(tests, loose) for one test file: the tests it declares as `(full title, mode)`, the title being the describe
    chain and the test name joined by ` > `, the mode `run`, `skip` (also `todo` and `fixme`) or `only`. `loose` is
    True when the file holds a title the scan cannot read (a variable, a template with an expression, `.each`) or
    parentheses that do not balance: its declared set is then not known to be complete, and a proof naming a test
    that is not in it is never called gone. `jsx` is True for a .tsx or .jsx file (elements are skipped as one
    token). A name assigned from `<something>.extend(...)` is a test base like `test` (`myTest('x', ...)`)."""
    tokens = ts_tokens(text, jsx)
    size = len(tokens)
    bases = {name: (kind, name) for name, kind in TS_TEST_BASES.items()}
    bases.update(extend_bases(tokens))
    close_of, stack, loose = {}, [], False
    for index, (kind, value) in enumerate(tokens):
        if kind == "p" and value == "(":
            stack.append(index)
        elif kind == "p" and value == ")":
            if stack:
                close_of[stack.pop()] = index
            else:
                loose = True
    loose = loose or bool(stack)
    found = []

    def call_at(index):
        """(kind, modifiers, index of the opening parenthesis, dynamic) of the test API call starting here."""
        kind, value = tokens[index]
        if kind != "id" or value not in bases or (index and tokens[index - 1] == ("p", ".")):
            return None
        mods, j = [], index + 1
        while j + 1 < size and tokens[j] == ("p", ".") and tokens[j + 1][0] == "id":
            mods.append(tokens[j + 1][1])
            j += 2
        if any(m not in TS_MODIFIERS for m in mods):
            return None
        dynamic = bool(mods) and mods[-1] in ("each", "for")
        if mods and mods[-1] in TS_CURRIED:
            if j < size and tokens[j] == ("p", "(") and j in close_of:
                j = close_of[j] + 1
            else:
                return ("test", mods, None, True, bases[value][1]) if dynamic else None
        if j < size and tokens[j] == ("p", "(") and j in close_of:
            return (bases[value][0] if "describe" not in mods else "describe"), mods, j, dynamic, bases[value][1]
        return None

    def walk(lo, hi, chain, inherited):
        nonlocal loose
        i = lo
        while i < hi:
            call = call_at(i)
            if call is None:
                i += 1
                continue
            if call[2] is None:   # `.each` written in a form the scan cannot follow
                loose = True
                i += 1
                continue
            kind, mods, open_at, dynamic, base = call
            end = close_of[open_at]
            if open_at + 1 == end:   # `test.slow()` and the like: an annotation, not a declaration
                i = end + 1
                continue
            mode = "skip" if (set(mods) & {"skip", "todo", "fixme"} or base.startswith("x")) else (
                "only" if ("only" in mods or base.startswith("f")) else "run")
            mode = inherited if mode == "run" else mode
            first = tokens[open_at + 1]
            title = None
            if first[0] == "str" and not dynamic and tokens[open_at + 2] in (("p", ","), ("p", ")")):
                title = js_unescape(first[1])
            else:
                loose = True
            if kind == "describe":
                walk(open_at + 1, end, chain + [title], mode)
            elif title is not None and None not in chain:
                found.append((norm_title(TITLE_SEPARATOR.join(chain + [title])), mode))
            i = end + 1

    walk(0, size, [], "run")
    return found, loose


# ---------------------------------------------------------------- tests declared in the source

TYPE_DECL_RE = re.compile(r"\b(struct|class|enum|actor|extension)\s+([A-Z][A-Za-z0-9_]*)")
FUNC_RE = re.compile(r"\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
TEST_DISPLAY_RE = re.compile(r'@Test\s*\(\s*"((?:[^"\\]|\\.)*)"')
RAW_STRING_OPEN_RE = re.compile(r'#+"')
SWIFT_ESCAPE_RE = re.compile(r"\\(?:u\{([0-9A-Fa-f]{1,8})\}|(.))", re.DOTALL)
SWIFT_SIMPLE_ESCAPES = {"0": "\0", "\\": "\\", "t": "\t", "n": "\n", "r": "\r", '"': '"', "'": "'"}
SUITE_AFTER_RE = re.compile(
    r"\s*(?:@\w+(?:\([^()]*\))?\s*)*"
    r"(?:(?:public|private|internal|fileprivate|open|final|nonisolated|package|indirect)\s+)*"
    r"(?:struct|class|enum|actor|extension)\s+([A-Z][A-Za-z0-9_]*)")


def unescape_swift(text):
    """A Swift string literal's content as the test report shows it: the escapes for a quote, a backslash,
    tab, newline, return, NUL and `u{hex}` become their characters; anything else (an interpolation) stays."""
    def one(match):
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)
        return SWIFT_SIMPLE_ESCAPES.get(match.group(2), match.group(0))
    return SWIFT_ESCAPE_RE.sub(one, text)


def labelled_func_name(text, match):
    """`name(label:other:)` for a `func` with parameters, the name a parameterized Swift Testing case is reported
    under (an unlabelled parameter is `_:`); None for a function with none, or a signature that does not close."""
    depth, start, parts = 1, match.end(), []
    i = start
    while i < len(text) and depth:
        ch = text[i]
        if ch in "([<":
            depth += 1
        elif ch in ")]>" and not (ch == ">" and text[i - 1] == "-"):
            depth -= 1
        elif ch == "," and depth == 1:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    if depth:
        return None
    parts.append(text[start:i - 1])
    labels = []
    for part in parts:
        head = part.split(":", 1)[0].split()
        if len(head) == 0 or ":" not in part:
            return None
        labels.append(head[0] + ":")
    return f"{match.group(1)}({''.join(labels)})"


def declared_display_names(text):
    """Every @Test("...") display name in the text, unescaped."""
    return [unescape_swift(name) for name in TEST_DISPLAY_RE.findall(text)]


def suite_declarations(text):
    """(display name, Type) for every @Suite("...") declaration, nested parentheses in its arguments allowed."""
    found = []
    for match in re.finditer(r"@Suite\s*\(", text):
        depth, pos = 1, match.end()
        while pos < len(text) and depth:
            char = text[pos]
            if char == '"':
                pos += 1
                while pos < len(text) and text[pos] != '"':
                    pos += 2 if text[pos] == "\\" else 1
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            pos += 1
        args = text[match.end():pos - 1]
        name = re.match(r'\s*"((?:[^"\\]|\\.)*)"', args)
        after = SUITE_AFTER_RE.match(text, pos)
        if name and after:
            found.append((unescape_swift(name.group(1)), after.group(1)))
    return found


def mask_swift(text):
    """The text with comments and string-literal contents blanked to spaces (newlines kept, same length), so a
    brace or a keyword inside either never counts and every offset still points into the original."""
    out, size, pos = list(text), len(text), 0

    def blank(start, end):
        for k in range(start, min(end, size)):
            if out[k] != "\n":
                out[k] = " "

    def string_end(start):
        """Index after the string literal whose first character (a # or the quote) is at start; interpolations
        are blanked and may hold strings of their own."""
        hashes = 0
        while start + hashes < size and text[start + hashes] == "#":
            hashes += 1
        quote = start + hashes
        triple = text.startswith('"""', quote)
        close = ('"""' if triple else '"') + "#" * hashes
        k = quote + (3 if triple else 1)
        while k < size:
            if text.startswith("\\" + "#" * hashes + "(", k):
                depth, k = 1, k + 2 + hashes
                while k < size and depth:
                    if text[k] == '"':
                        k = string_end(k)
                        continue
                    depth += 1 if text[k] == "(" else -1 if text[k] == ")" else 0
                    k += 1
                continue
            if text.startswith("\\" + "#" * hashes, k):
                k += 1 + hashes + 1
                continue
            if text.startswith(close, k):
                return k + len(close)
            if not triple and text[k] == "\n":
                return k  # an unterminated literal ends at its line
            k += 1
        return size

    while pos < size:
        two = text[pos:pos + 2]
        if two == "//":
            end = text.find("\n", pos)
            end = size if end < 0 else end
            blank(pos, end)
            pos = end
        elif two == "/*":
            depth, end = 1, pos + 2
            while end < size and depth:
                if text.startswith("/*", end):
                    depth, end = depth + 1, end + 2
                elif text.startswith("*/", end):
                    depth, end = depth - 1, end + 2
                else:
                    end += 1
            blank(pos, end)
            pos = end
        elif text[pos] == '"' or (text[pos] == "#" and RAW_STRING_OPEN_RE.match(text, pos)
                                  and (pos == 0 or not (text[pos - 1].isalnum() or text[pos - 1] == "_"))):
            end = string_end(pos)
            blank(pos, end)
            pos = end
        else:
            pos += 1
    return "".join(out)


def type_spans(text):
    """[(body start, body end, Type)] for every struct, class, enum, actor and extension whose braces balance,
    read on the masked text. The type that owns a func is the innermost span holding it, never the last type declared
    before it: a helper type above the tests, or nested among them, owns none of them."""
    masked = mask_swift(text)
    close_of, stack = {}, []
    for index, char in enumerate(masked):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            close_of[stack.pop()] = index
    spans = []
    for match in TYPE_DECL_RE.finditer(masked):
        stop = re.search(r"[{};]", masked[match.end():])
        if not stop or stop.group(0) != "{":
            continue
        opening = match.end() + stop.start()
        if opening in close_of:
            spans.append((opening, close_of[opening], match.group(2)))
    return spans


def owner_at(spans, pos):
    """The Type of the innermost span holding pos, or None."""
    best = None
    for start, end, name in spans:
        if start < pos < end and (best is None or start > best[0]):
            best = (start, name)
    return best[1] if best else None


def test_attribute_chunk(text, func_start):
    """The text a func's own attributes can sit in: from the line before it, cut after the last brace. A func
    declared inside a test's body follows that test's `{`, so the `@Test` of the line above is not its own."""
    line_start = text.rfind("\n", 0, func_start)
    previous = text.rfind("\n", 0, max(line_start, 0))
    chunk = text[max(previous, 0):func_start]
    return chunk[max(chunk.rfind("}"), chunk.rfind("{")) + 1:]


class Catalog:
    """What the test sources declare: types and their targets, @Suite display names, test functions.

    The scan is loose on purpose: it can miss a test that is gone, but it never calls a present test gone. A
    test's owning type is the innermost type or extension whose braces hold it (type_spans), so a helper type above
    or among the tests owns none of them.
    """

    def __init__(self, root, cfg):
        self.cfg = cfg
        self.type_target = {}     # Type -> target that declares it (first wins)
        self.display = {}         # @Suite display name -> {(target, Type)}
        self.type_display = {}    # Type -> its @Suite display name
        self.type_names = {}      # Type -> every test name its files hold (display names, func, func())
        self.cases = []           # (target, Type, method, test display name or None)
        self.texts = []
        for target, folder, _ in cfg.test_dirs():
            for path in walk_files(root / folder, "*.swift"):
                text = path.read_text(encoding="utf-8", errors="replace")
                self.texts.append(text)
                self._scan(target, text)
        self._words = None

    def _scan(self, target, text):
        decls = [(m.start(), m.group(1), m.group(2)) for m in TYPE_DECL_RE.finditer(text)]
        for _, kind, name in decls:
            if kind != "extension":
                self.type_target.setdefault(name, target)
        for display, type_name in suite_declarations(text):
            self.display.setdefault(display, set()).add((target, type_name))
            self.type_display.setdefault(type_name, display)
        funcs = FUNC_RE.findall(text)
        names = set(funcs) | {f + "()" for f in funcs} | set(declared_display_names(text))
        names |= {n for n in (labelled_func_name(text, m) for m in FUNC_RE.finditer(text)) if n}
        for _, _, type_name in decls:
            self.type_names.setdefault(type_name, set()).update(names)
        spans = type_spans(text)
        for match in FUNC_RE.finditer(text):
            owner = owner_at(spans, match.start())
            if owner is None:
                continue
            chunk = test_attribute_chunk(text, match.start())
            no_params = re.match(r"\s*\)", text[match.end():]) is not None
            method = match.group(1)
            if "@Test" in chunk:
                display = TEST_DISPLAY_RE.search(chunk[chunk.rfind("@Test"):])
                self.cases.append((target, owner, method, unescape_swift(display.group(1)) if display else None))
            elif method.startswith("test") and no_params:
                self.cases.append((target, owner, method, None))

    def duplicate_displays(self):
        return {name: sorted(hits) for name, hits in self.display.items() if len(hits) > 1}

    def head(self, head):
        """(target, Type) a proof's head names: an @Suite display name or a declared type; None otherwise."""
        hits = self.display.get(head)
        if hits:
            return next(iter(hits)) if len(hits) == 1 else None
        if head in self.type_target:
            return self.type_target[head], head
        return None

    def class_for(self, target, type_name):
        return f"{target}/package" if target in self.cfg.swift_test_targets else f"{target}/{type_name}"

    def words(self):
        if self._words is None:
            self._words = set()
            for text in self.texts:
                self._words.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
        return self._words

    def proof_gone(self, proof):
        """True when the source no longer declares the test a proof names (loose: never a false red)."""
        if "/" not in proof:
            return False
        head, name = proof.split("/", 1)
        if "/" in name:
            return False
        types = {t for _, t in self.display.get(head, ())}
        if head in self.type_target or head in self.type_names:
            types.add(head)
        if types:
            names = set()
            for type_name in types:
                names |= self.type_names.get(type_name, set())
            return name not in names
        return bool(CODE_PROOF_RE.fullmatch(proof)) and head not in self.words()


# ---------------------------------------------------------------- result bundles


def parse_xcresult(data, when, run_id):
    """`xcresulttool get test-results tests --compact` -> (by_path, runnable) for one bundle."""
    by_path, runnable = {}, {}

    def walk(node, target, suite):
        kind = node.get("nodeType")
        if kind in ("Unit test bundle", "UI test bundle"):
            target = node.get("name") or target
        elif kind == "Test Suite":
            suite = node.get("name") or suite
        if kind == "Test Case":
            name = node.get("name") or ""
            key = f"{suite}/{name}" if suite else name
            result = node.get("result")
            # XCTest counts a case whose only issues were anticipated (XCTExpectFailure) as passed.
            if result == "Expected Failure":
                result = "Passed"
            entry = {"result": result, "when": when, "run_id": run_id}
            ident = node.get("nodeIdentifier") or ""
            if ident:
                entry["identifier"] = ident
            old = by_path.get(key)
            if old and old.get("result") != "Passed":
                entry["result"] = old["result"]
            by_path[key] = entry
            type_name = ident.split("/")[0] if ident else suite
            if target and type_name:
                runnable[key] = f"{target}/{type_name}"
            return
        for child in node.get("children") or []:
            walk(child, target, suite)

    for top in data.get("testNodes") or []:
        walk(top, None, None)
    return by_path, runnable


def xunit_paths(path):
    """Both reports `swift test --xunit-output` may write: XCTest's and Swift Testing's sibling."""
    return [path, path.with_name(path.stem + "-swift-testing" + path.suffix)]


def parse_xunit(path, target, when, run_id):
    """Both xunit reports of one package run -> (by_path, runnable), keyed `Target/Suite/case`."""
    hits = {}
    for report in xunit_paths(path):
        if not report.exists():
            continue
        try:
            root = ET.parse(str(report)).getroot()
        except ET.ParseError as error:
            print(f"unreadable xunit report {report}: {error}", flush=True)
            continue
        for case in root.iter("testcase"):
            name = case.get("name") or ""
            if not name:
                continue
            if case.find("failure") is not None or case.find("error") is not None:
                result = "Failed"
            elif case.find("skipped") is not None:
                result = "Skipped"
            else:
                result = "Passed"
            suite = (case.get("classname") or "").split(".")[-1]
            key = f"{target}/{suite}/{name}" if suite else f"{target}/{name}"
            hits.setdefault(key, []).append(result)
    by_path, runnable = {}, {}
    for key, results in hits.items():
        if "Failed" in results:
            outcome = "Failed"
        elif all(r == "Skipped" for r in results):
            outcome = "Skipped"
        else:
            outcome = "Passed"
        by_path[key] = {"result": outcome, "when": when, "run_id": run_id}
        runnable[key] = f"{target}/package"
    return by_path, runnable


class DockerCatalog:
    """What a docker project's test files declare, read from TypeScript sources: per runner, per file, the tests with
    their full titles. A proof is `<file stem>/<full title>`: the stem is the file name without its last extension
    (`crypto.service.spec` for `crypto.service.spec.ts`), the full title the describe chain and the test name joined by
    ` > ` (the reporters' own text, Playwright's guillemet separator written as ` > `).

    The scan is loose on purpose, as the Swift one is: a file with a title it cannot read is `loose`, and a proof
    naming a test absent from a loose file is never called gone."""

    def __init__(self, root, cfg):
        self.cfg = cfg
        self.files = []       # {"runner", "stem", "name", "path", "tests": [(title, mode)], "loose"}
        self.by_stem = {}
        self.empty_runners = []
        for runner, spec in cfg.runners.items():
            seen, count = set(), 0
            for folder, patterns in spec["tests"]:
                for path in walk_files_pruned(root / folder, patterns):
                    if path in seen:
                        continue
                    seen.add(path)
                    text = path.read_text(encoding="utf-8", errors="replace")
                    tests, loose = declared_ts_tests(text, jsx=path.suffix in (".tsx", ".jsx"))
                    record = {"runner": runner, "stem": path.stem, "name": path.name,
                              "path": str(path.relative_to(root)), "tests": tests, "loose": loose}
                    self.files.append(record)
                    self.by_stem.setdefault(path.stem, []).append(record)
                    count += 1
            if not count:
                self.empty_runners.append(runner)

    def duplicate_displays(self):
        """Stems more than one test file carries: a proof citing one cannot tell the files apart."""
        return {stem: sorted((r["runner"], r["path"]) for r in records)
                for stem, records in self.by_stem.items() if len(records) > 1}

    def head(self, head):
        """(runner, stem) a proof's head names: a stem exactly one test file carries; None otherwise."""
        records = self.by_stem.get(head)
        return (records[0]["runner"], head) if records and len(records) == 1 else None

    def class_for(self, runner, stem):
        return f"{runner}/{RUNNER_CLASS}"

    def file_names(self, runner, stem):
        """The file names (basenames) a runner's tests of this stem live in."""
        return sorted({r["name"] for r in self.by_stem.get(stem, []) if r["runner"] == runner})

    def declared(self):
        """[(runner, `stem/full title`)] for every test the sources declare."""
        return [(r["runner"], f"{r['stem']}/{title}") for r in self.files for title, _ in r["tests"]]

    def proof_gone(self, proof):
        """True when the source no longer declares the test a proof names: its file is not among those scanned (when
        any was scanned), or the file does not hold the title and has no title the scan could not read."""
        if "/" not in proof:
            return False
        stem, title = proof.split("/", 1)
        records = self.by_stem.get(stem)
        if not records:
            return bool(self.files)
        if any(r["loose"] for r in records):
            return False
        title = norm_title(title)
        return not any(title == declared for r in records for declared, _ in r["tests"])


RUST_NOISE_RE = re.compile(r"""//[^\n]*|/\*.*?\*/|r(#*)".*?"\1|"(?:\\.|[^"\\])*"|'(?:\\.|[^\\'\n])'""", re.S)
RUST_TOKEN_RE = re.compile(r"#\[test\]|\bmod\s+(\w+)\s*\{|\bfn\s+(\w+)|[{}]")


def declared_rust_tests(text, prefix=""):
    """(tests, loose) of one Rust file: [(`module::path::name`, mode)], the name nextest reports. `prefix` is the
    file's own module path. A macro may write tests the scan cannot see, so a file that defines one is loose."""
    masked = RUST_NOISE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    tests, scopes, depth, pending = [], [], 0, False
    for token in RUST_TOKEN_RE.finditer(masked):
        word = token.group(0)
        if word == "#[test]":
            pending = True
        elif word == "}":
            depth -= 1
            if scopes and scopes[-1][0] == depth:
                scopes.pop()
        elif word == "{":
            depth += 1
        elif token.group(1):
            scopes.append((depth, token.group(1)))
            depth += 1
        elif pending:
            path = ([prefix] if prefix else []) + [name for _, name in scopes] + [token.group(2)]
            tests.append(("::".join(path), None))
            pending = False
    return tests, "macro_rules!" in masked


def rust_binary(path, root):
    """(binary id, module prefix) of a test file by its place in its crate, as nextest names them; None for a file no
    test binary is known by (a binary target, whose id holds a slash, and the helpers of an integration test)."""
    folder = path.parent
    while folder != root and not (folder / "Cargo.toml").is_file():
        folder = folder.parent
    manifest = folder / "Cargo.toml"
    if not manifest.is_file():
        return None
    found = re.search(r'^name\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"), re.M)
    crate = found.group(1) if found else folder.name
    parts = path.relative_to(folder).with_suffix("").parts
    if parts[0] == "tests":
        if len(parts) == 2:
            return f"{crate}::{parts[1]}", ""
        return (f"{crate}::{parts[1]}", "") if parts[2:] == ("main",) else None
    if parts[0] != "src" or parts[1:] == ("main",) or parts[1] == "bin":
        return None
    modules = [p for p in parts[1:] if p not in ("lib", "mod")]
    return crate, "::".join(modules)


class RustCatalog(DockerCatalog):
    """What a Rust workspace declares, read from its sources. A proof is `<binary id>/<module path::test>`, nextest's
    own suite and case names: `my-core/face::tests::blink`; an integration test `my-core::parity/outer`."""

    def __init__(self, root, cfg):
        self.cfg = cfg
        self.files, self.by_stem, self.empty_runners = [], {}, []
        for runner, spec in cfg.runners.items():
            seen, count = set(), 0
            for folder, patterns in spec["tests"]:
                for path in walk_files_pruned(root / folder, patterns):
                    place = rust_binary(path, root)
                    if path in seen or place is None:
                        continue
                    seen.add(path)
                    tests, loose = declared_rust_tests(path.read_text(encoding="utf-8", errors="replace"), place[1])
                    record = {"runner": runner, "stem": place[0], "name": path.name,
                              "path": str(path.relative_to(root)), "tests": tests, "loose": loose}
                    self.files.append(record)
                    self.by_stem.setdefault(place[0], []).append(record)
                    count += 1
            if not count:
                self.empty_runners.append(runner)

    def duplicate_displays(self):
        return {}   # every file of a crate shares its binary

    def head(self, head):
        records = self.by_stem.get(head)
        return (records[0]["runner"], head) if records else None


# ---------------------------------------------------------------- JUnit XML (Vitest, Playwright, nextest)

TEST_FILE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def stem_of(classname):
    """The test file stem of a `classname`: both reporters write the file path there (Vitest relative to its root,
    Playwright to its testDir)."""
    base = (classname or "").replace("\\", "/").rsplit("/", 1)[-1]
    for extension in TEST_FILE_EXTENSIONS:
        if base.endswith(extension):
            return base[:-len(extension)]
    return base


def js_regex_escape(text):
    """The text as a JavaScript regular expression that matches it literally (only characters every flag accepts
    escaped)."""
    return re.sub(r"[\\^$.*+?()\[\]{}|/]", lambda m: "\\" + m.group(0), text)


def combine_results(results):
    """One result for a test reported more than once: Failed if any failed, Skipped only if all were skipped."""
    return "Failed" if "Failed" in results else "Skipped" if all(r == "Skipped" for r in results) else "Passed"


def merge_bundle_entries(old, new):
    """One case read from two result bundles of one --reuse: Failed if it failed in either, else the result that is not
    Passed (a Skipped case proves nothing), else Passed; the entry keeps the later date and run."""
    results = (old["result"], new["result"])
    result = "Failed" if "Failed" in results else next((r for r in results if r != "Passed"), "Passed")
    later = new if (new.get("when") or "") >= (old.get("when") or "") else old
    return {**old, **later, "result": result}


def parse_junit(path, runner, when, run_id):
    """One JUnit XML file -> (by_path, runnable, counts) keyed `stem/full title`. A case fails on a <failure> or an
    <error>, is skipped on <skipped>, passes otherwise; the same key twice (a Playwright project per browser) keeps the
    worst result."""
    root = ET.parse(str(path)).getroot()
    hits = {}
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        if not name:
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            result = "Failed"
        elif case.find("skipped") is not None:
            result = "Skipped"
        else:
            result = "Passed"
        hits.setdefault(f"{stem_of(case.get('classname'))}/{norm_title(name)}", []).append(result)
    by_path, runnable, counts = {}, {}, {"Passed": 0, "Failed": 0, "Skipped": 0}
    for key, results in hits.items():
        outcome = combine_results(results)
        by_path[key] = {"result": outcome, "when": when, "run_id": run_id}
        if runner:
            runnable[key] = f"{runner}/{RUNNER_CLASS}"
        counts[outcome] += 1
    return by_path, runnable, counts


# ---------------------------------------------------------------- the engine


class Engine:
    def __init__(self, args, runtime):
        self.args = args
        self.rt = runtime
        self.root = pathlib.Path(args.project).resolve()
        self.doc_path, self.lang = self.find_map()
        self.state_path = self.root / "docs" / ".flows-state.json"
        self.cfg = Config(self.root, args)
        now = runtime.now()
        if args.today:
            try:
                self.today = datetime.date.fromisoformat(args.today)
            except ValueError:
                raise Stop(2, f"--today must be YYYY-MM-DD, not {args.today!r}")
        else:
            self.today = now.date()
        self.clock = datetime.datetime.combine(self.today, now.time())
        self.devices_json = None
        self.ran_whole = set()      # classes that ran whole in this run (a missing case there is a real red)
        self._indexes = {}

    # -------------------------------------------------------- input

    def find_map(self):
        """(path of the map, its Language). The map is docs/FLOWS.md, else docs/FLUXOS.md when only that exists.
        Its language is read from the file; a file with no mark takes --language, then English; a --language that
        contradicts the marks of the file is refused."""
        docs = self.root / "docs"
        found = next((docs / lang.map_name for lang in LANGUAGES.values() if (docs / lang.map_name).exists()), None)
        asked = self.args.language
        if found is None:
            path = docs / LANGUAGES[asked or DEFAULT_LANGUAGE].map_name
            return path, LANGUAGES[asked or DEFAULT_LANGUAGE]
        try:
            detected = detect_language(found.read_text(encoding="utf-8"), found.name)
        except (OSError, UnicodeDecodeError) as error:
            raise Stop(2, f"flow map unreadable: {found} ({error})")
        if asked and detected and asked != detected:
            raise Stop(2, f"{found.name} is written in {detected}; --language {asked} contradicts it")
        return found, LANGUAGES[detected or asked or DEFAULT_LANGUAGE]

    def read_doc(self):
        if not self.doc_path.exists():
            raise Stop(2, f"flow map not found: {self.doc_path} (--init writes a starter map)")
        self.text = self.doc_path.read_text(encoding="utf-8")
        self.flows = []
        seen, dupes, malformed = set(), [], []
        for match in ROW_RE.finditer(self.text):
            fid = match.group("id")
            if not FLOW_ID_RE.fullmatch(fid):
                malformed.append(fid)
                continue
            # empty cells stay: dropping an empty Proof cell shifted the Status word into its place
            cells = [c.strip() for c in match.group("rest").split("|")]
            if fid in seen:
                dupes.append(fid)
            seen.add(fid)
            self.flows.append((fid, cells, match))
        if malformed:
            raise Stop(2, "malformed flow ID (letters, a dash and digits, like F-ACC-01): " + ", ".join(malformed))
        if dupes:
            raise Stop(2, "duplicate flow ID, the map would lose a row: " + ", ".join(sorted(set(dupes))))
        if not self.flows:
            raise Stop(2, "no flow row found in the map")
        self.ids = [fid for fid, _, _ in self.flows]
        self.cells = {fid: cells for fid, cells, _ in self.flows}
        self.entry = {fid: (cells[1] if len(cells) > 1 else "") for fid, cells, _ in self.flows}
        self.proofs = {fid: (split_proofs(cells[2]) if len(cells) > 2 else []) for fid, cells, _ in self.flows}
        if self.cfg.platform == DOCKER:
            # Playwright prints a title path with a guillemet (U+203A), Vitest with ` > `: a proof may use either
            self.proofs = {fid: [p if is_free(p) else norm_title(p) for p in proofs]
                           for fid, proofs in self.proofs.items()}

    def load_state(self):
        if not self.state_path.exists():
            return {"version": STATE_VERSION, "flows": {}, "raw": {"by_path": {}, "by_name": {}, "runnable": {}},
                    "run": None}
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise Stop(2, f"state unreadable, left untouched: {self.state_path} ({error})")
        if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
            version = state.get("version") if isinstance(state, dict) else None
            raise Stop(2, f"state is version {version!r}, this engine reads version {STATE_VERSION}; left untouched")
        state.setdefault("flows", {})
        for rec in state["flows"].values():
            if isinstance(rec, dict) and "status" in rec:
                rec["status"] = canon(rec["status"])   # either language: the engine works on the canonical word
        raw = state.setdefault("raw", {})
        for key in ("by_path", "by_name", "runnable"):
            raw.setdefault(key, {})
        state.setdefault("run", None)
        return state

    def save_state(self, state):
        """The state on disk holds each status as the map's language writes it (the in-memory one is canonical)."""
        stored = dict(state, flows={fid: dict(rec, status=self.lang.stored.get(canon(rec.get("status")), rec["status"]))
                                    if isinstance(rec, dict) and "status" in rec else rec
                                    for fid, rec in state["flows"].items()})
        write_atomically(self.state_path, json.dumps(stored, ensure_ascii=False, indent=1, sort_keys=True))

    # -------------------------------------------------------- sources

    def build_source_index(self):
        self.source_index, self.clashes = {}, {}
        docker = self.cfg.platform == DOCKER
        for folder, patterns in self.cfg.sources:
            found = set()
            if docker:
                found.update(walk_files_pruned(self.root / folder, patterns))
            else:
                for pattern in patterns:
                    found.update(walk_files(self.root / folder, pattern))
            for path in sorted(found):
                if docker:
                    for key in self.index_keys(path):
                        first = self.source_index.setdefault(key, path)
                        if first != path:
                            self.clashes.setdefault(key, [first]).append(path)
                    continue
                first = self.source_index.get(path.name)
                if first is None:
                    self.source_index[path.name] = path
                elif first != path:
                    self.clashes.setdefault(path.name, [first]).append(path)

    def index_keys(self, path):
        """The names an Entry cell may cite a docker project's file by: its basename and every trailing part of its
        path (`providers/page.tsx`, `apps/web/.../page.tsx`), so the many `page.tsx` of a Next.js app can be told
        apart."""
        parts = path.relative_to(self.root).parts
        return ["/".join(parts[k:]) for k in range(len(parts) - 1, -1, -1)]

    def cited_names(self, entry):
        return sorted(set(self.cfg.source_re.findall(entry)))

    def fingerprint(self, entry):
        """sha1 of the cited files' bytes, sorted by name; (None, missing names) when one is not on disk."""
        names = self.cited_names(entry)
        if not names:
            return None, []
        hasher, missing = hashlib.sha1(), []
        for name in names:
            path = self.source_index.get(name)
            if path is None or not path.exists():
                missing.append(name)
                continue
            hasher.update(path.read_bytes())
        if missing:
            return None, missing
        return hasher.hexdigest(), []

    def warn_index(self):
        cited = set()
        for fid in self.ids:
            cited.update(self.cited_names(self.entry[fid]))
        for name in sorted(self.clashes):
            if name in cited:
                paths = [str(p.relative_to(self.root)) for p in self.clashes[name]]
                print(f"source clash: {name} is in {', '.join(paths)}; the first listed folder wins", flush=True)
        for runner in getattr(self.catalog, "empty_runners", []):
            print(f"runner {runner}: no test file found in its tests folders; check them in scripts/flows.json",
                  flush=True)
        for name, hits in sorted(self.catalog.duplicate_displays().items()):
            where = ", ".join(f"{t}/{c}" for t, c in hits)
            print(f"display name '{name}' is declared in more than one target or type ({where}); "
                  f"proofs citing it do not resolve", flush=True)

    # -------------------------------------------------------- results and lookup

    def raw_changed(self):
        self._indexes = {}

    def index(self, raw):
        key = id(raw["by_path"])
        found = self._indexes.get(key)
        if found is None:
            names, idents = {}, {}
            for path, entry in raw["by_path"].items():
                name = path.split("/", 1)[1] if "/" in path else path
                names.setdefault(name, []).append(path)
                ident = entry.get("identifier") if isinstance(entry, dict) else None
                if ident:
                    idents.setdefault(ident, []).append(path)
            found = (names, idents)
            self._indexes[key] = found
        return found

    def key_type(self, raw, key):
        entry = raw["by_path"].get(key) or {}
        ident = entry.get("identifier")
        if ident:
            return ident.split("/")[0]
        cls = raw.get("runnable", {}).get(key)
        return cls.split("/", 1)[1] if cls and "/" in cls else None

    def lookup(self, proof, raw):
        """(entry, key) for a proof: the exact key, else a unique case whose suite or Type is the proof's head
        (or, for a bare name, a unique case of that name). Ambiguity gives ({"result": "AMBIGUOUS"}, None)."""
        by_path = raw["by_path"]
        if proof in by_path:
            return by_path[proof], proof
        names, idents = self.index(raw)
        if "/" in proof:
            head, name = proof.split("/", 1)
            found = [k for k in names.get(name, []) if k.split("/", 1)[0] == head or self.key_type(raw, k) == head]
            found += [k for k in idents.get(proof, []) if k not in found]
        else:
            found = list(names.get(proof, []))
        if len(found) == 1:
            return by_path[found[0]], found[0]
        if len(found) > 1:
            return {"result": "AMBIGUOUS"}, None
        return None, None

    def resolve(self, proof, raw):
        """The `Target/Class` a proof runs in, or None when nothing says where it lives."""
        cls = raw["runnable"].get(proof)
        if cls:
            return cls
        if "/" in proof:
            head = proof.split("/", 1)[0]
            if head in self.cfg.swift_test_targets:
                return f"{head}/package"
            hit = self.catalog.head(head)
            if hit:
                return self.catalog.class_for(*hit)
        entry, key = self.lookup(proof, raw)
        if key:
            return raw["runnable"].get(key)
        return None

    def class_parts(self, cls):
        target, _, name = cls.partition("/")
        return target, name

    def is_ui(self, cls):
        return self.class_parts(cls)[0] in self.cfg.ui_names

    def merge(self, state, by_path, runnable):
        state["raw"]["by_path"].update(by_path)
        state["raw"]["runnable"].update(runnable)
        self.rebuild_by_name(state)

    def rebuild_by_name(self, state):
        """by_name is derived from by_path, so a name two suites share stays ambiguous across bundles."""
        by_name = {}
        for key, entry in state["raw"]["by_path"].items():
            name = key.split("/", 1)[1] if "/" in key else key
            by_name[name] = {"result": "AMBIGUOUS"} if name in by_name else dict(entry)
        state["raw"]["by_name"] = by_name
        self.raw_changed()

    def mark_absent(self, state, scope, cls, run_id):
        """A class that ran narrowed, to the cases the flows cite: a cited proof whose stored result this run did not
        produce was selected by name and is absent from the run's report (a file that failed to load, a crashed
        class). Its old result is replaced by an Absent marker, so it is never read as a pass; the next run that
        reports the test replaces the marker. A proof with no stored result stays a lookup miss."""
        raw, marked = state["raw"], []
        for fid in scope:
            for proof in self.proofs[fid]:
                if is_free(proof) or self.resolve(proof, raw) != cls:
                    continue
                entry, key = self.lookup(proof, raw)
                if key and entry.get("run_id") != run_id and key not in marked:
                    raw["by_path"][key] = dict(entry, result=ABSENT, when=self.today.isoformat(), run_id=run_id)
                    marked.append(key)
        if marked:
            self.rebuild_by_name(state)
            print(f"  {cls}: {len(marked)} cited test(s) selected by name and absent from the run's report: "
                  + ", ".join(sorted(marked)), flush=True)

    def prune(self, state, cls, produced_keys):
        """A class that ran whole: its stored results that the new bundle did not produce are history."""
        raw = state["raw"]
        gone = [k for k, c in raw["runnable"].items() if c == cls and k not in produced_keys]
        for key in gone:
            raw["by_path"].pop(key, None)
            raw["runnable"].pop(key, None)
        if gone:
            print(f"  {cls}: {len(gone)} stored result(s) its whole run no longer produced: " + ", ".join(sorted(gone)),
                  flush=True)

    # -------------------------------------------------------- the verdict

    def judge(self, proofs, raw):
        """(status, note, validated_when). The date is set only for OK: the latest date of its proofs."""
        if not proofs:
            return MISSING, "no proof declared", None
        device = [p for p in proofs if p.startswith("device:")]
        if device:
            if len(device) != len(proofs):
                return BROKEN, "device: beside another proof; keep one of the two", None
            reason = device[0][len("device:"):].strip()
            if len(reason) < DEVICE_REASON_MIN:
                return BROKEN, "device: reason not specific enough (under 12 characters)", None
            return DEVICE, reason, None
        if self.cfg.refuse_mixed:
            kinds = {self.is_ui(c) for c in (self.resolve(p, raw) for p in proofs if not is_free(p)) if c}
            if len(kinds) == 2:
                return BROKEN, "flow mixes a UI proof with a unit proof; split it in two flows", None
        notes, whens, worst = [], [], OK

        def lower(status):
            nonlocal worst
            if status == BROKEN or worst == OK:
                worst = status

        for proof in proofs:
            if proof.startswith("print:"):
                relative = proof[len("print:"):].strip()
                path = self.root / relative
                if path.exists():
                    whens.append(date_of_mtime(path).isoformat())
                elif self.git_tracks(relative):
                    lower(BROKEN)
                    notes.append(f"capture deleted: {relative}")
                else:
                    lower(MISSING)
                    notes.append(f"capture not made yet: {relative}")
                continue
            if proof.startswith("manual:"):
                parts = proof.split(":", 2)
                try:
                    day = datetime.date.fromisoformat(parts[1])
                except (IndexError, ValueError):
                    lower(BROKEN)
                    notes.append(f"invalid date in {proof}")
                    continue
                age = (self.today - day).days
                if age < 0:
                    lower(BROKEN)
                    notes.append(f"manual check dated in the future: {proof}")
                elif age > MANUAL_TTL_DAYS:
                    lower(BROKEN)
                    notes.append(f"manual check expired ({age} days)")
                else:
                    whens.append(day.isoformat())
                continue
            entry, _ = self.lookup(proof, raw)
            if entry is None:
                cls = self.resolve(proof, raw)
                if cls and cls in self.ran_whole:
                    lower(BROKEN)
                    notes.append(f"test not in its class's run: {proof}")
                else:
                    lower(MISSING)
                    notes.append(f"lookup: no result and no test run for {proof} yet")
                continue
            result = entry.get("result")
            if result == "AMBIGUOUS":
                lower(BROKEN)
                notes.append(f"ambiguous name, use Suite/Case: {proof}")
            elif self.catalog.proof_gone(proof):
                lower(BROKEN)
                notes.append(f"test no longer in the source: {proof}")
            elif result == ABSENT:
                lower(BROKEN)
                notes.append(f"selected by name, absent from the run's report: {proof}")
            elif result == "Skipped":
                lower(BROKEN)
                notes.append(f"skipped, proved nothing: {proof}")
            elif result != "Passed":
                lower(BROKEN)
                notes.append(f"failed: {proof}")
            else:
                whens.append(entry.get("when") or self.today.isoformat())
        when = max(whens) if (worst == OK and whens) else None
        return worst, "; ".join(notes), when

    def git_tracks(self, relative):
        out = self.rt.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=self.root)
        return out.returncode == 0 and bool((out.stdout or "").strip())

    def update_flow(self, state, fid, proofs, status, note, when, run_id):
        rec = state["flows"].setdefault(fid, {"source_missing": []})
        rec["status"] = status
        rec["proofs"] = list(proofs)
        rec["note"] = note
        if status != OK:
            return  # validated_at, fingerprint and run_id stay: the old date is the reference
        fp, missing = self.fingerprint(self.entry[fid])
        if missing:
            # A cited file that is not on disk cannot be fingerprinted: the flow waits for its map row.
            rec["status"] = REVALIDATE
            rec["note"] = "cited file not found: " + ", ".join(missing)
            rec["source_missing"] = missing
            return
        rec["validated_at"] = when
        rec["source_fingerprint"] = fp
        rec["source_missing"] = []
        rec["run_id"] = run_id

    # -------------------------------------------------------- passes run on every invocation

    def free_older_than_change(self, rec, proofs, entry):
        """Cited files changed after the oldest screenshot or manual check of a print:/manual: flow."""
        stored = rec.get("source_fingerprint")
        fp, missing = self.fingerprint(entry)
        if missing or fp is None or stored is None or fp == stored:
            return []
        taken = []
        for proof in proofs:
            if proof.startswith("print:"):
                path = self.root / proof[len("print:"):].strip()
                if path.exists():
                    taken.append(os.path.getmtime(str(path)))
            elif proof.startswith("manual:"):
                try:
                    day = datetime.date.fromisoformat(proof.split(":", 2)[1])
                except (IndexError, ValueError):
                    continue
                taken.append(datetime.datetime.combine(day, datetime.time.min).timestamp())
        if not taken:
            return []
        oldest = min(taken)
        return [n for n in self.cited_names(entry)
                if n in self.source_index and os.path.getmtime(str(self.source_index[n])) > oldest]

    def refresh_free_flows(self, state):
        """Flows that need no test run are judged every invocation: it costs nothing."""
        for fid in self.ids:
            proofs = self.proofs[fid]
            if not needs_no_run(proofs):
                continue
            status, note, when = self.judge(proofs, state["raw"])
            if status == OK:
                older = self.free_older_than_change(state["flows"].get(fid, {}), proofs, self.entry[fid])
                if older:
                    rec = state["flows"].setdefault(fid, {"source_missing": []})
                    rec.update(status=REVALIDATE, proofs=list(proofs),
                               note="proof older than the change in " + ", ".join(older))
                    continue
            self.update_flow(state, fid, proofs, status, note, when, "no-run")

    def invalidate_pass(self, state):
        """Every validated flow of the map whose cited source moved drops to revalidate, naming the file."""
        dropped = []
        for fid in self.ids:
            rec = state["flows"].get(fid)
            if not rec or canon(rec.get("status")) != OK:
                continue
            fp, missing = self.fingerprint(self.entry[fid])
            if missing:
                rec.update(status=REVALIDATE, note="cited file not found: " + ", ".join(missing), source_missing=missing)
            elif fp != rec.get("source_fingerprint"):
                rec.update(status=REVALIDATE, note="source changed: " + ", ".join(self.cited_names(self.entry[fid])))
            else:
                continue
            dropped.append((fid, rec["note"]))
        if dropped:
            print(f"{len(dropped)} validated flow(s) dropped to {self.label(REVALIDATE)}:", flush=True)
            for fid, note in dropped:
                print(f"  {fid}: {note}", flush=True)

    def gone_pass(self, state):
        """A test the source no longer declares breaks its flow, offline too, so its old Passed proves nothing."""
        broken = []
        for fid in self.ids:
            rec = state["flows"].get(fid)
            if not rec or canon(rec.get("status")) not in (OK, REVALIDATE):
                continue
            gone = []
            for proof in self.proofs[fid]:
                if is_free(proof):
                    continue
                entry, _ = self.lookup(proof, state["raw"])
                if entry is not None and entry.get("result") != "AMBIGUOUS" and self.catalog.proof_gone(proof):
                    gone.append(proof)
            if gone:
                rec.update(status=BROKEN, proofs=list(self.proofs[fid]),
                           note="; ".join(f"test no longer in the source: {p}" for p in gone))
                broken.append(fid)
        if broken:
            print(f"{len(broken)} flow(s) cite a test the source no longer declares: " + ", ".join(broken), flush=True)

    def proofs_changed_pass(self, state):
        """A validated flow whose Proof cell changed is judged again; a new proof counts only when its stored
        result comes from the flow's own validation run or a later one, otherwise the flow goes to revalidate."""
        changed = []
        for fid in self.ids:
            rec = state["flows"].get(fid)
            if not rec or canon(rec.get("status")) != OK:
                continue
            proofs, stored = self.proofs[fid], rec.get("proofs") or []
            automated = [p for p in proofs if not is_free(p)]
            if proofs == stored or not automated:
                continue
            change = [f"+{p}" for p in proofs if p not in stored] + [f"-{p}" for p in stored if p not in proofs]
            validated = run_moment(rec.get("run_id"))
            if validated is None and rec.get("validated_at"):
                validated = datetime.datetime.combine(datetime.date.fromisoformat(rec["validated_at"]),
                                                      datetime.time.min) + datetime.timedelta(days=1)
            added = [p for p in automated if p not in stored]
            runs = [((self.lookup(p, state["raw"])[0]) or {}).get("run_id") for p in added]
            moments = [run_moment(r) for r in runs]
            if added and (validated is None or any(m is None or m < validated for m in moments)):
                rec.update(status=REVALIDATE, proofs=list(proofs), note="proof changed in the map: " + ", ".join(change))
                changed.append((fid, REVALIDATE, change))
                continue
            status, note, when = self.judge(proofs, state["raw"])
            if status == OK:
                run_id = max(zip(moments, runs))[1] if added else rec.get("run_id")
                self.update_flow(state, fid, proofs, status, note, when, run_id)
            else:
                rec.update(status=status, proofs=list(proofs), note=note)
            changed.append((fid, canon(state["flows"][fid]["status"]), change))
        if changed:
            print(f"proof changed in the map: {len(changed)} flow(s) judged again:", flush=True)
            for fid, status, change in changed:
                print(f"  {fid} -> {self.label(status)}: {', '.join(change)}", flush=True)

    def invalidate_classes(self, state, spec):
        classes = set()
        for part in spec.split(","):
            name = part.strip()
            if not name:
                continue
            if "/" in name:
                classes.add(name)
                continue
            hit = self.catalog.head(name)
            if hit:
                classes.add(self.catalog.class_for(*hit))
            elif self.cfg.platform == DOCKER:
                classes.add(f"{name}/{RUNNER_CLASS}")   # a runner's name; any other word matches nothing
            else:
                target = (self.cfg.ui_targets if name.endswith("UITests") else self.cfg.unit_targets) or [("?", "")]
                classes.add(f"{target[0][0]}/{name}")
        demoted, kept = [], []
        for fid in self.ids:
            rec = state["flows"].get(fid)
            if not rec or canon(rec.get("status")) != OK:
                continue
            automated = [p for p in self.proofs[fid] if not is_free(p)]
            if not automated:
                continue
            if all(self.resolve(p, state["raw"]) in classes for p in automated):
                rec.update(status=REVALIDATE, note="class invalidated: " + ", ".join(sorted(classes)))
                demoted.append(fid)
            else:
                kept.append(fid)
        print(f"--invalidate-classes: {len(demoted)} flow(s) dropped to {self.label(REVALIDATE)}"
              + (": " + ", ".join(demoted) if demoted else ""), flush=True)
        if kept:
            print(f"--invalidate-classes: {len(kept)} validated flow(s) with a proof outside the list kept their status",
                  flush=True)

    # -------------------------------------------------------- rendering and reports

    def render(self, state):
        out, cursor = [], 0
        counts = {OK: 0, MISSING: 0, DEVICE: 0, BROKEN: 0, REVALIDATE: 0}
        rows = {}
        for fid, cells, match in self.flows:
            rec = state["flows"].get(fid, {})
            status = canon(rec.get("status", MISSING))
            if status not in counts:
                status = MISSING
            counts[status] += 1
            when = rec.get("validated_at") or NO_DATE
            new_cells = (list(cells[:3]) + [""] * 3)[:3] + [self.label(status), when]
            rows[fid] = (self.label(status), when)
            out.append(self.text[cursor:match.start()])
            out.append(f"| {fid} |" + "|".join(f" {c} " for c in new_cells) + "|")
            cursor = match.end()
        out.append(self.text[cursor:])
        text = "".join(out)
        total = len(self.flows)
        pct = round(100 * counts[OK] / total) if total else 0
        lang = self.lang
        block = "\n".join([
            lang.start,
            "| Total | " + " | ".join(lang.board) + " |",
            "|---|---|---|---|---|---|",
            f"| **{total}** | **{counts[OK]}** ({pct}%) | {counts[REVALIDATE]} | {counts[MISSING]} | "
            f"{counts[DEVICE]} | {counts[BROKEN]} |",
            "",
            lang.footer.format(date=self.today.isoformat()),
            lang.end,
        ])
        if lang.start in text and lang.end in text:
            head, rest = text.split(lang.start, 1)
            _, tail = rest.split(lang.end, 1)
            text = head + block + tail
        return text, counts, rows

    def write_doc(self, state):
        text, counts, _ = self.render(state)
        write_atomically(self.doc_path, text)
        return counts

    def label(self, status):
        """The word the map shows for a canonical status."""
        return self.lang.labels.get(status, status)

    def print_summary(self, counts):
        parts = ", ".join(f"{counts[status]} {self.label(status)}" for status in (OK, REVALIDATE, MISSING, DEVICE, BROKEN))
        print(f"flows: {sum(counts.values())} total, {parts}", flush=True)

    def claimed(self, raw):
        claimed = set()
        names, _ = self.index(raw)
        for fid in self.ids:
            for proof in self.proofs[fid]:
                if is_free(proof):
                    continue
                claimed.add(proof)
                _, key = self.lookup(proof, raw)
                if key:
                    claimed.add(key)
                if "/" not in proof:
                    claimed.update(names.get(proof, []))
        return claimed

    def print_listings(self, state):
        raw = state["raw"]
        claimed = self.claimed(raw)
        passing = sorted(k for k, e in raw["by_path"].items()
                         if e.get("result") == "Passed" and k not in claimed and not self.catalog.proof_gone(k))
        print(f"uncited passing tests, no flow cites them ({len(passing)}):", flush=True)
        for key in passing:
            print(f"  {key}", flush=True)
        names, idents = self.index(raw)
        never = set()
        if self.cfg.platform == DOCKER:
            never = {key for _, key in self.catalog.declared() if key not in claimed and key not in raw["by_path"]}
        for target, type_name, method, display in ([] if self.cfg.platform == DOCKER else self.catalog.cases):
            suite = self.catalog.type_display.get(type_name) or type_name
            shown = f"{suite}/{display or method + '()'}"
            aliases = {f"{type_name}/{method}()", shown}
            if target in self.cfg.swift_test_targets:
                aliases |= {f"{target}/{type_name}/{method}()", f"{target}/{type_name}/{method}"}
            if aliases & claimed or (display and display in claimed) or f"{method}()" in claimed:
                continue
            if any(a in raw["by_path"] for a in aliases) or f"{type_name}/{method}()" in idents:
                continue
            # backstop: a result whose case part is this case's display name or `method()` says it ran, whatever
            # suite the report put it under
            if (display and display in names) or f"{method}()" in names:
                continue
            never.add(shown)
        print(f"uncited tests never run, declared in the source ({len(never)}):", flush=True)
        for key in sorted(never):
            print(f"  {key}", flush=True)
        ghosts = [fid for fid in state["flows"] if fid not in self.cells]
        if ghosts:
            print(f"state entries without a map row: {len(ghosts)} ({', '.join(sorted(ghosts))}); kept untouched",
                  flush=True)

    def prune_state(self, state):
        """Removes the state entries of flows no longer in the map;
        the map, every other entry and the raw results stay as they are. Nothing to remove writes nothing."""
        ghosts = sorted(fid for fid in state["flows"] if fid not in self.cells)
        if not ghosts:
            print("state entries without a map row: 0; nothing written", flush=True)
            return 0
        for fid in ghosts:
            del state["flows"][fid]
            print(f"  removed {fid}", flush=True)
        self.save_state(state)
        print(f"state entries without a map row removed: {len(ghosts)}; the map and every other entry are unchanged",
              flush=True)
        return 0

    def print_open_flows(self, state):
        for fid in self.ids:
            rec = state["flows"].get(fid, {})
            status = canon(rec.get("status", MISSING))
            if status != OK:
                note = rec.get("note") or ""
                print(f"  {self.label(status)}  {fid}  {self.cells[fid][0] if self.cells[fid] else ''}"
                      + (f"  ({note})" if note else ""), flush=True)

    # -------------------------------------------------------- devices

    def simctl_devices(self):
        if self.devices_json is None:
            out = self.rt.run(["xcrun", "simctl", "list", "devices", "-j"])
            try:
                self.devices_json = json.loads(out.stdout) if out.returncode == 0 else {}
            except ValueError:
                self.devices_json = {}
        for group in (self.devices_json.get("devices") or {}).values():
            for entry in group:
                yield entry

    def destination_for(self, device):
        """A device name resolved to its UDID through simctl (Xcode 27 matches no `name=`); a UDID passes through."""
        if UDID_RE.fullmatch(device):
            return f"platform=iOS Simulator,id={device}"
        for entry in self.simctl_devices():
            if entry.get("name") == device and entry.get("isAvailable", True):
                return f"platform=iOS Simulator,id={entry['udid']}"
        raise Stop(2, f"device {device!r} not found by simctl; pass a device name or a UDID")

    def family_of_device(self, device):
        for entry in self.simctl_devices():
            if device in (entry.get("name"), entry.get("udid")):
                return "ipad" if "iPad" in (entry.get("deviceTypeIdentifier") or "") else "iphone"
        return self.family(self.cfg.device_kind)

    @staticmethod
    def family(kind):
        return "ipad" if kind == "ipad" else "iphone"

    def kind_of(self, cls):
        if self.cfg.platform in ("macos", DOCKER):
            return None
        return self.cfg.device_classes.get(self.class_parts(cls)[1], self.cfg.device_kind)

    def destination(self, kind, cache):
        """The -destination of a batch: this Mac for a macOS project, --device when given, else the simulator the
        configuration's "devices" names for the group's kind (resolved once per run)."""
        if self.cfg.platform == "macos":
            return f"platform=macOS,arch={mac_arch(getattr(self.rt, 'machine', lambda: 'arm64')())}"
        if self.args.device:
            return self.destination_for(self.args.device)
        if kind not in cache:
            device = self.cfg.devices.get(kind)
            if not device:
                raise Stop(2, f"no simulator for the {kind} device kind: add \"devices\": {{\"{kind}\": "
                              f"\"<simulator name or UDID>\"}} to scripts/flows.json, or pass --device")
            cache[kind] = self.destination_for(device)
        return cache[kind]

    def sdk_flags(self):
        """An iOS run names the simulator SDK; a Mac run has none."""
        return [] if self.cfg.platform == "macos" else ["-sdk", "iphonesimulator"]

    # -------------------------------------------------------- planning

    def classes_for(self, scope, raw, include_ui):
        wanted, unresolved = set(), []
        for fid in scope:
            proofs = self.proofs[fid]
            if needs_no_run(proofs):
                continue
            for proof in proofs:
                cls = self.resolve(proof, raw)
                if not cls:
                    unresolved.append((fid, proof))
                elif include_ui or not self.is_ui(cls):
                    wanted.add(cls)
        return wanted, unresolved

    def cases_for(self, scope, raw):
        """UI classes narrowed to the cases the flows cite; a case not knowable from the proof runs the whole class."""
        if self.cfg.platform == DOCKER:
            return self.runner_cases(scope, raw)
        cases, whole = {}, set()
        for fid in scope:
            if needs_no_run(self.proofs[fid]):
                continue
            for proof in self.proofs[fid]:
                cls = self.resolve(proof, raw)
                if not cls or not self.is_ui(cls):
                    continue
                type_name = self.class_parts(cls)[1]
                method = None
                if CODE_PROOF_RE.fullmatch(proof) and proof.split("/", 1)[0] == type_name:
                    method = proof.split("/", 1)[1][:-2]
                else:
                    entry, _ = self.lookup(proof, raw)
                    ident = (entry or {}).get("identifier") or ""
                    if ident.count("/") == 1 and ident.split("/")[0] == type_name:
                        method = ident.split("/")[1]
                        method = method[:-2] if method.endswith("()") else method
                if method:
                    cases.setdefault(cls, set()).add(f"{cls}/{method}")
                else:
                    whole.add(cls)
        return {cls: sorted(names) for cls, names in cases.items() if cls not in whole}

    def runner_cases(self, scope, raw):
        """A docker runner that can narrow (it has `only_args`) runs only the tests the flows in scope cite: {class:
        [proof]}. A runner that cannot, or a proof whose test file the scan does not know when `{files}` is needed,
        runs whole."""
        cases, whole = {}, set()
        for fid in scope:
            if needs_no_run(self.proofs[fid]):
                continue
            for proof in self.proofs[fid]:
                cls = None if is_free(proof) else self.resolve(proof, raw)
                runner = self.cfg.runners.get(self.class_parts(cls)[0]) if cls else None
                if runner is None:
                    continue
                stem = proof.split("/", 1)[0]
                if (not runner["only_args"] or "/" not in proof
                        or ("{files}" in runner["only_args"] and not self.catalog.file_names(runner["name"], stem))):
                    whole.add(cls)
                else:
                    cases.setdefault(cls, set()).add(proof)
        return {cls: sorted(proofs) for cls, proofs in cases.items() if cls not in whole}

    def runner_arguments(self, runner, proofs):
        """What `only_args` becomes for these proofs: `{pattern}` one regular expression matching the cited tests
        (their describe names and test name in order, anything between, so the reporter's separator does not matter),
        `{files}` the basenames of their test files, one argument each."""
        parts = [proof.split("/", 1) for proof in proofs]
        pattern = "|".join(sorted({".*".join(js_regex_escape(part) for part in title.split(TITLE_SEPARATOR))
                                   for _, title in parts}))
        files = sorted({name for stem, _ in parts for name in self.catalog.file_names(runner["name"], stem)})
        arguments = []
        for token in runner["only_args"]:
            if token == "{files}":
                arguments += files
            else:
                arguments.append(token.replace("{pattern}", pattern))
        return arguments

    def needed(self, proofs, raw):
        needed, resolvable = set(), True
        for proof in proofs:
            if is_free(proof):
                continue
            cls = self.resolve(proof, raw)
            if cls:
                needed.add(cls)
            else:
                resolvable = False
        return needed, resolvable

    def ready(self, proofs, raw, done):
        """Judged after a batch only when every class its proofs need ran in this run."""
        if needs_no_run(proofs):
            return True
        needed, resolvable = self.needed(proofs, raw)
        return resolvable and needed <= done

    def ready_final(self, proofs, raw, done):
        """At the end of a completed run an unresolved proof no longer holds a flow back (it is judged a lookup
        miss), but a class this run did not run still does."""
        if needs_no_run(proofs):
            return True
        needed, _ = self.needed(proofs, raw)
        return needed <= done

    def sort_key(self, cls):
        target, name = self.class_parts(cls)
        return (target in self.cfg.ui_names, target, name)

    def changed_scope(self):
        if self.args.since:
            commands = [["git", "diff", "--name-only", self.args.since]]
        else:
            commands = [["git", "diff", "--name-only"], ["git", "diff", "--name-only", "--cached"]]
        commands.append(["git", "ls-files", "--others", "--exclude-standard"])
        paths = set()
        for cmd in commands:
            out = self.rt.run(cmd, cwd=self.root)
            if out.returncode != 0:
                continue
            for line in (out.stdout or "").splitlines():
                line = line.strip()
                if line and any(fnmatch.fnmatch(pathlib.PurePosixPath(line).name, p) for p in self.cfg.patterns):
                    paths.add(line)
        docker = self.cfg.platform == DOCKER
        names = {pathlib.PurePosixPath(p).name for p in paths}
        test_dirs = [pathlib.PurePosixPath(folder) for _, folder, _ in self.cfg.test_dirs()]
        types = set()
        for path in paths:
            pure = pathlib.PurePosixPath(path)
            if docker:
                # a changed test file: its stem is the head of the proofs it holds
                if any(d == pure or d in pure.parents for d in test_dirs) and any(
                        fnmatch.fnmatch(pure.name, pat) for spec in self.cfg.runners.values()
                        for _, pats in spec["tests"] for pat in pats):
                    types.add(pure.stem)
                continue
            if pure.suffix != ".swift" or not any(d == pure or d in pure.parents for d in test_dirs):
                continue
            full = self.root / path
            if full.exists():
                text = full.read_text(encoding="utf-8", errors="replace")
                types.update(name for _, name in TYPE_DECL_RE.findall(text))
        scope = []
        for fid in self.ids:
            if docker:
                # a cited name matches a changed path when it is that path's tail (`page.tsx`, `providers/page.tsx`)
                if any(p == c or p.endswith("/" + c) for c in self.cited_names(self.entry[fid]) for p in paths):
                    scope.append(fid)
                    continue
                if any("/" in p and p.split("/", 1)[0] in types for p in self.proofs[fid] if not is_free(p)):
                    scope.append(fid)
                continue
            if set(self.cited_names(self.entry[fid])) & names:
                scope.append(fid)
                continue
            classes = {self.resolve(p, self.state["raw"]) for p in self.proofs[fid] if not is_free(p)}
            if any(c and self.class_parts(c)[1] in types for c in classes):
                scope.append(fid)
        print(f"changed files: {len(paths)}, flows touched: {len(scope)}", flush=True)
        return scope

    # -------------------------------------------------------- running

    def bundle_path(self, run_id, number, suffix, extension=".xcresult"):
        """A new path per batch, never one that exists: an engine-made name takes the next free counter; a path
        given with --result-path is used as given, and run_batch refuses it when it exists."""
        stamp = self.rt.now().strftime("%H%M%S")
        if self.args.result_path and extension == ".xcresult":
            given = pathlib.Path(self.args.result_path)
            return given.with_name(given.stem + ("" if number == 1 else f"-b{number}") + suffix + given.suffix)
        base = f"flows-{run_id}-b{number}-{stamp}"
        path, counter = self.cfg.results_dir / f"{base}{suffix}{extension}", 1
        while any(p.exists() for p in ([path] if extension == ".xcresult" else xunit_paths(path))):
            counter += 1
            path = self.cfg.results_dir / f"{base}-{counter}{suffix}{extension}"
        return path

    def run_batch(self, batch, kind, number, run_id, cases_by_class, dest_cache):
        """Run one batch; returns (by_path, runnable, ok) per group, for the classes of the batch."""
        if self.cfg.platform == DOCKER:
            return self.run_runners(batch, number, run_id, cases_by_class)
        app = [c for c in batch if self.class_parts(c)[0] not in self.cfg.xcode_packages
               and self.class_parts(c)[0] not in self.cfg.swift_test_targets]
        groups = []
        for pkg in self.cfg.packages:
            members = [c for c in batch if self.class_parts(c)[0] == pkg["target"]]
            if members:
                groups.append((pkg, members))
        if app:
            groups.append((None, app))
        outcomes = []
        for pkg, members in groups:
            if pkg and pkg["runner"] == "swift-test":
                outcomes.append((members,) + self.run_swift_test(pkg, number, run_id))
                continue
            suffix = f"-{pkg['scheme']}" if pkg else ""
            bundle = self.bundle_path(run_id, number, suffix)
            if bundle.exists():
                print(f"refused: the result bundle already exists, it is never overwritten: {bundle}", flush=True)
                outcomes.append((members, {}, {}, False))
                continue
            bundle.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["xcodebuild", "test"]
            if pkg:
                cmd += ["-scheme", pkg["scheme"]]
                derived = pathlib.Path(self.cfg.derived_data) / pkg["scheme"]   # one build folder per project: a subfolder
                cwd = self.root / pkg["dir"]
            else:
                cmd += ["-project", str(self.root / self.cfg.xcodeproj), "-scheme", self.cfg.scheme]
                derived = self.cfg.derived_data
                cwd = self.root
            cmd += self.sdk_flags()
            cmd += ["-destination", self.destination(kind, dest_cache),
                    "-parallel-testing-enabled", "NO",
                    # without it Xcode 27 waits on `simctl diagnose` after the tests and can leave no Info.plist
                    "-collect-test-diagnostics", "never",
                    "-derivedDataPath", str(derived), "-resultBundlePath", str(bundle)]
            for cls in members:
                cases = (cases_by_class or {}).get(cls)
                cmd += [f"-only-testing:{case}" for case in cases] if cases else [f"-only-testing:{cls}"]
            print("$ " + " ".join(cmd), flush=True)
            proc = self.rt.run(cmd, cwd=cwd)
            shown = [l.strip() for l in (proc.stdout or "").splitlines() if "Test run with" in l or "** TEST" in l]
            for line in shown:
                print("  " + line, flush=True)
            if not bundle.exists():
                tail = [l for l in (proc.stderr or "").splitlines() if l.strip()][-12:]
                print(f"  the batch wrote no result bundle (exit {proc.returncode}):", flush=True)
                for line in tail or ["(no error output)"]:
                    print("    " + line.strip(), flush=True)
                outcomes.append((members, {}, {}, False))
                continue
            out = self.rt.run(["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", str(bundle),
                               "--compact"])
            try:
                data = json.loads(out.stdout) if out.returncode == 0 else None
            except ValueError:
                data = None
            if data is None:
                print(f"  xcresulttool could not read {bundle}: {(out.stderr or '').strip()[:300]}", flush=True)
                outcomes.append((members, {}, {}, False))
                continue
            by_path, runnable = parse_xcresult(data, self.today.isoformat(), run_id)
            outcomes.append((members, by_path, runnable, True))
        return outcomes

    def archive_path(self, run_id, number):
        """A new folder per batch under the results folder, never one that exists."""
        base = f"flows-{run_id}-b{number}-{self.rt.now().strftime('%H%M%S')}"
        path, counter = self.cfg.results_dir / base, 1
        while path.exists():
            counter += 1
            path = self.cfg.results_dir / f"{base}-{counter}"
        return path

    @staticmethod
    def report_files(folder):
        """{path: (mtime, size)} of every XML report under a runner's report folder."""
        found = {}
        if folder.is_dir():
            for path in sorted(folder.rglob("*.xml")):
                if path.is_file():
                    stat = path.stat()
                    found[path] = (stat.st_mtime_ns, stat.st_size)
        return found

    def run_runners(self, batch, number, run_id, cases_by_class):
        """Run the docker runners of one batch. Each report the command wrote or rewrote (a file that was not there
        before the run or whose time or size changed) is copied into a new folder under the results folder and read
        from the copy; the runner's own report folder is never emptied or removed, and a leftover report of an earlier
        run is never read as this run's."""
        outcomes, archive = [], self.archive_path(run_id, number)
        for cls in batch:
            runner = self.cfg.runners[self.class_parts(cls)[0]]
            folder = self.root / runner["junit_dir"]
            cmd = list(runner["command"])
            cases = (cases_by_class or {}).get(cls)
            if cases:
                narrowed = cmd + self.runner_arguments(runner, cases)
                if len(shlex.join(narrowed)) > MAX_COMMAND_LINE:
                    # Windows refuses a command line this long (WinError 206): the whole class runs instead.
                    print(f"  {len(cases)} cases make the command too long; class {cls} runs whole", flush=True)
                    cases = None
                else:
                    cmd = narrowed
            before = self.report_files(folder)
            print("$ " + shlex.join(cmd), flush=True)
            proc = self.rt.run(cmd, cwd=self.root)
            after = self.report_files(folder)
            fresh = [path for path, stamp in after.items() if before.get(path) != stamp]
            if not fresh:
                tail = [l for l in (proc.stderr or proc.stdout or "").splitlines() if l.strip()][-12:]
                print(f"  runner {runner['name']} wrote no report into {runner['junit_dir']} (exit {proc.returncode}):",
                      flush=True)
                for line in tail or ["(no error output)"]:
                    print("    " + line.strip(), flush=True)
                outcomes.append(([cls], {}, {}, False))
                continue
            reported, reported_runnable, total = {}, {}, {"Passed": 0, "Failed": 0, "Skipped": 0}
            seen = {}
            for path in fresh:
                copy = archive / runner["name"] / path.relative_to(folder)
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(path), str(copy))
                try:
                    got = parse_junit(copy, runner["name"], self.today.isoformat(), run_id)
                except ET.ParseError as error:
                    print(f"unreadable JUnit report {path}: {error}", flush=True)
                    continue
                for key, entry in got[0].items():
                    seen.setdefault(key, []).append(entry["result"])
                    reported[key] = dict(entry, result=combine_results(seen[key]))
                reported_runnable.update(got[1])
            for entry in reported.values():
                total[entry["result"]] += 1
            print(f"  runner {runner['name']}: {total['Passed']} passed, {total['Failed']} failed, "
                  f"{total['Skipped']} skipped (exit {proc.returncode})", flush=True)
            self.print_uncited_failures(runner["name"], reported, reported_runnable)
            if cases:
                # A narrowed run keeps only the cited tests: Vitest's -t reports every test it did not select as
                # <skipped/>, and that must overwrite no earlier result. The archive says what was selected.
                cited = set(cases)
                (archive / runner["name"] / NARROWED_FILE).write_text(json.dumps({"selected": sorted(cited)}, indent=1), encoding="utf-8", newline="\n")
                by_path = {key: entry for key, entry in reported.items() if key in cited}
                runnable = {key: value for key, value in reported_runnable.items() if key in cited}
            else:
                by_path, runnable = reported, reported_runnable
            outcomes.append(([cls], by_path, runnable, bool(reported)))
            if not reported:
                print(f"  runner {runner['name']}: the reports hold no test case", flush=True)
        return outcomes

    def print_uncited_failures(self, name, reported, runnable):
        """What a runner's reports hold that no flow answers for: failed cases no flow cites (the case a file that
        fails to load is reported as is one of them), and the suite `vitest unhandled errors`."""
        raw = {"by_path": reported, "runnable": runnable}
        claimed = self.claimed(raw)
        self.raw_changed()   # the lookups above indexed a scratch dictionary
        failed = sorted(k for k, e in reported.items() if e["result"] == "Failed" and k not in claimed)
        unhandled = [k for k in failed if k.startswith(UNHANDLED_ERRORS + "/")]
        failed = [k for k in failed if k not in unhandled]
        for heading, keys in ((f"failed cases no flow cites ({len(failed)})", failed),
                              (f"{UNHANDLED_ERRORS} ({len(unhandled)})", unhandled)):
            if keys:
                print(f"  runner {name}: {heading}:", flush=True)
                for key in keys:
                    print(f"    {key}", flush=True)

    def run_swift_test(self, pkg, number, run_id):
        name = pathlib.PurePosixPath(pkg["dir"]).name
        report = self.bundle_path(run_id, number, f"-{name}", extension=".xml")
        if any(p.exists() for p in xunit_paths(report)):
            print(f"refused: the xunit report already exists, it is never overwritten: {report}", flush=True)
            return {}, {}, False
        report.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["swift", "test", "--package-path", str(self.root / pkg["dir"]), "--xunit-output", str(report)]
        print("$ " + " ".join(cmd), flush=True)
        proc = self.rt.run(cmd, cwd=self.root)
        if not any(p.exists() for p in xunit_paths(report)):
            tail = [l for l in (proc.stderr or proc.stdout or "").splitlines() if l.strip()][-12:]
            print(f"  swift test wrote no report (exit {proc.returncode}):", flush=True)
            for line in tail or ["(no error output)"]:
                print("    " + line.strip(), flush=True)
            return {}, {}, False
        by_path, runnable = parse_xunit(report, pkg["target"], self.today.isoformat(), run_id)
        return by_path, runnable, True

    def run_mode(self, state):
        args = self.args
        if args.resume:
            run = state["run"]
            run_id, mode = run["run_id"], run["mode"]
            planned = list(run.get("classes_planned") or [])
            done = set(run.get("classes_done") or [])
            cases_by_class = run.get("cases_by_class") or {}
            include_ui = bool(run.get("include_ui"))
            removed = [fid for fid in run.get("scope_ids") or [] if fid not in self.cells]
            scope = [fid for fid in run.get("scope_ids") or [] if fid in self.cells]
            print(f"resuming run {run_id} ({mode}): {len(done)} of {len(planned)} class(es) already done, "
                  f"UI {'included' if include_ui else 'left out'}", flush=True)
            for fid in removed:
                print(f"  {fid}: no longer in the map, skipped", flush=True)
        else:
            if args.only_stale:
                mode = "only-stale"
                scope = [fid for fid in self.ids
                         if canon(state["flows"].get(fid, {}).get("status", MISSING)) in (REVALIDATE, MISSING, BROKEN)]
                include_ui = args.ui or not self.cfg.ui_needs_flag
            elif args.changed or args.since:
                mode = "changed"
                scope = self.changed_scope()
                include_ui = args.ui
            else:
                mode = "full"
                scope = list(self.ids)
                include_ui = args.ui
            wanted, unresolved = self.classes_for(scope, state["raw"], include_ui)
            if self.cfg.platform == DOCKER and mode == "full":
                # a full run is every runner (the UI ones with --ui), whether or not a flow cites its tests yet
                wanted |= {f"{name}/{RUNNER_CLASS}" for name, spec in self.cfg.runners.items()
                           if include_ui or not spec["ui"]}
            if args.device and self.cfg.platform != "macos":
                family = self.family_of_device(args.device)
                waiting = {c for c in wanted if self.family(self.kind_of(c)) != family}
                wanted -= waiting
                for fid in scope:
                    held = sorted(c for c in self.needed(self.proofs[fid], state["raw"])[0] if c in waiting)
                    for cls in held:
                        print(f"waiting for a {self.kind_of(cls)} pass, not run on {args.device}: {fid} ({cls})",
                              flush=True)
            planned = sorted(wanted, key=self.sort_key)
            done = set()
            cases_by_class = self.cases_for(scope, state["raw"]) if mode == "only-stale" else {}
            cases_by_class = {c: v for c, v in cases_by_class.items() if c in wanted}
            run_id = f"{self.today.strftime('%Y%m%d')}T{self.clock.strftime('%H%M%S')}-{mode}"
            if unresolved:
                print(f"{len(unresolved)} proof(s) resolve to no test class; their flows are judged a lookup miss "
                      f"({self.label(MISSING)}), never a failure:", flush=True)
                for fid, proof in unresolved:
                    print(f"  {fid}: {proof}", flush=True)
            state["run"] = {"run_id": run_id, "mode": mode, "classes_planned": planned, "classes_done": [],
                            "scope_ids": scope, "include_ui": include_ui, "cases_by_class": cases_by_class,
                            "started_at": self.clock.isoformat(timespec="seconds"), "completed": False}
            self.save_state(state)  # a kill before the first batch is still resumable

        remaining = [c for c in planned if c not in done]
        if not remaining:
            print("nothing to run: every planned class is done", flush=True)
        failures = False
        kinds = []
        for cls in remaining:
            kind = self.kind_of(cls)
            if kind not in kinds:
                kinds.append(kind)
        kinds.sort(key=lambda k: (k != self.cfg.device_kind, str(k)))  # the project's own kind first
        if self.args.device and self.cfg.platform != "macos":
            family = self.family_of_device(self.args.device)
            skipped = [c for c in remaining if self.family(self.kind_of(c)) != family]
            for cls in skipped:
                print(f"waiting for a {self.kind_of(cls)} pass, not run on {self.args.device}: {cls}", flush=True)
            kinds = [k for k in kinds if self.family(k) == family]
        size = max(1, args.batch_size)
        number = 0
        dest_cache = {}
        for kind in kinds:
            group = [c for c in remaining if self.kind_of(c) == kind]
            for start in range(0, len(group), size):
                batch = group[start:start + size]
                number += 1
                produced = set()
                for members, by_path, runnable, ok in self.run_batch(batch, kind, number, run_id, cases_by_class,
                                                                     dest_cache):
                    if not ok:
                        continue
                    self.merge(state, by_path, runnable)
                    made = {c for c in members if c in set(runnable.values())}
                    if self.cfg.platform == DOCKER:
                        made = set(members)   # its reports held test cases, whether or not a cited one is among them
                    for cls in made:
                        if cls not in cases_by_class:
                            self.prune(state, cls, set(by_path))
                        else:
                            self.mark_absent(state, scope, cls, run_id)
                    produced |= made
                self.rebuild_by_name(state)
                for cls in batch:
                    if cls not in produced:
                        print(f"class without a result in this run: {cls}", flush=True)
                        failures = True
                done |= produced
                state["run"]["classes_done"] = sorted(done)
                self.ran_whole = {c for c in done if c not in cases_by_class}
                for fid in scope:
                    proofs = self.proofs[fid]
                    if self.ready(proofs, state["raw"], done):
                        self.update_flow(state, fid, proofs, *self.judge(proofs, state["raw"]), run_id)
                self.save_state(state)
                counts = self.write_doc(state)
                print(f"batch {number} ({', '.join(batch)}): {counts[OK]} of {len(self.ids)} flows {self.label(OK)}", flush=True)

        completed = set(planned) <= done and not failures
        state["run"]["completed"] = completed
        if completed:
            self.ran_whole = {c for c in done if c not in cases_by_class}
            for fid in scope:
                proofs = self.proofs[fid]
                if self.ready_final(proofs, state["raw"], done):
                    self.update_flow(state, fid, proofs, *self.judge(proofs, state["raw"]), run_id)
        self.save_state(state)
        counts = self.write_doc(state)
        print("map rewritten", flush=True)
        self.print_open_flows(state)
        self.print_listings(state)
        self.print_summary(counts)
        return 1 if (counts[BROKEN] or failures) else 0

    def reuse_mode(self, state, read, by_path, runnable, run_id):
        bundle_raw = {"by_path": by_path, "runnable": runnable}
        self.merge(state, by_path, runnable)
        for fid in self.ids:
            proofs = self.proofs[fid]
            if needs_no_run(proofs):
                continue
            automated = [p for p in proofs if not is_free(p)]
            inside = [p for p in automated if self.lookup(p, bundle_raw)[0] is not None]
            if not inside:
                continue
            if len(inside) < len(automated):
                print(f"partly covered by the bundle, status kept: {fid} ({len(inside)} of {len(automated)} proofs)",
                      flush=True)
                continue
            self.update_flow(state, fid, proofs, *self.judge(proofs, state["raw"]), run_id)
        self.save_state(state)
        counts = self.write_doc(state)
        print("map rewritten from " + "; ".join(f"{path} (results dated {when})" for path, when in read), flush=True)
        self.print_listings(state)
        self.print_summary(counts)
        return 1 if counts[BROKEN] else 0

    @staticmethod
    def narrowed_selection(report, top):
        """The proofs a narrowed run selected, from the NARROWED_FILE in the report's folder or above it (up to
        `top`); None when the report is not from a narrowed run."""
        folder = report.parent
        while True:
            manifest = folder / NARROWED_FILE
            if manifest.is_file():
                try:
                    return set(json.loads(manifest.read_text(encoding="utf-8"))["selected"])
                except (ValueError, KeyError, TypeError):
                    return None
            if folder == top or folder.parent == folder:
                return None
            folder = folder.parent

    def read_reuse_reports(self):
        """--reuse in a docker project: each target is one JUnit XML file, or a folder of them (the engine's own archive
        of a run holds one folder per runner). A report's runner is the configured runner whose name is a folder above
        it; without one, the runner is found from the test file stem when a proof is looked up. Every report keeps its
        own file's date; a test reported twice, in one target or in two, keeps the worst result and the later date,
        whatever the order of the targets."""
        by_path, runnable, seen, read, moments = {}, {}, {}, [], []
        for name in self.args.reuse:
            target = pathlib.Path(name)
            files = [target] if target.is_file() else sorted(target.rglob("*.xml")) if target.is_dir() else []
            if not files:
                raise Stop(2, f"no JUnit report found at {target}; nothing written")
            moment = datetime.datetime.fromtimestamp(max(os.path.getmtime(str(f)) for f in files))
            run_id = f"{moment.strftime('%Y%m%dT%H%M%S')}-reuse"
            read.append((target, moment.date().isoformat()))
            moments.append(moment)
            taken = 0
            for report in files:
                above = report.relative_to(target).parts[:-1] if target.is_dir() else (report.parent.name,)
                runner = next((part for part in reversed(above) if part in self.cfg.runners), None)
                day = datetime.date.fromtimestamp(os.path.getmtime(str(report))).isoformat()
                try:
                    got = parse_junit(report, runner, day, run_id)
                except ET.ParseError as error:
                    print(f"unreadable JUnit report {report}: {error}", flush=True)
                    continue
                selected = self.narrowed_selection(report, target if target.is_dir() else report.parent)
                for key, entry in got[0].items():
                    if selected is not None and key not in selected:
                        continue   # the archive of a narrowed run: the rest of the report is what the tool skipped
                    seen.setdefault(key, []).append(entry["result"])
                    later = entry if key not in by_path or entry["when"] >= by_path[key]["when"] else by_path[key]
                    by_path[key] = dict(later, result=combine_results(seen[key]))
                    taken += 1
                runnable.update({key: value for key, value in got[1].items() if selected is None or key in selected})
            if not taken:
                raise Stop(2, f"no test result read from {target}; nothing written")
        moment = max(moments)
        return read, by_path, runnable, f"{moment.strftime('%Y%m%dT%H%M%S')}-reuse"

    def read_reuse(self):
        if self.cfg.platform == DOCKER:
            return self.read_reuse_reports()
        by_path, runnable, read, moments = {}, {}, [], []
        for name in self.args.reuse:
            bundle = pathlib.Path(name)
            plist = bundle / "Info.plist"
            if plist.exists():
                moment = datetime.datetime.fromtimestamp(os.path.getmtime(str(plist)))
                when = moment.date().isoformat()
            else:
                moment, when = self.clock, self.today.isoformat()
            run_id = f"{moment.strftime('%Y%m%dT%H%M%S')}-reuse"
            out = self.rt.run(["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", str(bundle), "--compact"])
            try:
                data = json.loads(out.stdout) if out.returncode == 0 else {}
            except ValueError:
                data = {}
            got_path, got_runnable = parse_xcresult(data, when, run_id)
            if not got_path:
                raise Stop(2, f"no test case read from {bundle}; nothing written")
            for key, entry in got_path.items():
                by_path[key] = merge_bundle_entries(by_path[key], entry) if key in by_path else entry
            runnable.update(got_runnable)
            read.append((bundle, when))
            moments.append(moment)
        return read, by_path, runnable, f"{max(moments).strftime('%Y%m%dT%H%M%S')}-reuse"

    # -------------------------------------------------------- the whole invocation

    def check_arguments(self):
        args = self.args
        modes = [name for name, on in (("--from-cache/--invalidate", args.from_cache or args.invalidate),
                                       ("--reuse", bool(args.reuse)), ("--only-stale", args.only_stale),
                                       ("--changed", args.changed or bool(args.since)), ("--resume", args.resume),
                                       ("--prune-state", args.prune_state)) if on]
        if len(modes) > 1:
            raise Stop(2, "these modes cannot be combined: " + ", ".join(modes))
        if args.check:
            running = [flag for flag, on in (("--only-stale", args.only_stale), ("--changed", args.changed),
                                             ("--since", bool(args.since)), ("--resume", args.resume),
                                             ("--reuse", bool(args.reuse)), ("--ui", args.ui),
                                             ("--device", bool(args.device)), ("--result-path", bool(args.result_path)))
                       if on]
            if running:
                raise Stop(2, "--check verifies and writes nothing; it is refused beside " + ", ".join(running))
        if args.prune_state:
            others = [flag for flag, on in (("--check", args.check), ("--invalidate-classes", bool(args.invalidate_classes)),
                                            ("--ui", args.ui), ("--device", bool(args.device)),
                                            ("--result-path", bool(args.result_path))) if on]
            if others:
                raise Stop(2, "--prune-state goes alone; refused beside " + ", ".join(others))
        if args.invalidate_classes and (args.reuse or args.changed or args.resume):
            raise Stop(2, "--invalidate-classes goes alone or with --only-stale")
        if args.device and self.cfg.platform == "macos":
            raise Stop(2, "--device does not apply to a macOS project: its tests run on this Mac")
        if self.cfg.platform == DOCKER:
            refused = [flag for flag, on in (("--device", bool(args.device)), ("--derived-data", bool(args.derived_data)),
                                             ("--result-path", bool(args.result_path))) if on]
            if refused:
                raise Stop(2, ", ".join(refused) + " do not apply to a docker project: its tests run in containers "
                              "and their reports are archived under the configured results folder")
        if args.batch_size < 1:
            raise Stop(2, "--batch-size must be 1 or more")

    def execute(self):
        self.check_arguments()
        self.read_doc()
        state = self.load_state()
        self.state = state
        if self.args.prune_state:
            return self.prune_state(state)
        if self.args.resume and (not state.get("run") or state["run"].get("completed")):
            raise Stop(2, "nothing to resume: the state holds no unfinished run")
        self.build_source_index()
        self.catalog = (RustCatalog if self.cfg.is_rust else DockerCatalog if self.cfg.platform == DOCKER
                        else Catalog)(self.root, self.cfg)
        reuse = self.read_reuse() if self.args.reuse else None
        self.warn_index()

        self.refresh_free_flows(state)
        self.invalidate_pass(state)
        self.gone_pass(state)
        self.proofs_changed_pass(state)
        if self.args.invalidate_classes:
            self.invalidate_classes(state, self.args.invalidate_classes)

        if self.args.check:
            text, counts, rows = self.render(state)
            differs = []
            for fid in self.ids:
                cells = self.cells[fid]
                typed = (cells[3] if len(cells) > 3 else "", cells[4] if len(cells) > 4 else "")
                if typed != rows[fid]:
                    differs.append((fid, typed, rows[fid]))
            for fid, typed, wanted in differs:
                print(f"typed cell differs from the state: {fid}: map says '{typed[0]} {typed[1]}', "
                      f"state says '{wanted[0]} {wanted[1]}'", flush=True)
            self.print_listings(state)
            self.print_summary(counts)
            print("check: nothing written", flush=True)
            return 1 if (counts[BROKEN] or differs) else 0

        render_only = (self.args.from_cache or self.args.invalidate
                       or (bool(self.args.invalidate_classes) and not self.args.only_stale))
        if render_only:
            self.save_state(state)
            counts = self.write_doc(state)
            print("map rewritten from the stored state, no test run", flush=True)
            self.print_listings(state)
            self.print_summary(counts)
            return 1 if counts[BROKEN] else 0
        if reuse:
            return self.reuse_mode(state, *reuse)
        return self.run_mode(state)


def find_project(start):
    """The nearest folder, from `start` upward, that holds scripts/flows.json; `start` itself when none does."""
    start = pathlib.Path(start).resolve()
    for folder in [start] + list(start.parents):
        if (folder / "scripts" / "flows.json").is_file():
            return folder
    return start


def starter_map(lang, today):
    """The text of a new map: a heading, the scoreboard block at zero, the header row and no flow yet."""
    return "\n".join([
        "# Flow map" if lang.code == "en" else "# Mapa de fluxos",
        "",
        lang.start,
        "| Total | " + " | ".join(lang.board) + " |",
        "|---|---|---|---|---|---|",
        "| **0** | **0** (0%) | 0 | 0 | 0 | 0 |",
        "",
        lang.footer.format(date=today.isoformat()),
        lang.end,
        "",
        ("Add one row per flow below, under the header: an ID like F-AREA-01, the flow in a few words, the source "
         "files it runs through, the proofs separated by <br>, and leave the last two cells to `check-flows`."
         if lang.code == "en" else
         "Acrescente uma linha por fluxo abaixo do cabeçalho: um ID como F-AREA-01, o fluxo em poucas palavras, "
         "os arquivos de código por onde ele passa, as provas separadas por <br>, e deixe as duas últimas células "
         "para o `check-flows`."),
        "",
        lang.header_row().rstrip("\n"),
        "",
    ])


def check_init_arguments(args):
    others = [flag for flag, on in (("--check", args.check), ("--only-stale", args.only_stale),
                                    ("--from-cache/--invalidate", args.from_cache or args.invalidate),
                                    ("--reuse", bool(args.reuse)), ("--changed", args.changed or bool(args.since)),
                                    ("--resume", args.resume), ("--prune-state", args.prune_state),
                                    ("--invalidate-classes", bool(args.invalidate_classes)),
                                    ("--ui", args.ui), ("--device", bool(args.device)),
                                    ("--derived-data", bool(args.derived_data)),
                                    ("--result-path", bool(args.result_path))) if on]
    if others:
        raise Stop(2, "--init goes alone (and --language); refused beside " + ", ".join(others))


def init_map(args, runtime):
    root = pathlib.Path(args.project).resolve() if args.project else find_project(pathlib.Path.cwd())
    for lang in LANGUAGES.values():
        existing = root / "docs" / lang.map_name
        if existing.exists():
            raise Stop(2, f"a flow map already exists: {existing}; nothing written")
    lang = LANGUAGES[args.language or DEFAULT_LANGUAGE]
    path = root / "docs" / lang.map_name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomically(path, starter_map(lang, runtime.now().date()))
    print(f"starter map written: {path}", flush=True)
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="check-flows", allow_abbrev=False,
                                 description="Recompute the status of every flow in the project's flow map "
                                             "(docs/FLOWS.md, or docs/FLUXOS.md for a Portuguese map).")
    ap.add_argument("--project", help="the project root, the folder that holds docs/ and scripts/flows.json "
                                      "(default: the nearest folder above the current one with scripts/flows.json, "
                                      "else the current folder)")
    ap.add_argument("--language", choices=sorted(LANGUAGES),
                    help="the language of a map that carries none of its own marks yet, and of a map written by "
                         "--init (default en); a map that already has marks keeps its language and a --language "
                         "that contradicts them is refused")
    ap.add_argument("--init", action="store_true",
                    help="write a starter map (docs/FLOWS.md, or docs/FLUXOS.md with --language pt-BR) and exit; "
                         "an existing map is never overwritten")
    ap.add_argument("--only-stale", action="store_true",
                    help="run only the flows in revalidate, no proof or broken (the daily mode)")
    ap.add_argument("--from-cache", action="store_true",
                    help="recompute the map from the stored state and the sources; no test run, no device")
    ap.add_argument("--invalidate", action="store_true", help="the same as --from-cache")
    ap.add_argument("--invalidate-classes", metavar="A,B",
                    help="demote every validated flow whose proofs all resolve into these classes (Class or "
                         "Target/Class); alone it only recomputes, with --only-stale it reruns them")
    ap.add_argument("--check", action="store_true",
                    help="verify and write nothing: exit 1 on a broken flow or a typed Status cell")
    ap.add_argument("--prune-state", action="store_true",
                    help="remove from docs/.flows-state.json the entries of flows no longer in the map, print each, "
                         "and change nothing else")
    ap.add_argument("--reuse", metavar="BUNDLE", nargs="+",
                    help="judge the flows whose every automated proof is in these existing .xcresult bundles, read side "
                         "by side (a docker project: JUnit XML files or folders of them)")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted run")
    ap.add_argument("--changed", action="store_true",
                    help="run the flows whose cited files or test types git reports as changed")
    ap.add_argument("--since", metavar="REF", help="with --changed: compare with this git ref (untracked files count)")
    ap.add_argument("--ui", action="store_true", help="add the UI test classes to a full or --changed run")
    ap.add_argument("--device", help="a simulator name or a UDID (iOS only, refused for macOS and docker projects; "
                                     "default: the simulator \"devices\" names in scripts/flows.json for each device kind)")
    ap.add_argument("--derived-data", metavar="PATH",
                    help="derivedDataPath (default: the configuration's, else ~/Library/Developer/Xcode/DerivedData/"
                         "flows-<project>; refused for a docker project)")
    ap.add_argument("--result-path", metavar="PATH",
                    help="the result bundle of the first batch (later batches add -b<N>); an existing path is refused "
                         "(refused for a docker project)")
    ap.add_argument("--batch-size", type=int, default=1, help="test classes per batch and checkpoint (default 1)")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="the reference date (for tests of the engine)")
    return ap.parse_args(argv)


def main(argv=None, runtime=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if not args.project:
        args.project = str(find_project(pathlib.Path.cwd()))
    real = runtime is None
    runtime = runtime or Runtime()
    previous = None
    if real:
        def on_term(signum, frame):
            raise KeyboardInterrupt()  # a SIGTERM stops like a Ctrl-C: the state on disk is the checkpoint
        try:
            previous = signal.signal(signal.SIGTERM, on_term)
        except ValueError:  # not the main thread: no handler, nothing to restore
            real = False
    try:
        if args.init:
            check_init_arguments(args)
            return init_map(args, runtime)
        engine = Engine(args, runtime)
        return engine.execute()
    except Stop as stop:
        print(stop.message, file=sys.stderr, flush=True)
        return stop.code
    except KeyboardInterrupt:
        print("interrupted: the state on disk holds every finished batch; continue with --resume",
              file=sys.stderr, flush=True)
        raise
    finally:
        if real:
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    sys.exit(main())
