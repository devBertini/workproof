#!/usr/bin/env python
"""Regenerates every file under scripts/flow_fixtures except itself.

Run `python -B regenerate_fixtures.py` after changing a fixture source: the static states hold the sha1
fingerprints of the files their Entry cells cite, and `test_fixture_fingerprints_match_their_sources` fails when
they drift. The English tree is `project_tree`; `pt_tree` holds the same Demo project as a Brazilian Portuguese
map (docs/FLUXOS.md and its state). Python 3.10, standard library only.
"""
import hashlib
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent
TREE = OUT / "project_tree"
PT_TREE = OUT / "pt_tree"

def write(rel, text, binary=False, tree=None):
    path = (tree or TREE) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if binary:
        path.write_bytes(text)
    else:
        path.write_text(text)


def fp(project, *names):
    root = TREE / project
    hasher = hashlib.sha1()
    for name in sorted(set(names)):
        found = sorted(p for p in root.rglob(name) if ".build" not in p.parts)
        hasher.update(found[0].read_bytes())
    return hasher.hexdigest()


# ------------------------------------------------------------------ Demo (iOS app)

D = "Demo"
write(f"{D}/scripts/flows.json", json.dumps({
    "project": "Demo", "platform": "ios", "device_kind": "phone",
    "devices": {"phone": "Demo-iPhone-abc123", "ipad": "Demo-iPad-abc123"},
    "device_classes": {"IPadLayoutUITests": "ipad"},
    "unit_targets": [{"name": "DemoTests", "dir": "DemoTests"}],
    "ui_targets": [{"name": "DemoUITests", "dir": "DemoUITests"}],
    "sources": ["Demo", "DemoTests", "DemoUITests"],
    "source_patterns": ["*.swift", "*.xcstrings"],
}, indent=2) + "\n")

write(f"{D}/Demo/Home/HomeView.swift",
      "import SwiftUI\n\nstruct HomeView: View {\n    var body: some View { Text(\"Home\") }\n}\n")
write(f"{D}/Demo/Core/Rules.swift",
      "// Demo business rules.\nstruct Rules {\n    static func total(_ items: [Int]) -> Int { items.reduce(0, +) }\n}\n")
write(f"{D}/Demo/Localizable.xcstrings",
      json.dumps({"sourceLanguage": "en", "strings": {"Home": {}}, "version": "1.0"}, indent=2) + "\n")
write(f"{D}/DemoTests/RulesTests.swift",
      "import XCTest\n\nfinal class RulesTests: XCTestCase {\n    func testA() {}\n    func testB() {}\n}\n")
write(f"{D}/DemoTests/Pricing/PricingTests.swift", '''import Testing

@Suite("Pricing rules")
struct PricingTests {
    @Test("rounds half up") func roundsHalfUp() {}
    @Test func loads() {}
}

@Suite("Tagged", .tags(.fast))
nonisolated struct TaggedTests {
    @Test("tag sentence") func tagCase() {}
}

@Suite("Enum rules")
enum EnumRulesTests {
    @Test("enum sentence") static func enumCase() {}
}

@Suite("Actor rules")
actor ActorRulesTests {
    @Test("actor sentence") func actorCase() {}
}

extension PricingTests {
    @Test("extension sentence") func extensionCase() {}
}
''')
write(f"{D}/DemoTests/Suites/SuiteTests.swift", '''import Testing

@Suite("SuiteA") struct SuiteATests { @Test("loads") func loads() {} }
@Suite("SuiteB") struct SuiteBTests { @Test("loads") func loads() {} }
@Suite("RightSuite") struct RightSuiteTests { @Test("loads") func loads() {} }
''')
write(f"{D}/DemoUITests/HomeUITests.swift",
      "import XCTest\n\nfinal class HomeUITests: XCTestCase {\n    func testOpensDetail() {}\n    func testSaves() {}\n}\n")
write(f"{D}/DemoUITests/IPadLayoutUITests.swift",
      "import XCTest\n\nfinal class IPadLayoutUITests: XCTestCase {\n    func testLayout() {}\n}\n")
import struct
import zlib


def png_1x1():
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


PNG = png_1x1()
write(f"{D}/docs/prints/home.png", PNG, binary=True)
write(f"{D}/docs/prints/late.png", PNG, binary=True)

