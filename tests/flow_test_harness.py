"""The harness of test_check_flows.py: the fixtures, the fake runtime that replaces every subprocess and the clock,
the builders of maps and states, and the base class of the engine tests. Standard library only."""
import contextlib
import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock
from xml.sax.saxutils import quoteattr

TESTS = pathlib.Path(__file__).resolve().parent
HERE = TESTS.parent / "plugins" / "workproof" / "scripts"   # the engine under test
FIXTURES = TESTS / "flow_fixtures"
ENGINE_PATH = HERE / "check_flows.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import check_flows

OK, REV, MISS, DEV, BROKEN = "validated", "revalidate", "no proof", "device only", "broken"
WORDS = {  # canonical status -> (shown in the map, stored in the state), per language under test
    "en": {OK: ("validated", "validated"), REV: ("revalidate", "revalidate"), MISS: ("no proof", "no proof"),
           DEV: ("device only", "device only"), BROKEN: ("broken", "broken")},
    "pt-BR": {OK: ("validado", "validado"), REV: ("revalidar", "revalidar"), MISS: ("sem prova", "sem prova"),
              DEV: ("so em aparelho", "só em aparelho"), BROKEN: ("quebrado", "quebrado")},
}
BOARD = {"en": "| Total | validated | revalidate | no proof | device only | broken |",
         "pt-BR": "| Total | validados | revalidar | sem prova | so em aparelho | quebrados |"}
HEADERS = {"en": "| ID | Flow | Entry | Proof | Status | Validated on |",
           "pt-BR": "| ID | Fluxo | Entrada | Prova | Status | Validado em |"}
FOOTERS = {"en": "*Recomputed on 2026-01-01 by `check-flows`. This scoreboard is not written by hand.*",
           "pt-BR": "*Recalculado em 2026-01-01 por `check-flows`. Este placar não é escrito à mão.*"}
MARKERS = {"en": ("<!-- scoreboard:start -->", "<!-- scoreboard:end -->"),
           "pt-BR": ("<!-- placar:inicio -->", "<!-- placar:fim -->")}
MAP_NAMES = {"en": "FLOWS.md", "pt-BR": "FLUXOS.md"}
LANG = {"code": "en"}   # the language under test: EngineCase.setUp sets it from the class's `language`


def shown(status):
    """The word the map shows for a canonical status, in the language under test."""
    return WORDS[LANG["code"]][status][0]


def stored(status):
    """The word the state file keeps for a canonical status, in the language under test."""
    return WORDS[LANG["code"]][status][1]


TO_CANON = {word: status for words in WORDS.values() for status, pair in words.items() for word in pair}

NOW = datetime.datetime(2026, 3, 1, 10, 0, 0)


def ts(year, month, day, hour=12):
    return datetime.datetime(year, month, day, hour).timestamp()


T_SRC = ts(2026, 1, 5)       # every fixture file
T_PRINT = ts(2026, 2, 1)     # the screenshots
T_CHANGE = ts(2026, 2, 10)   # a source edited after the screenshots
RUN1 = "20260110T090000-only-stale"
RUN2 = "20260120T090000-only-stale"
RUN_OLD = "20260101T090000-only-stale"
STALE_FP = "0" * 40

DEVICES = {  # kind -> (name, udid), as in flow_fixtures/simctl_devices.json
    "phone": ("Demo-iPhone-abc123", "11111111-1111-4111-8111-111111111111"),
    "small": ("Demo-iPhone-17e-abc123", "33333333-3333-4333-8333-333333333333"),
    "ipad": ("Demo-iPad-abc123", "44444444-4444-4444-8444-444444444444"),
    "se": ("Demo-iPhone-SE-abc123", "55555555-5555-4555-8555-555555555555"),  # added to the fake's simctl list by FakeRuntime
}
UDID_PHONE = DEVICES["phone"][1]
UDID_PHONE_2 = "22222222-2222-4222-8222-222222222222"
UDID_IPAD = DEVICES["ipad"][1]
UDID_SE = DEVICES["se"][1]

# ---------------------------------------------------------------------------- recorded tool output (shape)


def tc(target, suite, name, ident=None, result="Passed"):
    """One test case as `xcresulttool get test-results tests --compact` reports it."""
    return {"target": target, "suite": suite, "name": name, "ident": ident or f"{suite}/{name}", "result": result}