HEADER = "| ID | Flow | Entry | Proof | Status | Validated on |\n|---|---|---|---|---|---|\n"
PLACAR = ("<!-- scoreboard:start -->\n| Total | validated | revalidate | no proof | device only | broken |\n"
          "|---|---|---|---|---|---|\n| **0** | **0** (0%) | 0 | 0 | 0 | 0 |\n\n"
          "*Recomputed on 2026-01-01 by `check-flows`. This scoreboard is not written by hand.*\n"
          "<!-- scoreboard:end -->\n")

# The same text in Brazilian Portuguese: the header row, the scoreboard block and the status cell of every row.
HEADER_PT = "| ID | Fluxo | Entrada | Prova | Status | Validado em |\n|---|---|---|---|---|---|\n"
PLACAR_PT = ("<!-- placar:inicio -->\n| Total | validados | revalidar | sem prova | so em aparelho | quebrados |\n"
             "|---|---|---|---|---|---|\n| **0** | **0** (0%) | 0 | 0 | 0 | 0 |\n\n"
             "*Recalculado em 2026-01-01 por `scripts/check_flows.py`. Este placar não é escrito à mão.*\n"
             "<!-- placar:fim -->\n")
PT_WORDS = {"validated": "validado", "revalidate": "revalidar", "no proof": "sem prova", "device only": "so em aparelho"}
PT_STORED = dict(PT_WORDS, **{"device only": "só em aparelho"})


def to_pt(text):
    text = text.replace(HEADER, HEADER_PT).replace(PLACAR, PLACAR_PT)
    out = []
    for line in text.split("\n"):
        cells = line.split("|")
        if line.startswith("| F-") and len(cells) >= 7 and cells[5].strip() in PT_WORDS:
            cells[5] = " " + PT_WORDS[cells[5].strip()] + " "
            line = "|".join(cells)
        out.append(line)
    return "\n".join(out)

# id, name, entry, proof, typed status, typed date, stored state (status, validated_at, run_id) or None
RULES_FP = "Rules.swift"
STATIC = [
    ("F-RULE-01", "Rules apply", "Rules.swift:10", "RulesTests/testA()<br>RulesTests/testB()", "validated", "2026-01-10", ("validated", "2026-01-10", "20260110T090000-only-stale", "Rules.swift")),
    ("F-PRICE-01", "Pricing rounds", "Rules.swift", "Pricing rules/rounds half up", "validated", "2026-01-10", ("validated", "2026-01-10", "20260110T090000-only-stale", "Rules.swift")),
    ("F-BARE-01", "Bare name proof", "Rules.swift", "tag sentence", "validated", "2026-01-10", ("validated", "2026-01-10", "20260110T090000-only-stale", "Rules.swift")),
    ("F-HOME-01", "Home opens detail", "HomeView.swift:5", "HomeUITests/testOpensDetail()", "revalidate", "2026-01-10", ("revalidate", "2026-01-10", "20260110T090000-only-stale", "STALE")),
    ("F-MIX-01", "Mixed unit and UI", "HomeView.swift<br>Rules.swift", "RulesTests/testA()<br>HomeUITests/testSaves()", "revalidate", "2026-01-10", ("revalidate", "2026-01-10", "20260110T090000-only-stale", "STALE")),
    ("F-PRINT-01", "Print present", "HomeView.swift", "print:docs/prints/home.png", "validated", "2026-02-01", ("validated", "2026-02-01", "no-run", "HomeView.swift")),
    ("F-PRINT-02", "Print missing", "HomeView.swift", "print:docs/prints/missing.png", "no proof", "—", None),
    ("F-MAN-01", "Manual valid", "Rules.swift", "manual:2026-02-15:tester", "validated", "2026-02-15", ("validated", "2026-02-15", "no-run", "Rules.swift")),
    ("F-MAN-02", "Manual expired", "Rules.swift", "manual:2025-10-01:tester", "no proof", "—", None),
    ("F-MAN-03", "Manual bad date", "Rules.swift", "manual:2026-13-01:tester", "no proof", "—", None),
    ("F-MAN-04", "Manual in the future", "Rules.swift", "manual:2026-04-15:tester", "no proof", "—", None),
    ("F-DEV-01", "Device short reason", "", "device:camera", "no proof", "—", None),
    ("F-DEV-02", "Device beside a test", "", "device:real camera capture of a receipt<br>RulesTests/testA()", "no proof", "—", None),
    ("F-DEV-03", "Device good reason", "", "device:real camera capture of a receipt", "device only", "—", ("device only", None, None, None)),
    ("F-NOENT-01", "Empty Entry cell", "", "RulesTests/testB()", "validated", "2026-01-10", ("validated", "2026-01-10", "20260110T090000-only-stale", None)),
    ("F-NOPRF-01", "Empty Proof cell", "Rules.swift", "", "no proof", "—", None),
    ("F-TYPED-01", "Typed validated, state says revalidate", "Rules.swift", "Pricing rules/loads()", "validated", "2026-02-02", ("revalidate", "2026-01-15", "20260115T090000-only-stale", "STALE")),
    ("F-CAT-01", "Catalog cited", "Localizable.xcstrings", "Enum rules/enum sentence", "validated", "2026-01-10", ("validated", "2026-01-10", "20260110T090000-only-stale", "Localizable.xcstrings")),
    ("F-GONE-01", "Cites a file not on disk", "Gone.swift:3", "Actor rules/actor sentence", "revalidate", "2026-01-10", ("revalidate", "2026-01-10", "20260110T090000-only-stale", "STALE")),
]
rows = "".join(f"| {i} | {n} | {e} | {p} | {s} | {d} |\n" for i, n, e, p, s, d, _ in STATIC)
DEMO_MAP = ("# Demo flow map\n\nA made-up app for the engine's tests.\n\n" + PLACAR + "\n---\n\n" + HEADER + rows
            + "\nEnd of the map.\n")
write(f"{D}/docs/FLOWS.md", DEMO_MAP)
write(f"{D}/docs/FLOWS-a11y.md", "# Demo flow map\n\n" + PLACAR + "\n" + HEADER
      + "| F-ACC-01 | Accessible flow | Rules.swift | RulesTests/testA() | no proof | — |\n"
      + "| F-A11Y-01 | Digit in the middle of the ID | Rules.swift | RulesTests/testB() | no proof | — |\n\nEnd.\n")
write(f"{D}/docs/FLOWS-dupe.md", "# Demo flow map\n\n" + PLACAR + "\n" + HEADER
      + "| F-HOME-01 | Home opens detail | HomeView.swift | HomeUITests/testOpensDetail() | no proof | — |\n"
      + "| F-HOME-01 | Same ID again | HomeView.swift | HomeUITests/testSaves() | no proof | — |\n\nEnd.\n")

RUN1, RUN_OLD = "20260110T090000-only-stale", "20260101T090000-only-stale"
KNOWN = {
    "RulesTests/testA()": ("DemoTests/RulesTests", "RulesTests/testA()", RUN1),
    "RulesTests/testB()": ("DemoTests/RulesTests", "RulesTests/testB()", RUN1),
    "Pricing rules/rounds half up": ("DemoTests/PricingTests", "PricingTests/roundsHalfUp()", RUN1),
    "Pricing rules/loads()": ("DemoTests/PricingTests", None, RUN_OLD),  # legacy entry: no stored identifier
    "Tagged/tag sentence": ("DemoTests/TaggedTests", "TaggedTests/tagCase()", RUN1),
    "Enum rules/enum sentence": ("DemoTests/EnumRulesTests", "EnumRulesTests/enumCase()", RUN1),
    "Actor rules/actor sentence": ("DemoTests/ActorRulesTests", "ActorRulesTests/actorCase()", RUN1),
    "RulesTests/testRemoved()": ("DemoTests/RulesTests", "RulesTests/testRemoved()", RUN_OLD),  # source no longer declares it
    "HomeUITests/testOpensDetail()": ("DemoUITests/HomeUITests", "HomeUITests/testOpensDetail()", RUN_OLD),
    "HomeUITests/testSaves()": ("DemoUITests/HomeUITests", "HomeUITests/testSaves()", RUN_OLD),
}
by_path, runnable = {}, {}
for path, (cls, ident, run) in KNOWN.items():
    entry = {"result": "Passed", "when": run[:4] + "-" + run[4:6] + "-" + run[6:8], "run_id": run}
    if ident:
        entry["identifier"] = ident
    by_path[path] = entry
    runnable[path] = cls
by_name = {path.split("/", 1)[1]: dict(entry) for path, entry in by_path.items()}