def xcresult_json(cases):
    plan = {"name": "Demo", "nodeType": "Test Plan", "result": "Passed", "children": []}
    bundles = {}
    for c in cases:
        bundle = bundles.get(c["target"])
        if bundle is None:
            kind = "UI test bundle" if c["target"].endswith("UITests") else "Unit test bundle"
            bundle = {"name": c["target"], "nodeType": kind, "result": "Passed", "children": []}
            bundles[c["target"]] = bundle
            plan["children"].append(bundle)
        suite = next((s for s in bundle["children"] if s["name"] == c["suite"]), None)
        if suite is None:
            suite = {"name": c["suite"], "nodeType": "Test Suite", "result": "Passed", "children": []}
            bundle["children"].append(suite)
        suite["children"].append({"name": c["name"], "nodeIdentifier": c["ident"], "nodeType": "Test Case",
                                  "result": c["result"]})
    return {"devices": [], "testNodes": [plan], "testPlanConfigurations": []}


DEMO_UNIT = [
    tc("DemoTests", "RulesTests", "testA()"),
    tc("DemoTests", "RulesTests", "testB()"),
    tc("DemoTests", "Pricing rules", "rounds half up", "PricingTests/roundsHalfUp()"),
    tc("DemoTests", "Pricing rules", "loads()", "PricingTests/loads()"),
    tc("DemoTests", "Pricing rules", "extension sentence", "PricingTests/extensionCase()"),
    tc("DemoTests", "Tagged", "tag sentence", "TaggedTests/tagCase()"),
    tc("DemoTests", "Enum rules", "enum sentence", "EnumRulesTests/enumCase()"),
    tc("DemoTests", "Actor rules", "actor sentence", "ActorRulesTests/actorCase()"),
    tc("DemoTests", "SuiteA", "loads", "SuiteATests/loads()"),
    tc("DemoTests", "SuiteB", "loads", "SuiteBTests/loads()"),
    tc("DemoTests", "RightSuite", "loads", "RightSuiteTests/loads()"),
]
DEMO_UI = [
    tc("DemoUITests", "HomeUITests", "testOpensDetail()"),
    tc("DemoUITests", "HomeUITests", "testSaves()"),
    tc("DemoUITests", "IPadLayoutUITests", "testLayout()"),
]
DEMO_MASTER = DEMO_UNIT + DEMO_UI
GAME_MASTER = [
    tc("DemoGameTests", "MenuTests", "testOpens()"),
    tc("PhysicsTests", "Economy", "cost doubles per level", "EconomyTests/costDoubles()"),
    tc("PhysicsTests", "Economy", "testCost()", "EconomyTests/testCost()"),
    tc("DemoGameUITests", "MenuUITests", "testTap()"),
]
MAC_MASTER = [
    tc("DemoMacTests", "MacRulesTests", "testMacRule()"),
    tc("DemoMacUITests", "MacUITests", "testWindowOpens()"),
]


def jc(runner, file, chain, result="Passed", report="junit.xml"):
    """One test case a docker runner reports: the file as the reporter prints it, the describe chain and the test
    name, and the report file it lands in."""
    return {"runner": runner, "file": file, "chain": tuple(chain), "result": result, "report": report}


U_SPEC, S_SPEC, G_SPEC = "test/unit/crypto.service.spec.ts", "test/unit/settings.spec.ts", "test/integration/gateway.int.spec.ts"
SYSTEM_API = [
    jc("api", U_SPEC, ["CryptoService", "encrypts and decrypts a key"], report="unit.xml"),
    jc("api", U_SPEC, ["CryptoService", "with a wrong master key", "fails to decrypt"], report="unit.xml"),
    jc("api", U_SPEC, ["CryptoService", "with a wrong master key", "is slow"], "Skipped", report="unit.xml"),
    jc("api", U_SPEC, ["CryptoService", "rotates the master key"], "Skipped", report="unit.xml"),
    jc("api", U_SPEC, ["CryptoService", "reads a template without an expression"], report="unit.xml"),
    jc("api", U_SPEC, ["top level case"], report="unit.xml"),
    jc("api", S_SPEC, ["settings", "parses effort"], report="unit.xml"),
    jc("api", S_SPEC, ["settings", "case 1"], report="unit.xml"),
    jc("api", S_SPEC, ["settings", "case 2"], report="unit.xml"),
    jc("api", G_SPEC, ["gateway", "returns 429 with Retry-After when the rate limit is exceeded"], report="integration.xml"),
    jc("api", G_SPEC, ["gateway", "refuses a request with tools"], report="integration.xml"),
]
SYSTEM_WEB = [jc("web", "test/catalog.unit.ts", ["catalog parity", "both languages have the same keys"])]
SYSTEM_E2E = [jc("e2e", "providers.e2e.ts", ["providers", "creates a provider"]),
              jc("e2e", "providers.e2e.ts", ["providers", "syncs the catalog"])]