flows = {}
for fid, _, entry, proof_cell, _, _, st in STATIC:
    if st is None:
        continue
    status, when, run, names = st
    proofs = [p for p in proof_cell.split("<br>") if p]
    rec = {"status": status, "proofs": proofs, "note": "", "source_missing": []}
    if when:
        rec["validated_at"] = when
    if run:
        rec["run_id"] = run
    if names == "STALE":
        rec["source_fingerprint"] = "0" * 40
    elif names:
        rec["source_fingerprint"] = fp("Demo", names)
    if fid == "F-GONE-01":
        rec["source_missing"] = ["Gone.swift"]
        rec["note"] = "file not found: Gone.swift"
    if fid == "F-CAT-01":
        rec["source_fingerprint"] = fp("Demo", "Localizable.xcstrings")
    flows[fid] = rec
flows["F-OLD-09"] = {"status": "validated", "proofs": ["OldTests/testGone()"], "note": "", "validated_at": "2025-12-01",
                     "run_id": "20251201T090000-full", "source_fingerprint": "f" * 40, "source_missing": []}
state = {
    "version": 2, "flows": flows,
    "raw": {"by_path": by_path, "by_name": by_name, "runnable": runnable},
    "run": {"run_id": RUN1, "mode": "only-stale", "classes_planned": ["DemoTests/RulesTests"],
            "classes_done": ["DemoTests/RulesTests"], "scope_ids": ["F-RULE-01"], "include_ui": True,
            "cases_by_class": {}, "started_at": "2026-01-10T09:00:00", "completed": True},
}
write(f"{D}/docs/.flows-state.json", json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))

# The same Demo map and state as a Brazilian Portuguese project keeps them: the statuses are stored in Portuguese.
pt_state = json.loads(json.dumps(state))
for rec in pt_state["flows"].values():
    rec["status"] = PT_STORED[rec["status"]]
write(f"{D}/docs/FLUXOS.md", to_pt(DEMO_MAP), tree=PT_TREE)
write(f"{D}/docs/.flows-state.json", json.dumps(pt_state, ensure_ascii=False, indent=1, sort_keys=True), tree=PT_TREE)

# ------------------------------------------------------------------ DemoGame (iOS game with a Physics package)

G = "DemoGame"
write(f"{G}/scripts/flows.json", json.dumps({
    "project": "DemoGame", "platform": "ios", "device_kind": "phone", "device_classes": {},
    "devices": {"phone": "Demo-iPhone-abc123"},
    "unit_targets": [{"name": "DemoGameTests", "dir": "DemoGameTests"}],
    "ui_targets": [{"name": "DemoGameUITests", "dir": "DemoGameUITests"}],
    "packages": [{"target": "PhysicsTests", "dir": "Packages/Physics", "tests_dir": "Packages/Physics/Tests",
                  "runner": "xcodebuild", "scheme": "Physics"}],
    "sources": ["DemoGame", "DemoGameTests", "DemoGameUITests", "Packages/Physics/Sources", "Packages/Physics/Tests",
                "Packages/Art/Sources"],
    "source_patterns": ["*.swift", "*.xcstrings", "*.json", "*.caf"],
}, indent=2) + "\n")
write(f"{G}/DemoGame/Data/tables.json", '{"cost": [1, 2, 4]}\n')
write(f"{G}/DemoGame/Sounds/tap.caf", b"caff\x00\x01\x00\x00tap", binary=True)
write(f"{G}/DemoGame/Home/Menu-View.swift", "import SwiftUI\n\nstruct MenuView: View {\n    var body: some View { Text(\"Menu\") }\n}\n")
write(f"{G}/DemoGameTests/MenuTests.swift", "import XCTest\n\nfinal class MenuTests: XCTestCase {\n    func testOpens() {}\n}\n")
write(f"{G}/DemoGameUITests/MenuUITests.swift", "import XCTest\n\nfinal class MenuUITests: XCTestCase {\n    func testTap() {}\n}\n")
write(f"{G}/Packages/Physics/Sources/Physics/Config.swift", "// The Physics package's configuration.\npublic struct Config { public static let levels = 10 }\n")
write(f"{G}/Packages/Art/Sources/Art/Config.swift", "// The Art package reuses the basename Config.swift.\nstruct ArtConfig { static let tint = 1 }\n")
write(f"{G}/Packages/Physics/Tests/PhysicsTests/EconomyTests.swift", '''import Testing

@Suite("Economy")
struct EconomyTests {
    @Test("cost doubles per level") func costDoubles() {}
    @Test func testCost() {}
}
''')
write(f"{G}/docs/FLOWS.md", "# DemoGame flow map\n\n" + PLACAR + "\n" + HEADER
      + "| F-MENU-01 | Menu opens | Menu-View.swift | MenuTests/testOpens() | no proof | — |\n"
      + "| F-ECO-01 | Cost doubles | Config.swift | Economy/cost doubles per level | no proof | — |\n"
      + "| F-ECO-02 | Cost by test name | Config.swift | EconomyTests/testCost() | no proof | — |\n"
      + "| F-TAB-01 | Tables | tables.json | MenuTests/testOpens() | no proof | — |\n"
      + "| F-TAP-01 | Tap sound | tap.caf | MenuTests/testOpens() | no proof | — |\n"
      + "| F-UI-01 | Tap in the menu | Menu-View.swift | MenuUITests/testTap() | no proof | — |\n\nEnd.\n")

# ------------------------------------------------------------------ DemoMac (macOS app with a swift-test package)

M = "DemoMac"
write(f"{M}/scripts/flows.json", json.dumps({
    "project": "DemoMac", "platform": "macos", "ui_needs_flag": True,
    "unit_targets": [{"name": "DemoMacTests", "dir": "DemoMacTests"}],
    "ui_targets": [{"name": "DemoMacUITests", "dir": "DemoMacUITests"}],
    "packages": [{"target": "DemoCoreTests", "dir": "Packages/DemoCore", "tests_dir": "Packages/DemoCore/Tests",
                  "runner": "swift-test"}],
    "sources": ["DemoMac", "DemoMacTests", "DemoMacUITests", {"dir": "Packages", "patterns": ["*.swift"]}],
    "source_patterns": ["*.swift", "*.xcstrings"],
}, indent=2) + "\n")
write(f"{M}/DemoMac/MacRules.swift", "struct MacRules { static let width = 400 }\n")
write(f"{M}/DemoMacTests/MacRulesTests.swift", "import XCTest\n\nfinal class MacRulesTests: XCTestCase {\n    func testMacRule() {}\n}\n")
write(f"{M}/DemoMacUITests/MacUITests.swift", "import XCTest\n\nfinal class MacUITests: XCTestCase {\n    func testWindowOpens() {}\n}\n")
write(f"{M}/Packages/DemoCore/Sources/DemoCore/Core.swift", "public struct Core { public static let version = 1 }\n")
write(f"{M}/Packages/DemoCore/Tests/DemoCoreTests/CoreTests.swift", '''import XCTest
import Testing

final class CoreRulesTests: XCTestCase {
    func testRule() {}
    func testBroken() {}
}

@Suite("Engine")
struct EngineTests {
    @Test func engineRuns() {}
    @Test func engineFails() {}
}
''')
write(f"{M}/docs/FLOWS.md", "# DemoMac flow map\n\n" + PLACAR + "\n" + HEADER
      + "| F-MAC-01 | Mac rule | MacRules.swift | MacRulesTests/testMacRule() | no proof | — |\n"
      + "| F-CORE-01 | Core rule | Core.swift | DemoCoreTests/CoreRulesTests/testRule | no proof | — |\n\nEnd.\n")

# ------------------------------------------------------------------ recorded tool output

def case(target, suite, name, ident, result="Passed"):
    return {"target": target, "suite": suite, "name": name, "ident": ident, "result": result}


def xcresult(cases):
    plan = {"name": "Demo", "nodeType": "Test Plan", "result": "Passed", "children": []}
    bundles = {}
    for c in cases:
        bundle = bundles.get(c["target"])
        if bundle is None:
            kind = "UI test bundle" if c["target"].endswith("UITests") else "Unit test bundle"
            bundle = {"name": c["target"], "nodeType": kind, "result": "Passed",
                      "nodeIdentifierURL": f"test://com.apple.xcode/Demo/{c['target']}", "children": []}
            bundles[c["target"]] = bundle
            plan["children"].append(bundle)
        suite = next((s for s in bundle["children"] if s["name"] == c["suite"]), None)
        if suite is None:
            suite = {"name": c["suite"], "nodeType": "Test Suite", "result": "Passed",
                     "nodeIdentifierURL": f"test://com.apple.xcode/Demo/{c['target']}/{c['suite']}", "children": []}
            bundle["children"].append(suite)
        suite["children"].append({
            "name": c["name"], "nodeIdentifier": c["ident"], "nodeType": "Test Case", "result": c["result"],
            "nodeIdentifierURL": f"test://com.apple.xcode/Demo/{c['target']}/{c['ident']}",
            "duration": "0,01s", "durationInSeconds": 0.01})
    return {"devices": [], "testNodes": [plan], "testPlanConfigurations": []}