SYSTEM_MASTER = SYSTEM_API + SYSTEM_WEB + SYSTEM_E2E
SYSTEM_SERVICES = {"api-test": "api", "web-test": "web", "e2e": "e2e"}      # compose service -> runner
SYSTEM_REPORT_DIRS = {"api": "test-results/api", "web": "test-results/web", "e2e": "test-results/e2e"}
SYSTEM_FRAMEWORK = {"api": "vitest", "web": "vitest", "e2e": "playwright"}
COMPOSE_RUN = ["docker", "compose", "--profile", "test", "run", "--rm"]

MASTERS = {"Demo": DEMO_MASTER, "DemoGame": GAME_MASTER, "DemoMac": MAC_MASTER, "DemoSystem": SYSTEM_MASTER}

# proof path -> (Target/Class it runs in, nodeIdentifier), for seeding the state's raw results
KNOWN = {
    "RulesTests/testA()": ("DemoTests/RulesTests", "RulesTests/testA()"),
    "RulesTests/testB()": ("DemoTests/RulesTests", "RulesTests/testB()"),
    "RulesTests/testA2()": ("DemoTests/RulesTests", "RulesTests/testA2()"),
    "RulesTests/testRemoved()": ("DemoTests/RulesTests", "RulesTests/testRemoved()"),
    "Pricing rules/rounds half up": ("DemoTests/PricingTests", "PricingTests/roundsHalfUp()"),
    "Pricing rules/loads()": ("DemoTests/PricingTests", "PricingTests/loads()"),
    "Pricing rules/extension sentence": ("DemoTests/PricingTests", "PricingTests/extensionCase()"),
    "Pricing rules/removed sentence": ("DemoTests/PricingTests", "PricingTests/removed()"),
    "Tagged/tag sentence": ("DemoTests/TaggedTests", "TaggedTests/tagCase()"),
    "Enum rules/enum sentence": ("DemoTests/EnumRulesTests", "EnumRulesTests/enumCase()"),
    "Actor rules/actor sentence": ("DemoTests/ActorRulesTests", "ActorRulesTests/actorCase()"),
    "SuiteA/loads": ("DemoTests/SuiteATests", "SuiteATests/loads()"),
    "SuiteB/loads": ("DemoTests/SuiteBTests", "SuiteBTests/loads()"),
    "HomeUITests/testOpensDetail()": ("DemoUITests/HomeUITests", "HomeUITests/testOpensDetail()"),
    "HomeUITests/testSaves()": ("DemoUITests/HomeUITests", "HomeUITests/testSaves()"),
    "IPadLayoutUITests/testLayout()": ("DemoUITests/IPadLayoutUITests", "IPadLayoutUITests/testLayout()"),
    "Opening flows/opens detail": ("DemoUITests/HomeUITests", "HomeUITests/testOpensDetail()"),
    "Saving flows/saves": ("DemoUITests/HomeUITests", None),
    "MenuTests/testOpens()": ("DemoGameTests/MenuTests", "MenuTests/testOpens()"),
}

# ---------------------------------------------------------------------------- state and document builders

def header():
    return HEADERS[LANG["code"]] + "\n|---|---|---|---|---|---|\n"


def placar(code=None):
    code = code or LANG["code"]
    start, end = MARKERS[code]
    return (start + "\n" + BOARD[code] + "\n|---|---|---|---|---|---|\n| **0** | **0** (0%) | 0 | 0 | 0 | 0 |\n\n"
            + FOOTERS[code] + "\n" + end + "\n")


def to_language(text, code):
    """A map written in English, rewritten in `code`: the header row, the scoreboard block and the status cells."""
    if code == "en":
        return text
    text = text.replace(HEADERS["en"], HEADERS[code]).replace(placar("en"), placar(code))
    out = []
    for line in text.split("\n"):
        cells = line.split("|")
        if line.startswith("| F-") and len(cells) >= 7:
            word = cells[5].strip()
            if word in WORDS["en"]:
                cells[5] = " " + WORDS[code][word][0] + " "
                line = "|".join(cells)
        out.append(line)
    return "\n".join(out)


def state_in_language(state, code):
    """A state written with canonical statuses, with each status stored as `code` stores it."""
    state = json.loads(json.dumps(state))
    for rec in state["flows"].values():
        rec["status"] = WORDS[code].get(rec["status"], (None, rec["status"]))[1]
    return state


def row(fid, name, entrada, prova, status="no proof", when="—"):
    return (fid, name, entrada, prova, status, when)


def doc_text(*rows):
    body = "".join(f"| {i} | {n} | {e} | {p} | {shown(s) if s in WORDS['en'] else s} | {w} |\n" for i, n, e, p, s, w in rows)
    return "# Test map\n\n" + placar() + "\n" + header() + body + "\nEnd of the map.\n"