U = "DemoTests"
UNIT = [
    case(U, "RulesTests", "testA()", "RulesTests/testA()"),
    case(U, "RulesTests", "testB()", "RulesTests/testB()"),
    case(U, "Pricing rules", "rounds half up", "PricingTests/roundsHalfUp()"),
    case(U, "Pricing rules", "loads()", "PricingTests/loads()"),
    case(U, "Pricing rules", "extension sentence", "PricingTests/extensionCase()"),
    case(U, "Tagged", "tag sentence", "TaggedTests/tagCase()"),
    case(U, "Enum rules", "enum sentence", "EnumRulesTests/enumCase()"),
    case(U, "Actor rules", "actor sentence", "ActorRulesTests/actorCase()"),
    case(U, "SuiteA", "loads", "SuiteATests/loads()"),
    case(U, "SuiteB", "loads", "SuiteBTests/loads()"),
    case(U, "RightSuite", "loads", "RightSuiteTests/loads()"),
]
UI = "DemoUITests"
HOME = [case(UI, "HomeUITests", "testOpensDetail()", "HomeUITests/testOpensDetail()"),
        case(UI, "HomeUITests", "testSaves()", "HomeUITests/testSaves()")]
IPAD = [case(UI, "IPadLayoutUITests", "testLayout()", "IPadLayoutUITests/testLayout()")]


def with_result(cases, ident, result):
    return [dict(c, result=result) if c["ident"] == ident else c for c in cases]


RESULTS = OUT / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
scenarios = {
    "unit_all_pass": xcresult(UNIT),
    "unit_one_failed": xcresult(with_result(UNIT, "RulesTests/testB()", "Failed")),
    "unit_skipped": xcresult(with_result(UNIT, "RulesTests/testB()", "Skipped")),
    "unit_renamed": xcresult([c for c in UNIT if c["suite"] != "RulesTests"]
                             + [case(U, "RulesTests", "testA2()", "RulesTests/testA2()"),
                                case(U, "RulesTests", "testB()", "RulesTests/testB()")]),
    "ui_cases": xcresult(HOME[:1]),
    "ipad_pass": xcresult(IPAD),
    "ambiguous_a": xcresult([case(U, "SuiteA", "loads", "SuiteATests/loads()")]),
    "ambiguous_b": xcresult([case(U, "SuiteB", "loads", "SuiteBTests/loads()")]),
    "wrong_suite": xcresult([case(U, "RightSuite", "loads", "RightSuiteTests/loads()"),
                             case(U, "Pricing rules", "loads()", "PricingTests/loads()")]),
    "physics_pass": xcresult([case("PhysicsTests", "Economy", "cost doubles per level", "EconomyTests/costDoubles()"),
                            case("PhysicsTests", "Economy", "testCost()", "EconomyTests/testCost()")]),
    "empty": {"devices": [], "testNodes": [{"name": "Demo", "nodeType": "Test Plan", "result": "Passed", "children": []}],
              "testPlanConfigurations": []},
    "partial": xcresult([case(U, "RulesTests", "testA()", "RulesTests/testA()")]),
}
for name, data in scenarios.items():
    (RESULTS / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")

XU = OUT / "xunit"
XU.mkdir(parents=True, exist_ok=True)
(XU / "core.xunit.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
<testsuite name="DemoCoreTests.xctest" errors="0" tests="2" failures="1" skipped="0" time="0.01">
<testcase classname="DemoCoreTests.CoreRulesTests" name="testRule" time="0.001">
</testcase>
<testcase classname="DemoCoreTests.CoreRulesTests" name="testBroken" time="0.001">
<failure message="XCTAssertEqual failed: (1) is not equal to (2)">
</failure>
</testcase>
</testsuite>
</testsuites>
''')
(XU / "core.xunit-swift-testing.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
<testsuite name="Swift Testing Results" errors="0" tests="2" failures="1" skipped="0" time="0.01">
<testcase classname="DemoCoreTests.EngineTests" name="engineRuns()" time="0.001"/>
<testcase classname="DemoCoreTests.EngineTests" name="engineFails()" time="0.001">
<failure message="Expectation failed: (a == b)"/>
</testcase>
</testsuite>
</testsuites>
''')
(XU / "stonly.xunit-swift-testing.xml").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
<testsuite name="Swift Testing Results" errors="0" tests="1" failures="0" skipped="0" time="0.01">
<testcase classname="DemoCoreTests.EngineTests" name="engineRuns()" time="0.001"/>
</testsuite>
</testsuites>
''')

DEVICE_TYPES = {
    "Demo-iPhone-abc123": ("11111111-1111-4111-8111-111111111111", "iPhone-17-Pro-Max"),
    "Demo-iPhone-def456": ("22222222-2222-4222-8222-222222222222", "iPhone-17-Pro-Max"),
    "Demo-iPhone-17e-abc123": ("33333333-3333-4333-8333-333333333333", "iPhone-17e"),
    "Demo-iPad-abc123": ("44444444-4444-4444-8444-444444444444", "iPad-Pro-13-inch-M5-12GB"),
}
(OUT / "simctl_devices.json").write_text(json.dumps({"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-27-0": [
    {"name": name, "udid": udid, "isAvailable": True, "state": "Shutdown",
     "deviceTypeIdentifier": f"com.apple.CoreSimulator.SimDeviceType.{kind}"}
    for name, (udid, kind) in DEVICE_TYPES.items()]}}, indent=1) + "\n")
print("fixtures written under", OUT)

# ------------------------------------------------------------------ DemoSystem (a docker project: Vitest and Playwright)

S = "DemoSystem"
COMPOSE = ["docker", "compose", "--profile", "test", "run", "--rm"]
write(f"{S}/scripts/flows.json", json.dumps({
    "project": "DemoSystem", "platform": "docker",
    "runners": [
        {"name": "api", "framework": "vitest", "command": COMPOSE + ["api-test"], "junit_dir": "test-results/api",
         "tests": [{"dir": "apps/api/test/unit", "patterns": ["*.spec.ts"]},
                   {"dir": "apps/api/test/integration", "patterns": ["*.int.spec.ts"]}]},
        {"name": "web", "framework": "vitest", "command": COMPOSE + ["web-test"], "junit_dir": "test-results/web",
         "tests": [{"dir": "apps/web/test", "patterns": ["*.unit.ts", "*.unit.tsx"]}]},
        {"name": "e2e", "framework": "playwright", "ui": True, "command": COMPOSE + ["e2e"],
         "junit_dir": "test-results/e2e", "tests": [{"dir": "apps/e2e/tests", "patterns": ["*.e2e.ts"]}]},
    ],
    "sources": ["apps", "packages"],
    "source_patterns": ["*.ts", "*.tsx", "*.json"],
}, indent=2) + "\n")
write(f"{S}/apps/api/src/crypto/crypto.service.ts",
      "export class CryptoService {\n  encrypt(plain: string): string { return plain.split('').reverse().join(''); }\n}\n")
write(f"{S}/apps/api/src/gateway/gateway.service.ts",
      "export class GatewayService {\n  limit(): number { return 60; }\n}\n")
write(f"{S}/apps/web/src/app/(dashboard)/providers/page.tsx",
      "export default function ProvidersPage() { return null; }\n")
write(f"{S}/apps/web/src/app/(dashboard)/systems/page.tsx",
      "export default function SystemsPage() { return null; }\n")
write(f"{S}/apps/web/messages/en.json", '{"providers": "Providers"}\n')
write(f"{S}/packages/shared/src/settings.ts", "export const EFFORTS = ['low', 'medium', 'high'] as const;\n")
write(f"{S}/apps/api/test/unit/crypto.service.spec.ts", '''import { describe, it, expect } from 'vitest';

// describe('commented out', () => { it('ghost', () => {}); });
/* it('also a ghost', () => {}) */
describe('CryptoService', () => {
  it('encrypts and decrypts a key', () => {
    expect({ a: 1 }).toEqual({ a: 1 }); // braces ) ( in code
  });

  describe('with a wrong master key', () => {
    it('fails to decrypt', () => {
      const text = "it('inside a string', () => {})";
      expect(/it\\('x'\\)/.test(text)).toBe(false);
    });
    it.skip('is slow', () => {});
  });

  it.todo('rotates the master key');
  it(`reads a template without an expression`, () => {});
});

it('top level case', () => {});
''')
write(f"{S}/apps/api/test/unit/helpers.ts", "// not a test file: its name matches no pattern\nexport const fake = () => { it('from a helper', () => {}); };\n")
write(f"{S}/apps/api/test/unit/settings.spec.ts", '''import { describe, it } from 'vitest';

describe('settings', () => {
  it('parses effort', () => {});
  it.each([1, 2])('case %i', () => {});
});
''')
write(f"{S}/apps/api/test/integration/gateway.int.spec.ts", '''import { describe, it } from 'vitest';

describe('gateway', () => {
  it('returns 429 with Retry-After when the rate limit is exceeded', async () => {});
  it('refuses a request with tools', async () => {});
});
''')
write(f"{S}/apps/web/test/catalog.unit.ts", '''import { describe, it } from 'vitest';

describe('catalog parity', () => {
  it('both languages have the same keys', () => {});
});
''')
write(f"{S}/apps/e2e/tests/providers.e2e.ts", '''import { test, expect } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

test.describe('providers', () => {
  test('creates a provider', async ({ page }) => {
    await test.step('open the form', async () => {});
  });
  test('syncs the catalog', async ({ page }) => {});
});
''')
write(f"{S}/docs/FLOWS.md", "# DemoSystem flow map\n\nA made-up docker system for the engine's tests.\n\n" + PLACAR + "\n" + HEADER
      + "| F-CRY-01 | A key is encrypted | crypto.service.ts | crypto.service.spec/CryptoService > encrypts and decrypts a key | no proof | — |\n"
      + "| F-GAT-01 | The gateway limits calls | gateway.service.ts | gateway.int.spec/gateway > returns 429 with Retry-After when the rate limit is exceeded | no proof | — |\n"
      + "| F-PRV-01 | A provider is created | apps/web/src/app/(dashboard)/providers/page.tsx | providers.e2e/providers > creates a provider | no proof | — |\n"
      + "\nEnd.\n")

# JUnit XML in the shapes the reporters write. Vitest: `testsuites > testsuite > testcase`, the file path in
# `classname` and the title path joined by ` > ` in `name`. Playwright: the testsuite named after the file, the
# testcase named after the title path joined by ` \u203a `, its classname the file. Both written here by hand.
JU = OUT / "junit"
JU.mkdir(parents=True, exist_ok=True)
(JU / "vitest-shape.xml").write_text('''<?xml version="1.0" encoding="UTF-8" ?>
<testsuites name="vitest tests" tests="2" failures="1" errors="0" time="0.5">
    <testsuite name="suite/sample.spec.ts" hostname="host" tests="2" failures="1" errors="0" skipped="0" time="0.01">
        <testcase classname="suite/sample.spec.ts" name="sample suite &gt; adds two numbers" time="0.01">
            <failure message="expected 5 to be 4" type="AssertionError">
AssertionError: expected 5 to be 4
            </failure>
        </testcase>
        <testcase classname="suite/sample.spec.ts" name="sample suite &gt; subtracts two numbers" time="0">
        </testcase>
    </testsuite>
</testsuites>
''')
(JU / "playwright-shape.xml").write_text('''<testsuites id="" name="" tests="4" failures="1" skipped="1" errors="0" time="3.2">
<testsuite name="providers.e2e.ts" timestamp="2026-10-08T12:00:00.000Z" hostname="chromium" tests="3" failures="1" skipped="1" time="2.1" errors="0">
<testcase name="providers \u203a creates a provider" classname="providers.e2e.ts" time="1.2">
</testcase>
<testcase name="providers \u203a syncs the catalog" classname="providers.e2e.ts" time="0.9">
<failure message="providers.e2e.ts:9:3 syncs the catalog" type="FAILURE">
<![CDATA[  1) providers.e2e.ts:9:3 \u203a providers \u203a syncs the catalog
     Error: expect(received).toBe(expected)]]>
</failure>
</testcase>
<testcase name="providers \u203a keeps a note" classname="providers.e2e.ts" time="0">
<skipped/>
</testcase>
</testsuite>
<testsuite name="auth/login.e2e.ts" timestamp="2026-10-08T12:00:00.000Z" hostname="chromium" tests="1" failures="0" skipped="0" time="1.1" errors="0">
<testcase name="signs in" classname="auth/login.e2e.ts" time="1.1">
</testcase>
</testsuite>
</testsuites>
''')