def frec(status, proofs, fp=None, when=None, run=None, note="", missing=None):
    rec = {"status": status, "proofs": list(proofs), "note": note, "source_missing": list(missing or [])}
    if when:
        rec["validated_at"] = when
    if fp is not None:
        rec["source_fingerprint"] = fp
    if run:
        rec["run_id"] = run
    return rec


def seed(*paths, result="Passed", when="2026-01-10", run=RUN1, identifiers=True):
    """(by_path, runnable) for these proof paths, as a past run would have stored them."""
    by_path, runnable = {}, {}
    for path in paths:
        cls, ident = KNOWN[path]
        entry = {"result": result, "when": when, "run_id": run}
        if identifiers and ident:
            entry["identifier"] = ident
        by_path[path] = entry
        runnable[path] = cls
    return by_path, runnable


def make_state(flows=None, raw=None, run=None):
    by_path, runnable = raw if raw else ({}, {})
    by_name = {}
    for key, entry in by_path.items():
        name = key.split("/", 1)[1]
        by_name[name] = {"result": "AMBIGUOUS"} if name in by_name else dict(entry)
    return {"version": 2, "flows": flows or {}, "raw": {"by_path": by_path, "by_name": by_name,
                                                          "runnable": runnable}, "run": run}


def sha1_of(paths):
    """The engine's fingerprint: sha1 over the bytes of the cited files, sorted by basename."""
    hasher = hashlib.sha1()
    for path in sorted(paths, key=lambda p: pathlib.Path(p).name):
        hasher.update(pathlib.Path(path).read_bytes())
    return hasher.hexdigest()


def doc_row(text, fid):
    for line in text.splitlines():
        if re.match(r"^\|\s*" + re.escape(fid) + r"\s*\|", line):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return None


def scoreboard(text):
    m = re.search(r"\|\s*\*\*(\d+)\*\*\s*\|\s*\*\*(\d+)\*\*[^|]*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", text)
    if not m:
        return None
    keys = ("total", OK, REV, MISS, DEV, BROKEN)
    return dict(zip(keys, (int(g) for g in m.groups())))


def summary(out):
    words = [shown(s) for s in (OK, REV, MISS, DEV, BROKEN)]
    m = re.search(r"^flows: (\d+) total, " + ", ".join(r"(\d+) " + w for w in words) + "$", out, re.M)
    if not m:
        return None
    return dict(zip(("total", OK, REV, MISS, DEV, BROKEN), (int(g) for g in m.groups())))


def listing(out, *keywords):
    """The indented lines under the first unindented heading holding all keywords; None without that heading."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line and not line[0].isspace() and all(k in line.lower() for k in keywords):
            items = []
            for follow in lines[i + 1:]:
                if follow.strip() and follow[0].isspace():
                    items.append(follow.strip())
                else:
                    break
            return items
    return None


def arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def only_testing(cmd):
    return [a[len("-only-testing:"):] for a in cmd if a.startswith("-only-testing:")]


def class_of(selection):
    return "/".join(selection.split("/")[:2])


def norm(status):
    """The canonical status of a word as the map or the state shows it, in either language."""
    return TO_CANON.get(status, status)


def junit_xml(framework, cases):
    """JUnit XML in the shape the reporter documents: Vitest's `testsuites > testsuite > testcase` with the file path
    in `classname` and the title path joined by ` > ` in `name`; Playwright's with the title path joined by ` \u203a `
    and the testsuite named after the file. `cases` are `jc` dicts."""
    separator = " > " if framework == "vitest" else " \u203a "
    suites = {}
    for c in cases:
        suites.setdefault(c["file"], []).append(c)
    out = ['<?xml version="1.0" encoding="UTF-8" ?>', '<testsuites name="%s" tests="%d">'
           % ("vitest tests" if framework == "vitest" else "", len(cases))]
    for file, members in suites.items():
        out.append("<testsuite name=%s tests=\"%d\">" % (quoteattr(file), len(members)))
        for c in members:
            head = "<testcase classname=%s name=%s time=\"0.01\">" % (quoteattr(file), quoteattr(separator.join(c["chain"])))
            body = {"Passed": "", "Skipped": "<skipped/>",
                    "Failed": "<failure message=\"expected 5 to be 4\" type=\"AssertionError\">trace</failure>",
                    "Error": "<error message=\"boom\" type=\"Error\">trace</error>"}[c["result"]]
            out.append(head + body + "</testcase>")
        out.append("</testsuite>")
    out.append("</testsuites>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------- the fake runtime


class Call:
    def __init__(self, cmd, cwd, env):
        self.cmd, self.cwd, self.env = cmd, cwd, env

    def __repr__(self):
        return f"Call({self.cmd!r}, cwd={self.cwd!r})"


class FakeRuntime:
    """Replaces every subprocess, the clock and git. Records every call; refuses what it was not told to expect."""

    def __init__(self):
        self.now_value = NOW
        self.calls = []
        self.master = []
        self.overrides = {}          # (suite, name) -> result, for the "auto" bundles
        self.xcodebuild = []         # queue: "auto", "!exit65", "!interrupt", a results/<name>.json name, or a dict
        self.swift = []              # queue of xunit pair names
        self.fail_xcresulttool = False
        self.git_diff, self.git_cached, self.git_since = [], [], []
        self.git_untracked, self.git_tracked = [], []
        self.bundles_written = []
        self.docker = []             # queue for a docker project's runners: "auto", "!exit1", "!interrupt", "!malformed", "!empty"
        self.report_dirs = dict(SYSTEM_REPORT_DIRS)
        self.load_failures = set()   # test files (as the report prints them) that fail to load
        self.reports_written = []
        self.ticks = 0
        self.devices = json.loads((FIXTURES / "simctl_devices.json").read_text())
        # the SE is not in the shared fixture file
        next(iter(self.devices["devices"].values())).append({
            "name": DEVICES["se"][0], "udid": DEVICES["se"][1], "isAvailable": True, "state": "Shutdown",
            "deviceTypeIdentifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-SE--2nd-generation-"})

    def now(self):
        return self.now_value

    def reset(self):
        self.calls, self.bundles_written = [], []

    # -- views used by the tests
    def xcode_calls(self):
        return [c for c in self.calls if c.cmd and c.cmd[0] == "xcodebuild"]

    def swift_calls(self):
        return [c for c in self.calls if c.cmd and c.cmd[0] == "swift"]

    def runner_calls(self):
        """Every call that builds or runs a test, resolves a device or reads a result bundle."""
        return [c for c in self.calls if (c.cmd and c.cmd[0] in ("xcodebuild", "swift", "docker"))
                or c.cmd[:2] in (["xcrun", "xcresulttool"], ["xcrun", "simctl"])]

    def commands(self, program):
        return [c for c in self.calls if c.cmd and c.cmd[0] == program]

    def docker_calls(self):
        return [c for c in self.calls if c.cmd and c.cmd[0] == "docker"]

    # -- the seam
    def run(self, cmd, cwd=None, env=None, **kwargs):
        cmd = [str(c) for c in cmd]
        call = Call(cmd, str(cwd) if cwd is not None else None, dict(env) if env else None)
        self.calls.append(call)
        program = cmd[0] if cmd else ""
        if program == "xcodebuild":
            return self._xcodebuild(cmd)
        if program == "swift":
            return self._swift(cmd)
        if program == "xcrun" and cmd[1:3] == ["xcresulttool", "get"]:
            return self._xcresulttool(cmd)
        if program == "xcrun" and cmd[1:3] == ["simctl", "list"]:
            return self._done(cmd, 0, json.dumps(self.devices))
        if program == "git":
            return self._git(cmd)
        if program == "docker":
            return self._docker(cmd, cwd)
        raise AssertionError(f"the engine ran a command the tests do not expect: {cmd}")

    @staticmethod
    def _done(cmd, code=0, out="", err=""):
        return subprocess.CompletedProcess(cmd, code, out, err)

    def _xcodebuild(self, cmd):
        assert "test" in cmd, f"xcodebuild without the test action: {cmd}"
        if not self.xcodebuild:
            raise AssertionError(f"unexpected xcodebuild call, the test queued no result for it: {cmd}")
        action = self.xcodebuild.pop(0)
        if action == "!interrupt":
            raise KeyboardInterrupt()
        if action == "!exit65":
            return self._done(cmd, 65, "", "xcodebuild: error: Failed to build the scheme (fake)")
        bundle = pathlib.Path(arg_after(cmd, "-resultBundlePath"))
        if bundle.exists():
            raise AssertionError(f"the engine handed xcodebuild a result bundle path that already exists: {bundle}")
        if isinstance(action, dict):
            data = action
        elif action == "auto":
            data = xcresult_json(self._auto_cases(cmd))
        else:
            data = json.loads((FIXTURES / "results" / f"{action}.json").read_text())
        bundle.mkdir(parents=True)
        (bundle / "fixture.json").write_text(json.dumps(data))
        (bundle / "Info.plist").write_text("<plist/>")
        os.utime(bundle / "Info.plist", (self.now_value.timestamp(),) * 2)
        self.bundles_written.append(bundle)
        failed = any(n.get("result") == "Failed" for n in self._walk_cases(data))
        out = "Test run with tests finished\n** TEST FAILED **\n" if failed else "** TEST SUCCEEDED **\n"
        return self._done(cmd, 65 if failed else 0, out)

    def _auto_cases(self, cmd):
        wanted = only_testing(cmd)
        cases = []
        for c in self.master:
            cls = c["ident"].split("/")[0]
            method = c["ident"].split("/")[1].replace("()", "")
            for w in wanted:
                parts = w.split("/")
                if parts[0] == c["target"] and parts[1] == cls and (len(parts) < 3 or parts[2].replace("()", "") == method):
                    result = self.overrides.get((c["suite"], c["name"]), c["result"])
                    cases.append(dict(c, result=result))
                    break
        return cases

    @staticmethod
    def _walk_cases(data):
        stack = list(data.get("testNodes", []))
        while stack:
            node = stack.pop()
            if node.get("nodeType") == "Test Case":
                yield node
            stack.extend(node.get("children") or [])

    def _docker(self, cmd, cwd):
        """A runner's compose command: finds the service, applies the name and file filters the engine appended, and
        writes JUnit XML into the runner's report folder (inside the cwd) as the container would."""
        assert cmd[:2] == ["docker", "compose"], f"docker without compose: {cmd}"
        assert cwd is not None, "a runner must run from the project folder"
        index = next((i for i, a in enumerate(cmd) if a in SYSTEM_SERVICES), None)
        if index is None:
            raise AssertionError(f"no known compose service in {cmd}")
        runner = SYSTEM_SERVICES[cmd[index]]
        if not self.docker:
            raise AssertionError(f"unexpected docker call, the test queued no result for it: {cmd}")
        action = self.docker.pop(0)
        if action == "!interrupt":
            raise KeyboardInterrupt()
        if action == "!exit1":
            return self._done(cmd, 1, "", "compose: the image did not build (fake)")
        extra, pattern, files, k = cmd[index + 1:], None, [], 0
        while k < len(extra):
            if extra[k] in ("-t", "--grep"):
                pattern = extra[k + 1]
                k += 2
            else:
                files.append(extra[k])
                k += 1
        framework = SYSTEM_FRAMEWORK[runner]
        cases = []
        for c in self.master:
            if c["runner"] != runner:
                continue
            if files and not any(f in c["file"] for f in files):
                continue
            result = self.overrides.get((runner, c["chain"]), c["result"])
            if pattern is not None:
                # what each tool matches the pattern against (read in their sources on 08/10/2026): Vitest the full
                # test name, the names joined by " > ", a regular expression with no flags; Playwright the project,
                # the file, the describe names, the title and the tags joined by spaces, flags "gi"
                if framework == "vitest":
                    matched = re.search(pattern, " > ".join(c["chain"]))
                else:
                    matched = re.search(pattern, " ".join(["chromium", c["file"]] + list(c["chain"])), re.I)
                if not matched:
                    if framework == "playwright":
                        continue          # Playwright filters the test out: it is not run and not reported
                    result = "Skipped"    # Vitest keeps it in the file and marks it skip: the report says <skipped/>
            cases.append(dict(c, result=result))
        for broken in sorted(self.load_failures):
            # Vitest, a file that fails to load and holds no task: one test case named after the file, with a failure
            report = next((c["report"] for c in cases if c["file"] == broken), "junit.xml")
            cases = [c for c in cases if c["file"] != broken] + [jc(runner, broken, [broken], "Failed", report=report)]
        folder = pathlib.Path(cwd) / self.report_dirs[runner]
        folder.mkdir(parents=True, exist_ok=True)
        grouped = {}
        for c in cases:
            grouped.setdefault(c["report"], []).append(c)
        if action == "!empty":
            grouped = {"empty.xml": []}
        for name, members in sorted(grouped.items()):
            path = folder / name
            path.write_text("<testsuites><oops" if action == "!malformed" else junit_xml(framework, members))
            self.ticks += 1
            stamp = self.now_value.timestamp() + 60 * self.ticks
            os.utime(path, (stamp, stamp))
            self.reports_written.append(path)
        failed = any(c["result"] in ("Failed", "Error") for c in cases)
        return self._done(cmd, 1 if failed else 0, "runner finished (fake)\n")

    def _xcresulttool(self, cmd):
        if self.fail_xcresulttool:
            return self._done(cmd, 1, "", "xcresulttool: error: cannot read the bundle (fake)")
        fixture = pathlib.Path(arg_after(cmd, "--path")) / "fixture.json"
        if not fixture.exists():
            return self._done(cmd, 1, "", f"no result bundle at {fixture.parent}")
        return self._done(cmd, 0, fixture.read_text())

    def _swift(self, cmd):
        if not self.swift:
            raise AssertionError(f"unexpected swift test call, the test queued no result for it: {cmd}")
        name = self.swift.pop(0)
        path = pathlib.Path(arg_after(cmd, "--xunit-output"))
        sibling = path.with_name(path.stem + "-swift-testing" + path.suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        failed = False
        for source, target in ((FIXTURES / "xunit" / f"{name}.xunit.xml", path),
                               (FIXTURES / "xunit" / f"{name}.xunit-swift-testing.xml", sibling)):
            if source.exists():
                shutil.copy(source, target)
                failed = failed or "<failure" in source.read_text()
        return self._done(cmd, 1 if failed else 0, "Executed tests (fake)\n")

    def _git(self, cmd):
        if cmd[1] == "diff":
            if "--cached" in cmd:
                files = self.git_cached
            elif any(not a.startswith("-") for a in cmd[2:]):
                files = self.git_since
            else:
                files = self.git_diff
            return self._done(cmd, 0, "".join(f + "\n" for f in files))
        if cmd[1] == "ls-files":
            if "--others" in cmd:
                return self._done(cmd, 0, "".join(f + "\n" for f in self.git_untracked))
            asked = cmd[cmd.index("--") + 1:] if "--" in cmd else []
            listed = [f for f in self.git_tracked if not asked or f in asked]
            if "--error-unmatch" in cmd:
                return self._done(cmd, 0 if listed else 1, "".join(f + "\n" for f in listed))
            return self._done(cmd, 0, "".join(f + "\n" for f in listed))
        return self._done(cmd, 0, "")


# ---------------------------------------------------------------------------- spies and clocks


@contextlib.contextmanager
def deletion_spy():
    """Records every deletion the engine makes through the standard library (the path of each)."""
    seen = []
    real_rmtree, real_remove, real_unlink, real_rmdir = shutil.rmtree, os.remove, os.unlink, os.rmdir
    real_path_unlink = pathlib.Path.unlink

    def wrap(name, fn):
        def inner(*args, **kwargs):
            seen.append((name, str(args[0]) if args else ""))
            return fn(*args, **kwargs)
        return inner

    def path_unlink(self, *args, **kwargs):
        seen.append(("Path.unlink", str(self)))
        return real_path_unlink(self, *args, **kwargs)

    with mock.patch.object(shutil, "rmtree", wrap("rmtree", real_rmtree)), \
            mock.patch.object(os, "remove", wrap("remove", real_remove)), \
            mock.patch.object(os, "unlink", wrap("unlink", real_unlink)), \
            mock.patch.object(os, "rmdir", wrap("rmdir", real_rmdir)), \
            mock.patch.object(pathlib.Path, "unlink", path_unlink):
        yield seen


class _NoToday(datetime.date):
    @classmethod
    def today(cls):
        raise AssertionError("the engine read the real date: it must use runtime.now() or --today")


class _NoNow(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        raise AssertionError("the engine read the real clock: it must use runtime.now()")

    @classmethod
    def today(cls):
        raise AssertionError("the engine read the real clock: it must use runtime.now()")

    @classmethod
    def utcnow(cls):
        raise AssertionError("the engine read the real clock: it must use runtime.now()")


@contextlib.contextmanager
def real_clock_forbidden():
    with mock.patch.object(datetime, "date", _NoToday), mock.patch.object(datetime, "datetime", _NoNow):
        yield


# ---------------------------------------------------------------------------- the harness


class Result:
    def __init__(self, code, out, err, interrupted=False):
        self.code, self.out, self.err, self.interrupted = code, out, err, interrupted

    @property
    def text(self):
        return self.out + self.err


DEL = object()


class Project:
    def __init__(self, root, master):
        self.root, self.master = root, master
        self.doc_path = root / "docs" / MAP_NAMES[LANG["code"]]
        self.state_path = root / "docs" / ".flows-state.json"

    def set_doc(self, text):
        self.doc_path.write_text(text)

    def set_state(self, state):
        if state is None:
            if self.state_path.exists():
                self.state_path.unlink()
        else:
            state = state_in_language(state, LANG["code"])
            self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))

    def doc(self):
        return self.doc_path.read_text()

    def state(self):
        return json.loads(self.state_path.read_text())

    def snapshot(self):
        return (self.doc_path.read_bytes(),
                self.state_path.read_bytes() if self.state_path.exists() else None)

    def config(self, **changes):
        path = self.root / "scripts" / "flows.json"
        data = json.loads(path.read_text())
        for key, value in changes.items():
            if value is DEL:
                data.pop(key, None)
            else:
                data[key] = value
        path.write_text(json.dumps(data, indent=2))

    def path(self, name):
        found = sorted(p for p in self.root.rglob(name) if ".build" not in p.parts)
        assert found, f"{name} is not in the fixture project {self.root.name}"
        return found[0]

    def fp(self, *names):
        return sha1_of([self.path(n) for n in sorted(set(names))])

    def write(self, rel, text, when=T_SRC):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text) if isinstance(text, str) else path.write_bytes(text)
        os.utime(path, (when, when))
        return path

    def edit(self, name, when=T_CHANGE):
        path = name if isinstance(name, pathlib.Path) else self.path(name)
        with open(path, "ab") as handle:
            handle.write(b"\n// edited\n")
        os.utime(path, (when, when))

    def touch(self, rel, when):
        os.utime(self.root / rel, (when, when))


KEEP = object()


class EngineCase(unittest.TestCase):
    maxDiff = None
    language = "en"   # the language of the maps this class writes and reads

    def setUp(self):
        LANG["code"] = self.language
        self.addCleanup(LANG.update, code="en")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = pathlib.Path(temp.name).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = FakeRuntime()
        self.count = 0

    # -- projects
    def project(self, kind="Demo", doc=KEEP, state=KEEP, config=None):
        """A fresh copy of the fixture tree; returns the one project asked for."""
        self.count += 1
        base = self.tmp / f"t{self.count}" / "tree"
        shutil.copytree(FIXTURES / "project_tree", base)
        # decoys the index must skip: a build folder's copy and a dependency's copy (made here, never shipped)
        (base / "DemoMac/Packages/DemoCore/.build/debug").mkdir(parents=True)
        (base / "DemoMac/Packages/DemoCore/.build/debug/Core.swift").write_text("// decoy: a build folder's copy\n")
        (base / "DemoSystem/apps/api/node_modules/dep").mkdir(parents=True)
        (base / "DemoSystem/apps/api/node_modules/dep/index.ts").write_text("// decoy: a dependency's copy\n")
        if self.language != "en":
            for found in base.rglob("docs/FLOWS*.md"):
                found.write_text(to_language(found.read_text(), self.language))
                if found.name == "FLOWS.md":
                    found.rename(found.with_name(MAP_NAMES[self.language]))
            for found in base.rglob("docs/.flows-state.json"):
                found.write_text(json.dumps(state_in_language(json.loads(found.read_text()), self.language),
                                            ensure_ascii=False, indent=1, sort_keys=True))
        for path in base.rglob("*"):
            if path.is_file():
                os.utime(path, (T_SRC, T_SRC))
        for png in base.rglob("*.png"):
            os.utime(png, (T_PRINT, T_PRINT))
        project = Project(base / kind, MASTERS[kind])
        if doc is not KEEP:
            project.set_doc(doc)
        if state is not KEEP:
            project.set_state(state)
        if config:
            project.config(**config)
        return project

    def make_bundle(self, name, cases, when=None):
        """A result bundle on disk for --reuse: fixture.json plus an Info.plist with the given mtime."""
        path = self.tmp / name
        path.mkdir(parents=True)
        (path / "fixture.json").write_text(json.dumps(xcresult_json(cases)))
        (path / "Info.plist").write_text("<plist/>")
        if when:
            os.utime(path / "Info.plist", (when, when))
        return path

    # -- running
    def run_engine(self, project, *args, xcode=None, swift=None, docker=None):
        self.fake.master = project.master
        if docker is not None:
            self.fake.docker = list(docker)
        if xcode is not None:
            self.fake.xcodebuild = list(xcode)
        if swift is not None:
            self.fake.swift = list(swift)
        out, err = io.StringIO(), io.StringIO()
        code, interrupted = None, False
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), real_clock_forbidden():
            try:
                code = check_flows.main(["--project", str(project.root)] + list(args), runtime=self.fake)
            except SystemExit as exc:
                if isinstance(exc.code, int):
                    code = exc.code
                else:
                    code = 0 if exc.code is None else 1
                    err.write(str(exc.code))
            except KeyboardInterrupt:
                interrupted = True
        return Result(code, out.getvalue(), err.getvalue(), interrupted)

    # -- reading
    def status(self, project, fid):
        return norm(project.state()["flows"][fid]["status"])

    def note(self, project, fid):
        return project.state()["flows"][fid].get("note") or ""

    def assertStatus(self, project, fid, expected, message):
        self.assertEqual(self.status(project, fid), expected, f"{fid}: {message}")

    def assertUntouched(self, project, before, message):
        self.assertEqual(project.snapshot(), before, message)

    def planned(self, project):
        return set(project.state()["run"]["classes_planned"])
