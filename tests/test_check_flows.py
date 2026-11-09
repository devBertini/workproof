#!/usr/bin/env python
"""Tests of the flow-map engine, `check_flows.py` beside this file.

Run: `python -B test_check_flows.py` from this folder (Python 3.10, standard library only).
No simulator, no xcodebuild and no network: every subprocess, the clock and git go through one runtime object that
these tests replace with `FakeRuntime`. The fixtures are in `flow_fixtures/` (`regenerate_fixtures.py` writes them).

Every test is named for the defect or the mode it guards. An expected status is written by hand in each test from
the rules of skill `workproof:mapping-flows`, never copied from the engine's output. Statuses are written in
their canonical English words (`validated`, `revalidate`, `no proof`, `device only`, `broken`); the harness shows
them in the language under test, English by default and Brazilian Portuguese in the `Portuguese...` classes, which
run the same tests on a map written in Portuguese (docs/FLUXOS.md, `validado`, `placar`, ...).

THE CONTRACT THESE TESTS HOLD THE ENGINE TO

1. Entry point. `check_flows.main(argv, runtime=None)` returns the exit code (0, 1 or 2). `argv` starts with
   `--project <dir>` (the project root, the folder that holds `docs/` and `scripts/flows.json`); without it the
   nearest folder above the current one that holds `scripts/flows.json` is the project. No module-level state
   survives between two `main()` calls. A `KeyboardInterrupt` in the runner may propagate, but the state already
   on disk after the last finished batch is the checkpoint.
2. Runtime. The engine calls only `runtime.run(cmd, cwd=None, env=None)`, which returns an object with
   `returncode`, `stdout` and `stderr`, and `runtime.now()`, which returns a `datetime.datetime`. Every
   `xcodebuild`, `swift test`, `xcrun xcresulttool`, `xcrun simctl`, test runner and `git` call goes through
   `run`. The engine never reads the real clock. With `--today D` the date is D and the time of day comes from
   `runtime.now()`. A git failure (rc != 0) means "no files", and "never tracked" for the print: check.
3. Files. `docs/FLOWS.md` (or `docs/FLUXOS.md`, a Portuguese map) and `docs/.flows-state.json` (version 2) under
   the project; `scripts/flows.json`. Home is `$HOME`. The source index skips `.build` and lists the first folder
   first.
4. State. `flows[ID]` has `status`, `proofs`, `note`, `validated_at`, `source_fingerprint`, `source_missing`,
   `run_id`; the status is stored in the map's language (`só em aparelho` keeps its accent). `raw` has `by_path`
   (key `Suite/case`, value `result`, `when`, `run_id` and, when known, `identifier`), `by_name` and `runnable`
   (`Suite/case` to `Target/Class`). `run` has `run_id`, `mode`, `classes_planned`, `classes_done`, `scope_ids`,
   `include_ui`, `cases_by_class`, `completed`.
5. Commands. Every iOS `xcodebuild` command carries `-sdk iphonesimulator` before `-destination`; a Mac command
   has no `-sdk`. The simulator of a device kind is the one `devices` names in `scripts/flows.json`, or `--device`.
5b. Result bundles. Written under `<derived data>/results/` as `flows-<run id>-b<N>-<HHMMSS>.xcresult` (plus
   `-<package>` for a package group). `--result-path P` with one batch writes exactly P, and an existing P
   refuses the batch. A bundle is never removed. The date of a result is today's, except under `--reuse`.
6. Console (plain ASCII, English; status words are the map's): the summary line `flows: N total, V validated, ...`,
   `changed files: N, flows touched: M`, `state entries without a map row: N`, `waiting` lines for a class that
   needs another device kind, `partly`, `no longer in the map`, `clash`, `declared in more than one`, the two
   `uncited` listings and `nothing to resume`: each test names the line it reads.
7. Notes (the state's `note`): a changed source names the file; a lookup miss holds `lookup` and never `failed`;
   the other notes are read by the test that guards them.
8. A docker project (`"platform": "docker"`, fixture `DemoSystem`) has no simulator and no Xcode: each runner is
   one command run in the project folder that writes JUnit XML into the runner's `junit_dir`; every report it
   wrote is copied into a new folder under the results folder and read from the copy. A proof is `<test file
   stem>/<full title>`, the title being the describe chain and the test name joined by ` > `.
9. Languages. The language of a map is read from the map (scoreboard markers, header row, status words of its
   rows, the file name); a map with none of these takes `--language` (English by default); a contradicting
   `--language` is refused with exit 2; `--init` writes a starter map and never overwrites one.
"""
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
from test_support import find_sh, posix

from flow_test_harness import *  # noqa: F401,F403 (the harness names are this file's vocabulary)
from flow_test_harness import _NoNow, _NoToday  # noqa: F401


# ---------------------------------------------------------------------------- tests of the harness itself


class HarnessTests(unittest.TestCase):
    """The fake runtime and the fixtures must be right before any engine test can mean anything."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = pathlib.Path(temp.name).resolve()

    def test_fake_refuses_an_existing_bundle_path(self):
        fake = FakeRuntime()
        fake.master = DEMO_MASTER
        fake.xcodebuild = ["auto", "auto"]
        cmd = ["xcodebuild", "test", "-resultBundlePath", str(self.tmp / "a.xcresult"), "-only-testing:DemoTests/RulesTests"]
        fake.run(cmd)
        with self.assertRaises(AssertionError, msg="a second batch on the same bundle path must be refused"):
            fake.run(cmd)

    def test_fake_auto_bundle_holds_only_the_selected_cases_and_exits_65_on_a_failure(self):
        fake = FakeRuntime()
        fake.master = DEMO_MASTER
        fake.overrides = {("RulesTests", "testB()"): "Failed"}
        fake.xcodebuild = ["auto", "auto"]
        path = self.tmp / "b.xcresult"
        proc = fake.run(["xcodebuild", "test", "-resultBundlePath", str(path), "-only-testing:DemoTests/RulesTests",
                         "-only-testing:DemoUITests/HomeUITests/testSaves"])
        names = sorted(n["nodeIdentifier"] for n in fake._walk_cases(json.loads((path / "fixture.json").read_text())))
        self.assertEqual(names, ["HomeUITests/testSaves()", "RulesTests/testA()", "RulesTests/testB()"])
        self.assertEqual(proc.returncode, 65, "xcodebuild exits 65 with a bundle when a case failed")

    def test_fake_records_an_unexpected_command_then_refuses_it(self):
        fake = FakeRuntime()
        with self.assertRaises(AssertionError):
            fake.run(["rm", "-rf", "x.xcresult"])
        self.assertEqual(fake.calls[0].cmd, ["rm", "-rf", "x.xcresult"], "the call is recorded before it is refused")

    def test_fake_git_and_swift_answers(self):
        fake = FakeRuntime()
        fake.git_tracked = ["docs/prints/a.png"]
        fake.git_untracked = ["new.swift"]
        self.assertEqual(fake.run(["git", "ls-files", "--", "docs/prints/a.png"]).stdout, "docs/prints/a.png\n")
        self.assertEqual(fake.run(["git", "ls-files", "--", "docs/prints/b.png"]).stdout, "")
        self.assertEqual(fake.run(["git", "ls-files", "--others", "--exclude-standard"]).stdout, "new.swift\n")
        fake.swift = ["core"]
        proc = fake.run(["swift", "test", "--package-path", "P", "--xunit-output", str(self.tmp / "r" / "x.xml")])
        self.assertEqual(proc.returncode, 1, "swift test exits 1 when a report holds a failure")
        self.assertTrue((self.tmp / "r" / "x.xml").exists() and (self.tmp / "r" / "x-swift-testing.xml").exists())

    def test_fixture_fingerprints_match_their_sources(self):
        """The static state's fingerprints are the sha1 of the files the static map's Entrada cells cite."""
        project = FIXTURES / "project_tree" / "Demo"
        state = json.loads((project / "docs" / ".flows-state.json").read_text())
        text = (project / "docs" / "FLOWS.md").read_text()
        checked = 0
        for fid, rec in state["flows"].items():
            fp = rec.get("source_fingerprint")
            cells = doc_row(text, fid)
            if cells is None or fp in (None, STALE_FP):
                continue
            names = sorted(set(re.findall(r"[A-Za-z0-9_+-]+\.(?:swift|xcstrings)", cells[2])))
            files = [next(p for p in project.rglob(n)) for n in names]
            self.assertEqual(fp, sha1_of(files), f"{fid}: a fixture source changed without regenerating the state")
            checked += 1
        self.assertGreaterEqual(checked, 5, "the static state must carry fingerprints for the engine to compare")

    def test_result_fixtures_have_the_real_tool_shape_and_match_the_masters(self):
        """testNodes > Test Plan > Unit/UI test bundle > Test Suite > Test Case, in the shape `xcresulttool` prints."""
        names = sorted(p.stem for p in (FIXTURES / "results").glob("*.json"))
        wanted = ["ambiguous_a", "ambiguous_b", "empty", "ipad_pass", "partial", "physics_pass", "ui_cases",
                  "unit_all_pass", "unit_one_failed", "unit_renamed", "unit_skipped", "wrong_suite"]
        self.assertEqual(names, wanted)
        for name in names:
            data = json.loads((FIXTURES / "results" / f"{name}.json").read_text())
            for plan in data["testNodes"]:
                self.assertEqual(plan["nodeType"], "Test Plan", name)
                for bundle in plan.get("children", []):
                    self.assertIn(bundle["nodeType"], ("Unit test bundle", "UI test bundle"), name)
                    for suite in bundle["children"]:
                        self.assertEqual(suite["nodeType"], "Test Suite", name)
                        for case in suite["children"]:
                            self.assertEqual(case["nodeType"], "Test Case", name)
                            self.assertIn("/", case["nodeIdentifier"], name)
                            self.assertIn(case["result"], ("Passed", "Failed", "Skipped"), name)
        full = json.loads((FIXTURES / "results" / "unit_all_pass.json").read_text())
        got = sorted((n["nodeIdentifier"], n["name"], n["result"]) for n in FakeRuntime._walk_cases(full))
        self.assertEqual(got, sorted((c["ident"], c["name"], c["result"]) for c in DEMO_UNIT),
                         "unit_all_pass.json drifted from the master list the auto bundles use")
        empty = json.loads((FIXTURES / "results" / "empty.json").read_text())
        self.assertEqual(list(FakeRuntime._walk_cases(empty)), [], "empty.json must hold no test case")

    def test_the_portuguese_fixture_is_the_english_one_translated(self):
        """pt_tree holds the Demo map and state the way a Portuguese project keeps them: the same flows, the same
        fingerprints, the Portuguese words (the device status stored with its accent)."""
        english = FIXTURES / "project_tree" / "Demo" / "docs"
        portuguese = FIXTURES / "pt_tree" / "Demo" / "docs"
        self.assertEqual((portuguese / "FLUXOS.md").read_text(),
                         to_language((english / "FLOWS.md").read_text(), "pt-BR"))
        self.assertEqual(json.loads((portuguese / ".flows-state.json").read_text()),
                         state_in_language(json.loads((english / ".flows-state.json").read_text()), "pt-BR"))
        self.assertIn("só em aparelho", (portuguese / ".flows-state.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- the engine's tests


class DeviceAndDestinationTests(EngineCase):
    def test_destination_resolves_device_name_to_udid(self):
        """Defect: a destination built by name fails on recent Xcode versions (destination_for)."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "Rules.swift", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale", "--device", "Demo-iPhone-abc123", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        cmds = self.fake.xcode_calls()
        self.assertEqual(len(cmds), 1, "one stale class, one batch")
        self.assertEqual(arg_after(cmds[0].cmd, "-destination"), "platform=iOS Simulator,id=" + UDID_PHONE,
                         "the device name Demo-iPhone-abc123 must be resolved to the UDID the simctl fixture gives it")
        self.fake.reset()
        p2 = self.project(doc=doc, state=make_state())
        r = self.run_engine(p2, "--only-stale", "--device", UDID_PHONE_2, xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(arg_after(self.fake.xcode_calls()[0].cmd, "-destination"),
                         "platform=iOS Simulator,id=" + UDID_PHONE_2, "a 36-character UDID must pass straight through")
        self.assertFalse([a for c in self.fake.calls for a in c.cmd if "name=" in a],
                         "no command may carry a name= destination")

    def test_device_class_runs_in_its_own_kind_pass(self):
        """Defect: a class tied to one destination type marks its flows broken (needs_other_device)."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "", "RulesTests/testA()"),
                       row("F-HOME-01", "Home detail", "", "HomeUITests/testOpensDetail()"),
                       row("F-IPAD-01", "iPad layout", "", "IPadLayoutUITests/testLayout()"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale", xcode=["auto"] * 3)
        self.assertEqual(r.code, 0, r.text)
        ipad = [c for c in self.fake.xcode_calls() if any("IPadLayoutUITests" in t for t in only_testing(c.cmd))]
        self.assertEqual(len(ipad), 1)
        self.assertTrue(all("IPadLayoutUITests" in t for t in only_testing(ipad[0].cmd)),
                        "the iPad-only class runs in a group of its own")
        self.assertEqual(arg_after(ipad[0].cmd, "-destination"), "platform=iOS Simulator,id=" + UDID_IPAD,
                         "the simulator the configuration names for the ipad kind")
        others = [c for c in self.fake.xcode_calls() if c is not ipad[0]]
        self.assertEqual(len(others), 2)
        for c in others:
            self.assertEqual(arg_after(c.cmd, "-destination"), "platform=iOS Simulator,id=" + UDID_PHONE,
                             "the simulator the configuration names for the phone kind")
        self.assertStatus(p, "F-IPAD-01", OK, "ran on the iPad, so validated")
        # with a phone given, the iPad-only flow keeps its status and is listed as waiting
        self.fake.reset()
        flows = {"F-IPAD-01": frec(REV, ["IPadLayoutUITests/testLayout()"], when="2026-01-10", run=RUN1),
                 "F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1)}
        raw = seed("IPadLayoutUITests/testLayout()", "RulesTests/testA()")
        p2 = self.project(doc=doc, state=make_state(flows, raw))
        r = self.run_engine(p2, "--only-stale", "--device", "Demo-iPhone-abc123", xcode=["auto"] * 3)
        self.assertEqual(r.code, 0, r.text)
        self.assertStatus(p2, "F-IPAD-01", REV, "an iPad class that could not run on a phone must keep its status, never broken")
        self.assertFalse([c for c in self.fake.xcode_calls() if any("IPadLayoutUITests" in t for t in only_testing(c.cmd))],
                         "the iPad class must not run on a phone")
        waiting = [l for l in r.out.splitlines() if "waiting" in l and "ipad" in l.lower() and "F-IPAD-01" in l]
        self.assertTrue(waiting, "the output must list F-IPAD-01 as waiting for an ipad pass:\n" + r.out)

    def test_se_device_kind_runs_on_the_simulator_configured_for_it(self):
        """Defect: `device_kind: "se"` is looked up as another kind, or the SE run goes to another device."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "", "RulesTests/testA()"),
                       row("F-HOME-01", "Home detail", "", "HomeUITests/testOpensDetail()"))
        p = self.project(doc=doc, state=make_state(),
                         config={"device_kind": "se", "devices": {"se": DEVICES["se"][0]}})
        r = self.run_engine(p, "--only-stale", xcode=["auto"] * 2)
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(len(self.fake.xcode_calls()), 2)
        for c in self.fake.xcode_calls():
            self.assertEqual(arg_after(c.cmd, "-destination"), "platform=iOS Simulator,id=" + UDID_SE,
                             "every batch of the project's own classes runs on the SE")
        self.assertStatus(p, "F-HOME-01", OK, "ran on the SE")

    def test_end_pass_on_the_se_runs_the_iphone_family_and_holds_the_ipad_class(self):
        """Defect: an SE named by --device counts as an iPad (family_of_device, family), so the iPhone classes wait."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "", "RulesTests/testA()"),
                       row("F-HOME-01", "Home detail", "", "HomeUITests/testOpensDetail()"),
                       row("F-IPAD-01", "iPad layout", "", "IPadLayoutUITests/testLayout()"))
        flows = {"F-IPAD-01": frec(REV, ["IPadLayoutUITests/testLayout()"], when="2026-01-10", run=RUN1),
                 "F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1),
                 "F-HOME-01": frec(REV, ["HomeUITests/testOpensDetail()"], when="2026-01-10", run=RUN1)}
        raw = seed("IPadLayoutUITests/testLayout()", "RulesTests/testA()", "HomeUITests/testOpensDetail()")
        for device in (DEVICES["se"][0], UDID_SE):
            self.fake.reset()
            p = self.project(doc=doc, state=make_state(flows, raw), config={"device_kind": "se"})
            r = self.run_engine(p, "--only-stale", "--device", device, xcode=["auto"] * 3)
            self.assertEqual(r.code, 0, r.text)
            ran = [t.split("/")[1] for c in self.fake.xcode_calls() for t in only_testing(c.cmd)]
            self.assertEqual(sorted(set(ran)), ["HomeUITests", "RulesTests"],
                             f"the iPhone-family classes run on the SE ({device}); the iPad class does not")
            for c in self.fake.xcode_calls():
                self.assertEqual(arg_after(c.cmd, "-destination"), "platform=iOS Simulator,id=" + UDID_SE)
            waiting = [l for l in r.out.splitlines() if "waiting for a ipad pass" in l and "F-IPAD-01" in l]
            self.assertTrue(waiting, "the iPad class is listed as waiting for an ipad pass:\n" + r.out)
            self.assertFalse([l for l in r.out.splitlines() if "waiting for a se pass" in l],
                             "no se-kind class waits on an SE device")
            self.assertStatus(p, "F-IPAD-01", REV, "an iPad class that could not run on the SE keeps its status")
            self.assertStatus(p, "F-HOME-01", OK, "ran on the SE")

    def test_a_device_kind_without_a_configured_simulator_is_refused_with_a_hint(self):
        """The engine owns no simulator tool: a kind the configuration does not name stops the run with exit 2."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state(), config={"devices": DEL})
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertEqual(r.code, 2, r.text)
        self.assertIn("devices", r.err, "the message names the configuration key to add")
        self.assertEqual(self.fake.xcode_calls(), [], "no build starts without a simulator")
        bad = self.project(doc=doc, state=make_state(), config={"devices": {"phone": ""}})
        self.assertEqual(self.run_engine(bad, "--from-cache").code, 2, "an empty simulator name is a configuration error")
        self.assertEqual(self.run_engine(self.project(config={"devices": ["phone"]}), "--from-cache").code, 2)

    def test_a_run_resolves_devices_through_simctl_and_creates_or_shuts_down_nothing(self):
        """The engine only reads the simulator list: no tool boots, creates or shuts a device down."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "", "RulesTests/testA()"),
                       row("F-PRICE-01", "Pricing", "", "Pricing rules/rounds half up"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale", xcode=["auto", "auto"])
        self.assertEqual(r.code, 0, r.text)
        simctl = [c.cmd for c in self.fake.calls if c.cmd[:2] == ["xcrun", "simctl"]]
        self.assertTrue(simctl and all(cmd[2] == "list" for cmd in simctl), simctl)
        self.assertEqual([c for c in self.fake.calls if c.cmd[0] == "bash"], [], "no shell tool is called")
        self.assertEqual(len(simctl), 1, "the list is read once, however many batches")
        # a kill leaves the checkpoint and nothing else to clean
        self.fake.reset()
        p_kill = self.project(doc=doc, state=make_state())
        r = self.run_engine(p_kill, "--only-stale", xcode=["!interrupt"])
        self.assertTrue(r.interrupted)
        self.assertEqual([c for c in self.fake.calls if c.cmd[0] == "bash"], [])
        # nothing to run: no device is looked up
        self.fake.reset()
        flows = {"F-RULE-01": frec(OK, ["RulesTests/testA()"], when="2026-01-10", run=RUN1),
                 "F-PRICE-01": frec(OK, ["Pricing rules/rounds half up"], when="2026-01-10", run=RUN1)}
        p_idle = self.project(doc=doc, state=make_state(flows, seed("RulesTests/testA()", "Pricing rules/rounds half up")))
        self.run_engine(p_idle, "--only-stale")
        self.assertEqual(self.fake.runner_calls(), [], "a run with nothing to run must not look up a device")
        # modes that never run a test
        for mode in (["--from-cache"], ["--check"], ["--invalidate"]):
            self.fake.reset()
            self.run_engine(self.project(), *mode)
            self.assertEqual(self.fake.runner_calls(), [], f"{mode[0]} must not run anything")
        self.fake.reset()
        bundle = self.make_bundle("reuse.xcresult", [tc("DemoTests", "RulesTests", "testA()")], when=ts(2026, 2, 20))
        self.run_engine(self.project(), "--reuse", str(bundle))
        self.assertEqual([c for c in self.fake.calls if c.cmd[0] in ("xcodebuild", "bash")]
                         + [c for c in self.fake.calls if c.cmd[:2] == ["xcrun", "simctl"]], [],
                         "--reuse reads bundles only")

    def test_macos_has_fixed_destination_and_no_device(self):
        """Mode --device on macOS: a fixed destination, no simulator, no --device."""
        doc = doc_text(row("F-MAC-01", "Mac rule", "MacRules.swift", "MacRulesTests/testMacRule()"))
        p = self.project("DemoMac", doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        cmd = self.fake.xcode_calls()[0].cmd
        self.assertEqual(arg_after(cmd, "-destination"), "platform=macOS,arch=arm64")
        self.assertNotIn("-sdk", cmd, "a Mac run has no simulator sdk")
        self.assertEqual(check_flows.mac_arch("x86_64"), "x86_64", "an Intel Mac runs its own architecture")
        self.assertEqual(check_flows.mac_arch("arm64"), "arm64")
        self.fake.machine = lambda: "x86_64"
        self.run_engine(p, "--only-stale", "--invalidate-classes", "MacRulesTests", xcode=["auto"])
        self.assertEqual(arg_after(self.fake.xcode_calls()[-1].cmd, "-destination"), "platform=macOS,arch=x86_64")
        self.assertEqual([c for c in self.fake.calls if "simctl" in c.cmd], [], "a Mac run never calls simctl")
        self.fake.reset()
        before = p.snapshot()
        r = self.run_engine(p, "--only-stale", "--device", "Demo-iPhone-abc123")
        self.assertEqual(r.code, 2, "--device on macOS must be refused with exit 2:\n" + r.text)
        self.assertEqual(self.fake.runner_calls(), [])
        self.assertUntouched(p, before, "a refused --device must not write")

    def test_ios_commands_pass_the_simulator_sdk(self):
        """Defect: an iOS xcodebuild command without -sdk iphonesimulator."""
        runs = [("Demo", doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"),
                                  row("F-HOME-01", "Home", "", "HomeUITests/testSaves()"))),
                ("DemoGame", doc_text(row("F-MENU-01", "Menu", "", "MenuTests/testOpens()"),
                                      row("F-ECO-02", "Cost", "", "EconomyTests/testCost()")))]
        for kind, doc in runs:
            self.fake.reset()
            p = self.project(kind, doc=doc, state=make_state())
            self.run_engine(p, "--only-stale", xcode=["auto"] * 4)
            cmds = self.fake.xcode_calls()
            self.assertGreaterEqual(len(cmds), 2, f"{kind}: the app and the package or UI classes ran")
            for c in cmds:
                self.assertEqual(arg_after(c.cmd, "-sdk"), "iphonesimulator", f"{kind}: {c.cmd}")
                self.assertLess(c.cmd.index("-sdk"), c.cmd.index("-destination"), "the sdk comes before the destination")


class SourceAndFingerprintTests(EngineCase):
    def test_catalog_and_table_change_drops_flow(self):
        """Defect: the fingerprint covers only .swift (SOURCE_PATTERNS)."""
        doc = doc_text(row("F-CAT-01", "Catalog", "Localizable.xcstrings", "RulesTests/testA()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=KEEP)
        p.set_state(make_state({"F-CAT-01": frec(OK, ["RulesTests/testA()"], fp=p.fp("Localizable.xcstrings"),
                                                 when="2026-01-10", run=RUN1)}, seed("RulesTests/testA()")))
        p.edit("Localizable.xcstrings")
        r = self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-CAT-01", REV, "editing the string catalog must drop a validated app flow")
        self.assertIn("Localizable.xcstrings", self.note(p, "F-CAT-01"), r.text)
        game_doc = doc_text(row("F-TAB-01", "Tables", "tables.json", "MenuTests/testOpens()", OK, "2026-01-10"),
                            row("F-TAP-01", "Tap sound", "tap.caf", "MenuTests/testOpens()", OK, "2026-01-10"))
        g = self.project("DemoGame", doc=game_doc, state=KEEP)
        g.set_state(make_state({
            "F-TAB-01": frec(OK, ["MenuTests/testOpens()"], fp=g.fp("tables.json"), when="2026-01-10", run=RUN1),
            "F-TAP-01": frec(OK, ["MenuTests/testOpens()"], fp=g.fp("tap.caf"), when="2026-01-10", run=RUN1)},
            seed("MenuTests/testOpens()")))
        g.edit("tables.json")
        g.edit("tap.caf")
        r = self.run_engine(g, "--from-cache")
        self.assertStatus(g, "F-TAB-01", REV, "a JSON table cited in Entrada is part of the fingerprint")
        self.assertStatus(g, "F-TAP-01", REV, "a .caf sound cited in Entrada is part of the fingerprint")
        self.assertIn("tables.json", self.note(g, "F-TAB-01"))
        self.assertIn("tap.caf", self.note(g, "F-TAP-01"))

    def test_changed_source_drops_flow_and_names_file(self):
        """Status rule: a changed source drops the flow to revalidar and names the file; the old date stays."""
        doc = doc_text(row("F-RULE-01", "Rules apply", "Rules.swift:10", "RulesTests/testA()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=KEEP)
        p.set_state(make_state({"F-RULE-01": frec(OK, ["RulesTests/testA()"], fp=p.fp("Rules.swift"),
                                                  when="2026-01-10", run=RUN1)}, seed("RulesTests/testA()")))
        p.edit("Rules.swift")
        r = self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-RULE-01", REV, "an edited Entrada file must drop the validated flow")
        self.assertIn("Rules.swift", self.note(p, "F-RULE-01"), "the state's note must name the file")
        self.assertTrue([l for l in r.out.splitlines() if "F-RULE-01" in l and "Rules.swift" in l],
                        "the console must name the flow and the file:\n" + r.out)
        self.assertEqual(p.state()["flows"]["F-RULE-01"]["validated_at"], "2026-01-10", "the reference date is kept")
        self.assertEqual(doc_row(p.doc(), "F-RULE-01")[4:], [shown(REV), "2026-01-10"])

    def test_print_and_manual_flows_drop_after_source_change(self):
        """Defect: a screenshot or manual proof rewrites the fingerprint every round (free_proofs_older_than_change)."""
        doc = doc_text(
            row("F-PRINT-01", "Print", "HomeView.swift", "print:docs/prints/home.png", OK, "2026-02-01"),
            row("F-MAN-01", "Manual", "Rules.swift", "manual:2026-02-01:tester", OK, "2026-02-01"),
            row("F-PRINT-03", "Late capture", "Localizable.xcstrings", "print:docs/prints/late.png", OK, "2026-02-01"))
        p = self.project(doc=doc, state=KEEP)
        old = {"home": p.fp("HomeView.swift"), "rules": p.fp("Rules.swift"), "cat": p.fp("Localizable.xcstrings")}
        p.set_state(make_state({
            "F-PRINT-01": frec(OK, ["print:docs/prints/home.png"], fp=old["home"], when="2026-02-01", run="no-run"),
            "F-MAN-01": frec(OK, ["manual:2026-02-01:tester"], fp=old["rules"], when="2026-02-01", run="no-run"),
            "F-PRINT-03": frec(OK, ["print:docs/prints/late.png"], fp=old["cat"], when="2026-02-01", run="no-run")}))
        p.edit("HomeView.swift", when=T_CHANGE)                  # after the capture (2026-02-01)
        p.edit("Rules.swift", when=T_CHANGE)                     # after the manual check (2026-02-01)
        p.edit("Localizable.xcstrings", when=ts(2026, 1, 20))    # before the capture
        r = self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-PRINT-01", REV, "the source changed after the capture")
        self.assertStatus(p, "F-MAN-01", REV, "the source changed after the manual check")
        self.assertEqual(p.state()["flows"]["F-PRINT-01"]["source_fingerprint"], old["home"],
                         "a screenshot proof must not stamp the new fingerprint on a flow it predates")
        self.assertEqual(p.state()["flows"]["F-MAN-01"]["source_fingerprint"], old["rules"],
                         "a manual proof must not stamp the new fingerprint on a flow it predates")
        self.assertStatus(p, "F-PRINT-03", OK, "a capture newer than the change keeps the flow validated")

    def test_source_index_first_listed_wins_dash_names_and_build_skip(self):
        """Source index: first listed folder wins a clash (and it is printed), dash names count, .build is skipped."""
        doc = doc_text(row("F-CFG-01", "Config", "Config.swift", "MenuTests/testOpens()", OK, "2026-01-10"),
                       row("F-DASH-01", "Menu", "Menu-View.swift", "MenuTests/testOpens()", OK, "2026-01-10"))
        g = self.project("DemoGame", doc=doc, state=KEEP)
        motor = g.root / "Packages/Physics/Sources/Physics/Config.swift"
        g.set_state(make_state({
            "F-CFG-01": frec(OK, ["MenuTests/testOpens()"], fp=sha1_of([motor]), when="2026-01-10", run=RUN1),
            "F-DASH-01": frec(OK, ["MenuTests/testOpens()"], fp=g.fp("Menu-View.swift"), when="2026-01-10", run=RUN1)},
            seed("MenuTests/testOpens()")))
        g.edit("Menu-View.swift")
        r = self.run_engine(g, "--from-cache")
        self.assertStatus(g, "F-CFG-01", OK, "Physics is listed before Art, so Physics' Config.swift is the one fingerprinted")
        self.assertStatus(g, "F-DASH-01", REV, "a file name with a dash must be found in the Entrada cell")
        self.assertIn("Menu-View.swift", self.note(g, "F-DASH-01"), "the note names the whole dash-named file")
        self.assertNotIn("not found", self.note(g, "F-DASH-01"), "the file was read and changed, not missed")
        self.assertTrue([l for l in r.out.splitlines() if "clash" in l and "Config.swift" in l],
                        "a cited basename found in two folders must be printed:\n" + r.out)
        mac_doc = doc_text(row("F-CORE-01", "Core", "Core.swift", "DemoCoreTests/CoreRulesTests/testRule", OK, "2026-01-10"))
        m = self.project("DemoMac", doc=mac_doc, state=KEEP)
        real = m.root / "Packages/DemoCore/Sources/DemoCore/Core.swift"
        m.set_state(make_state({"F-CORE-01": frec(OK, ["DemoCoreTests/CoreRulesTests/testRule"], fp=sha1_of([real]),
                                                  when="2026-01-10", run=RUN1)},
                               ({"DemoCoreTests/CoreRulesTests/testRule": {"result": "Passed", "when": "2026-01-10", "run_id": RUN1}},
                                {"DemoCoreTests/CoreRulesTests/testRule": "DemoCoreTests/package"})))
        r = self.run_engine(m, "--from-cache")
        self.assertStatus(m, "F-CORE-01", OK, "the decoy Core.swift in a .build folder must never be indexed")


class StatusRuleTests(EngineCase):
    def test_final_pass_skips_flow_whose_class_did_not_run(self):
        """Defect: the final pass re-validates flows whose class did not run this session."""
        doc = doc_text(row("F-HOME-01", "Home", "HomeView.swift", "HomeUITests/testOpensDetail()"),
                       row("F-RULE-01", "Rules", "Rules.swift", "RulesTests/testA()"))
        flows = {"F-HOME-01": frec(REV, ["HomeUITests/testOpensDetail()"], fp=STALE_FP, when="2026-01-01", run=RUN_OLD),
                 "F-RULE-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP, when="2026-01-01", run=RUN_OLD)}
        raw = seed("HomeUITests/testOpensDetail()", "RulesTests/testA()", when="2026-01-01", run=RUN_OLD)
        p = self.project(doc=doc, state=make_state(flows, raw))
        r = self.run_engine(p, xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertTrue(p.state()["run"]["completed"], "the run finished")
        self.assertStatus(p, "F-RULE-01", OK, "its unit class ran")
        self.assertStatus(p, "F-HOME-01", REV, "a UI class that did not run must not be judged from an old Passed")

    def test_reuse_judges_only_flows_fully_in_bundle(self):
        """Defect: --reuse stamps the current fingerprint on flows without proof in the bundle."""
        doc = doc_text(row("F-FULL-01", "Whole", "Rules.swift", "RulesTests/testA()<br>RulesTests/testB()"),
                       row("F-NONE-01", "None in bundle", "Rules.swift", "Tagged/tag sentence"),
                       row("F-PART-01", "One of two", "Rules.swift", "RulesTests/testA()<br>Pricing rules/loads()"))
        flows = {"F-FULL-01": frec(REV, ["RulesTests/testA()", "RulesTests/testB()"], fp=STALE_FP, when="2025-12-01", run=RUN_OLD),
                 "F-NONE-01": frec(REV, ["Tagged/tag sentence"], fp=STALE_FP, when="2025-12-01", run=RUN_OLD),
                 "F-PART-01": frec(REV, ["RulesTests/testA()", "Pricing rules/loads()"], fp=STALE_FP, when="2025-12-01", run=RUN_OLD)}
        raw = seed("Tagged/tag sentence", "Pricing rules/loads()", when="2025-12-01", run=RUN_OLD)
        p = self.project(doc=doc, state=make_state(flows, raw))
        bundle = self.make_bundle("reuse.xcresult", [tc("DemoTests", "RulesTests", "testA()"),
                                                     tc("DemoTests", "RulesTests", "testB()")], when=ts(2026, 2, 20))
        r = self.run_engine(p, "--reuse", str(bundle))
        self.assertEqual(r.code, 0, r.text)
        state = p.state()
        self.assertStatus(p, "F-FULL-01", OK, "every proof of the flow is in the bundle")
        self.assertEqual(state["flows"]["F-FULL-01"]["validated_at"], "2026-02-20",
                         "the date is the bundle's own, not today's (2026-03-01)")
        self.assertEqual(state["flows"]["F-FULL-01"]["source_fingerprint"], p.fp("Rules.swift"))
        self.assertEqual(state["raw"]["by_path"]["RulesTests/testA()"]["when"], "2026-02-20")
        for fid in ("F-NONE-01", "F-PART-01"):
            self.assertStatus(p, fid, REV, "a flow not fully covered by the bundle keeps its status")
            self.assertEqual(state["flows"][fid]["source_fingerprint"], STALE_FP,
                             "and keeps the old fingerprint: nothing ran against the current source")
            self.assertEqual(state["flows"][fid]["validated_at"], "2025-12-01")
        self.assertTrue([l for l in r.out.splitlines() if "partly" in l and "F-PART-01" in l],
                        "the flow with one of two proofs must be listed as partly covered:\n" + r.out)

    def test_proof_change_rejudges_from_same_or_later_run(self):
        """Defect: adding or renaming a proof does not change a validated flow (proofs_changed_pass)."""
        both = "RulesTests/testA()<br>RulesTests/testB()"
        doc = doc_text(row("F-PA-01", "Same run", "Rules.swift", both, OK, "2026-01-10"),
                       row("F-PB-01", "Later run", "Rules.swift", both, OK, "2026-01-20"))
        p = self.project(doc=doc, state=KEEP)
        fp = p.fp("Rules.swift")
        flows = {"F-PA-01": frec(OK, ["RulesTests/testA()"], fp=fp, when="2026-01-10", run=RUN1),
                 "F-PB-01": frec(OK, ["RulesTests/testA()"], fp=fp, when="2026-01-20", run=RUN2)}
        p.set_state(make_state(flows, seed("RulesTests/testA()", "RulesTests/testB()", run=RUN1)))
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-PA-01", OK, "the new proof's result comes from the flow's own validation run")
        self.assertEqual(p.state()["flows"]["F-PA-01"]["proofs"], ["RulesTests/testA()", "RulesTests/testB()"])
        self.assertStatus(p, "F-PB-01", REV, "the new proof's result is older than the flow's validation run")
        self.assertIn("proof changed in the map", self.note(p, "F-PB-01"))

    def test_proof_and_source_change_together_stay_revalidar(self):
        """Defect: adding or renaming a proof (reclaim_pass ordering): invalidation must come first."""
        doc = doc_text(row("F-PS-01", "Both change", "Rules.swift", "RulesTests/testA()<br>RulesTests/testB()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=KEEP)
        old = p.fp("Rules.swift")
        p.set_state(make_state({"F-PS-01": frec(OK, ["RulesTests/testA()"], fp=old, when="2026-01-10", run=RUN1)},
                               seed("RulesTests/testA()", "RulesTests/testB()", run=RUN1)))
        p.edit("Rules.swift")
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-PS-01", REV, "a source change plus a cached passing new proof must not turn validado")
        self.assertIn("Rules.swift", self.note(p, "F-PS-01"))

    def test_mixed_flow_gets_no_green_from_stale_ui(self):
        """Defect: a mixed unit+UI flow is never re-judged outside --ui, then a stale UI result gives a false green."""
        proofs = ["RulesTests/testA()", "HomeUITests/testSaves()"]
        doc = doc_text(row("F-MIX-01", "Mixed", "Rules.swift", "<br>".join(proofs), OK, "2026-01-10"))

        def mixed():
            p = self.project(doc=doc, state=KEEP)
            raw = seed(*proofs, when="2026-01-10", run=RUN1)
            p.set_state(make_state({"F-MIX-01": frec(REV, proofs, fp=STALE_FP, when="2026-01-10", run=RUN1)}, raw))
            return p

        p = mixed()
        self.run_engine(p, xcode=["auto"])
        self.assertStatus(p, "F-MIX-01", REV, "a full run without --ui never ran its UI half")
        p = mixed()
        bundle = self.make_bundle("unit_only.xcresult", [tc("DemoTests", "RulesTests", "testA()")], when=ts(2026, 2, 20))
        self.run_engine(p, "--reuse", str(bundle))
        self.assertStatus(p, "F-MIX-01", REV, "a bundle with only the unit case cannot validate the mixed flow")
        p = mixed()
        self.run_engine(p, "--only-stale", xcode=["auto", "auto"])
        self.assertStatus(p, "F-MIX-01", OK, "validated once both of its classes ran in the same run")

    def test_mixed_flow_refused_when_configured(self):
        """Mixed unit+UI (the games' refusal rule): refuse_mixed_ui_unit decides."""
        doc = doc_text(row("F-MIX-01", "Mixed", "", "RulesTests/testA()<br>HomeUITests/testSaves()"))
        p = self.project(doc=doc, state=make_state(), config={"refuse_mixed_ui_unit": True})
        self.run_engine(p, "--only-stale", xcode=["auto", "auto"])
        self.assertStatus(p, "F-MIX-01", BROKEN, "with refuse_mixed_ui_unit the mixed flow is broken")
        self.assertIn("mix", self.note(p, "F-MIX-01").lower())
        q = self.project(doc=doc, state=make_state())
        self.run_engine(q, "--only-stale", xcode=["auto", "auto"])
        self.assertStatus(q, "F-MIX-01", OK, "by default the same flow is judged on its proofs")

    def test_lookup_failure_is_sem_prova_not_broken(self):
        """Status rule: a new proof cannot be born red."""
        doc = doc_text(row("F-UNK-01", "Unknown", "", "Unknown sentence/never seen"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale")
        self.assertStatus(p, "F-UNK-01", MISS, "a proof that resolves to no class and has no result is a lookup failure")
        note = self.note(p, "F-UNK-01").lower()
        self.assertIn("lookup", note)
        self.assertNotIn("failed", note, "a lookup miss must not be worded as a test failure")
        doc = doc_text(row("F-GHOST-01", "Absent case", "", "RulesTests/testGhost()"),
                       row("F-RULE-01", "Present case", "", "RulesTests/testA()"))
        q = self.project(doc=doc, state=make_state())
        self.run_engine(q, "--only-stale", xcode=["auto"])
        self.assertStatus(q, "F-GHOST-01", BROKEN, "a case missing from its class's whole run is broken")
        self.assertIn("not in its class", self.note(q, "F-GHOST-01"))
        self.assertStatus(q, "F-RULE-01", OK, "the present case in the same class passes")

    def test_skipped_result_is_broken_with_own_note(self):
        """Extra: a Skipped case proves nothing."""
        doc = doc_text(row("F-SKIP-01", "Skipped", "", "RulesTests/testB()"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["unit_skipped"])
        self.assertStatus(p, "F-SKIP-01", BROKEN, "a skipped case is not a pass")
        note = self.note(p, "F-SKIP-01").lower()
        self.assertIn("skipped", note)
        self.assertNotIn("failed", note, "skipped is not failed")

    def test_an_expected_failure_is_a_pass(self):
        """Defect: a case xcresulttool reports as "Expected Failure" (its only issues were anticipated by
        XCTExpectFailure, and XCTest counts it as passed) broke its flow as failed."""
        doc = doc_text(row("F-XF-01", "Anticipated issue", "Rules.swift", "RulesTests/testA()"))
        flows = {"F-XF-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP, when="2025-12-01", run=RUN_OLD)}
        p = self.project(doc=doc, state=make_state(flows))
        bundle = self.make_bundle("xf.xcresult", [tc("DemoTests", "RulesTests", "testA()", result="Expected Failure")],
                                  when=ts(2026, 2, 20))
        r = self.run_engine(p, "--reuse", str(bundle))
        self.assertEqual(r.code, 0, r.text)
        self.assertStatus(p, "F-XF-01", OK, "XCTest counts an expected failure as a pass")
        self.assertEqual(p.state()["raw"]["by_path"]["RulesTests/testA()"]["result"], "Passed")

    def test_ambiguity_survives_across_bundles(self):
        """Extra: by_name ambiguity is lost across bundles."""
        doc = doc_text(row("F-A-01", "In suite A", "", "SuiteA/loads"), row("F-B-01", "In suite B", "", "SuiteB/loads"),
                       row("F-AMB-01", "Bare name", "", "loads"))
        flows = {"F-A-01": frec(REV, ["SuiteA/loads"], when="2026-01-10", run=RUN1),
                 "F-B-01": frec(REV, ["SuiteB/loads"], when="2026-01-10", run=RUN1),
                 "F-AMB-01": frec(REV, ["loads"], when="2026-01-10", run=RUN1)}
        p = self.project(doc=doc, state=make_state(flows, seed("SuiteA/loads", "SuiteB/loads")))
        self.run_engine(p, "--only-stale", xcode=["ambiguous_a", "ambiguous_b"])
        self.assertStatus(p, "F-AMB-01", BROKEN, "the bare name 'loads' exists in two suites that ran in two bundles")
        note = self.note(p, "F-AMB-01")
        self.assertIn("ambiguous", note.lower())
        self.assertIn("Suite/Case", note)
        self.assertStatus(p, "F-A-01", OK, "the qualified proofs are not ambiguous")
        self.assertStatus(p, "F-B-01", OK, "the qualified proofs are not ambiguous")

    def test_wrong_suite_prefix_does_not_pass(self):
        """Extra: the bare-name fallback across suites."""
        doc = doc_text(row("F-WRONG-01", "Wrong suite", "", "WrongSuite/loads"),
                       row("F-RIGHT-01", "Type of a display-named suite", "", "PricingTests/loads()"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["wrong_suite"])
        self.assertStatus(p, "F-WRONG-01", MISS, "WrongSuite/loads must not pass on RightSuite's Passed 'loads'")
        self.assertStatus(p, "F-RIGHT-01", OK, "the Type PricingTests names the suite 'Pricing rules' correctly")

    def test_cited_missing_source_stays_revalidar_after_run(self):
        """Extra: a missing cited file is re-validated by every run."""
        doc = doc_text(row("F-GONE-01", "Cites Gone.swift", "Gone.swift:3", "RulesTests/testA()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=make_state(
            {"F-GONE-01": frec(OK, ["RulesTests/testA()"], fp="a" * 40, when="2026-01-10", run=RUN1)},
            seed("RulesTests/testA()")))
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertStatus(p, "F-GONE-01", REV, "its proof passed, but the cited file is not on disk")
        self.assertIn("Gone.swift", self.note(p, "F-GONE-01"))

    def test_only_a_run_writes_validado_with_date(self):
        """Status rule: only an actual run writes validado, dated by the latest of its proofs; no render-only mode promotes."""
        doc = doc_text(row("F-NEW-01", "New flow", "", "RulesTests/testA()<br>manual:2026-02-15:tester"))
        raw = seed("RulesTests/testA()", when="2026-02-10", run=RUN1)
        p = self.project(doc=doc, state=make_state(raw=raw))
        for mode in ("--from-cache", "--invalidate"):
            self.run_engine(p, mode)
            self.assertEqual(doc_row(p.doc(), "F-NEW-01")[4:], [shown(MISS), "\u2014"],
                             f"{mode}: a cached Passed and a current manual check must not make a never-validated flow validado")
        self.run_engine(p, "--check")
        self.assertEqual(doc_row(p.doc(), "F-NEW-01")[4:], [shown(MISS), "\u2014"], "--check writes nothing")
        self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertStatus(p, "F-NEW-01", OK, "its test passed in this run and the manual check is current")
        self.assertEqual(p.state()["flows"]["F-NEW-01"]["validated_at"], "2026-03-01",
                         "the latest of the test's run date (2026-03-01) and the manual date (2026-02-15)")
        self.assertEqual(doc_row(p.doc(), "F-NEW-01")[4:], [shown(OK), "2026-03-01"])

    def test_device_proof_rules(self):
        """Status rule: device: rules."""
        doc = doc_text(row("F-DA-01", "Short reason", "", "device:camera"),
                       row("F-DB-01", "Beside a test", "", "device:real camera capture of a receipt<br>RulesTests/testA()"),
                       row("F-DC-01", "Good reason", "", "device:real camera capture of a receipt"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale")
        self.assertStatus(p, "F-DA-01", BROKEN, "a reason under 12 characters")
        self.assertStatus(p, "F-DB-01", BROKEN, "device: beside an automated proof")
        self.assertStatus(p, "F-DC-01", DEV, "a specific reason")
        self.assertEqual(doc_row(p.doc(), "F-DC-01")[4:], [shown(DEV), "—"], "a device-only flow has no date")
        self.assertEqual(self.fake.xcode_calls(), [], "no device flow is planned: no test runs for any of them")
        self.assertEqual(p.state()["run"]["classes_planned"], [])
        self.assertEqual(r.code, 1, "two flows are broken")

    def test_manual_proof_expires_after_90_days(self):
        """Status rule: manual proofs expire after 90 days."""
        doc = doc_text(row("F-MA-01", "90 days", "", "manual:2026-01-01:tester"),
                       row("F-MB-01", "91 days", "", "manual:2025-12-31:tester"),
                       row("F-MC-01", "Bad date", "", "manual:2026-13-01:tester"),
                       row("F-MD-01", "Future", "", "manual:2026-05-01:tester"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--from-cache", "--today", "2026-04-01")
        self.assertStatus(p, "F-MA-01", OK, "90 days old is still valid")
        self.assertStatus(p, "F-MB-01", BROKEN, "91 days old has expired")
        self.assertStatus(p, "F-MC-01", BROKEN, "an impossible date")
        self.assertStatus(p, "F-MD-01", BROKEN, "a date in the future")
        self.assertIn("future", self.note(p, "F-MD-01"))

    def test_print_missing_untracked_vs_tracked(self):
        """Extra: a print: file never committed is a capture not yet made; a tracked one that is gone was deleted."""
        doc = doc_text(row("F-PM-01", "Not yet captured", "", "print:docs/prints/todo.png"),
                       row("F-PM-02", "Deleted capture", "", "print:docs/prints/deleted.png"))
        p = self.project(doc=doc, state=make_state())
        self.fake.git_tracked = ["docs/prints/deleted.png"]
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-PM-01", MISS, "git never tracked the file: the capture is not made yet")
        self.assertStatus(p, "F-PM-02", BROKEN, "git tracks the file and it is gone from the disk")

    def test_accented_device_status_compatible(self):
        """Extra: state compatibility of the device status."""
        doc = doc_text(row("F-DEV-03", "Good reason", "", "device:real camera capture of a receipt"))
        p = self.project(doc=doc, state=make_state({"F-DEV-03": frec(DEV, ["device:real camera capture of a receipt"])}))
        self.run_engine(p, "--from-cache")
        self.assertEqual(doc_row(p.doc(), "F-DEV-03")[4], shown(DEV), "the doc shows the unaccented word")
        self.assertEqual(scoreboard(p.doc())[DEV], 1, "and the scoreboard counts it")
        self.assertEqual(p.state()["flows"]["F-DEV-03"]["status"], stored(DEV), "the state keeps the stored value")

    def test_state_entries_not_in_map_kept_and_listed(self):
        """Extra: ghost state entries are kept byte for byte, never rendered, and counted in the output."""
        p = self.project()
        ghost = p.state()["flows"]["F-OLD-09"]
        r = self.run_engine(p, "--from-cache")
        self.assertEqual(p.state()["flows"]["F-OLD-09"], ghost, "the engine must not drop or rewrite a ghost entry")
        self.assertNotIn("F-OLD-09", p.doc())
        self.assertIn("state entries without a map row: 1", r.out)

    def test_prune_state_removes_only_the_entries_of_flows_not_in_the_map(self):
        """--prune-state: the ghost entries go; every other entry, the raw results and the map stay as they are."""
        p = self.project()
        before, doc = p.state(), p.doc()
        r = self.run_engine(p, "--prune-state")
        self.assertEqual(r.code, 0, r.out + r.err)
        after = p.state()
        self.assertEqual(after["flows"], {k: v for k, v in before["flows"].items() if k != "F-OLD-09"},
                         "a ghost entry stayed, or an entry of a flow in the map was dropped or rewritten")
        self.assertEqual(after["raw"], before["raw"], "the raw results must not change")
        self.assertEqual(p.doc(), doc, "the map must not change")
        self.assertIn("removed F-OLD-09", r.out)

    def test_prune_state_with_nothing_to_remove_writes_nothing(self):
        p = self.project()
        self.run_engine(p, "--prune-state")
        written = os.stat(p.state_path).st_mtime_ns
        before = p.snapshot()
        r = self.run_engine(p, "--prune-state")
        self.assertEqual(r.code, 0, r.out + r.err)
        self.assertUntouched(p, before, "a prune with nothing to remove changed a file")
        self.assertEqual(os.stat(p.state_path).st_mtime_ns, written, "a prune with nothing to remove rewrote the state")
        self.assertIn("nothing written", r.out)

    def test_prune_state_goes_alone(self):
        p = self.project()
        before = p.snapshot()
        for other in ("--check", "--from-cache"):
            r = self.run_engine(p, "--prune-state", other)
            self.assertEqual(r.code, 2, other)
            self.assertIn("--prune-state", r.err, other)
        self.assertUntouched(p, before, "a refused prune wrote something")


class LineEndingTests(unittest.TestCase):
    """The map and the state are committed text: on Windows a text-mode write turned every line into CRLF,
    so one run rewrote the whole file in git."""

    def test_write_atomically_keeps_lf_on_every_platform(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "FLOWS.md"
            check_flows.write_atomically(path, "# map\n\n| a | b |\n")
            self.assertEqual(path.read_bytes(), b"# map\n\n| a | b |\n")


class SourceScanTests(unittest.TestCase):
    """The scan of the test sources: who owns a test, what the masking hides and how names are unescaped. These
    feed only the listings, so a defect here never changes a status and no engine test above would notice it."""

    def owners(self, text):
        spans = check_flows.type_spans(text)
        return {m.group(1): check_flows.owner_at(spans, m.start()) for m in check_flows.FUNC_RE.finditer(text)}

    def test_the_innermost_type_owns_a_func(self):
        text = ("struct Outer {\n    struct Inner {\n        func testInner() {}\n    }\n"
                "    func testOuter() {}\n}\n")
        self.assertEqual(self.owners(text), {"testInner": "Inner", "testOuter": "Outer"})

    def test_an_extension_owns_its_funcs(self):
        text = "final class Suite {}\n\nextension Suite {\n    func testInExtension() {}\n}\n"
        self.assertEqual(self.owners(text), {"testInExtension": "Suite"})

    def test_braces_inside_block_comments_and_raw_strings_never_count(self):
        text = ('struct First {\n    /* an open brace { in a comment */\n'
                '    let s = #"a quote " then a brace { inside"#\n'
                "    func testFirst() {}\n}\n"
                "struct Second {\n    func testSecond() {}\n}\n")
        self.assertEqual(self.owners(text), {"testFirst": "First", "testSecond": "Second"})

    def test_a_func_inside_a_tests_body_is_not_a_test(self):
        """Defect: a helper declared on the line after `@Test func x() {` was taken for a test of its own."""
        text = ("struct Suite {\n    @Test func nextChange() {\n        func close() -> Bool { true }\n    }\n"
                "    @Test(\"named\") func named() {}\n}\n")
        marked = {m.group(1): "@Test" in check_flows.test_attribute_chunk(text, m.start())
                  for m in check_flows.FUNC_RE.finditer(text)}
        self.assertEqual(marked, {"nextChange": True, "close": False, "named": True})

    def test_every_simple_escape_and_the_unicode_escape_are_read(self):
        self.assertEqual(check_flows.unescape_swift(r'tab\tthen line\nbreak, \u{41}, \"q\", back\\slash'),
                         'tab\tthen line\nbreak, A, "q", back\\slash')


class RunPlanningTests(EngineCase):
    def test_only_stale_runs_cited_ui_cases(self):
        """Defect: --only-stale runs the whole class per proof (cases_for_flows)."""
        doc = doc_text(row("F-HOME-01", "Home", "", "HomeUITests/testOpensDetail()"),
                       row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["auto"] * 2)
        selected = [t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)]
        self.assertIn("DemoUITests/HomeUITests/testOpensDetail", selected, "the UI case the flow cites")
        self.assertNotIn("DemoUITests/HomeUITests", selected, "never the whole UI class for one cited case")
        self.assertIn("DemoTests/RulesTests", selected, "a unit class runs whole")
        self.assertEqual(p.state()["raw"]["by_path"]["HomeUITests/testOpensDetail()"]["identifier"],
                         "HomeUITests/testOpensDetail()", "a run stores the xcresult nodeIdentifier")
        # a display-name proof takes its case from the stored nodeIdentifier
        self.fake.reset()
        shown = doc_text(row("F-DISP-01", "Display name", "", "Opening flows/opens detail"))
        flows = {"F-DISP-01": frec(REV, ["Opening flows/opens detail"], when="2026-01-10", run=RUN1)}
        q = self.project(doc=shown, state=make_state(flows, seed("Opening flows/opens detail")))
        self.run_engine(q, "--only-stale", xcode=["auto"])
        self.assertEqual([t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)],
                         ["DemoUITests/HomeUITests/testOpensDetail"], "the identifier in raw names the case")
        # and runs the whole class when the stored entry has no identifier
        self.fake.reset()
        legacy = doc_text(row("F-LEG-01", "Legacy entry", "", "Saving flows/saves"))
        flows = {"F-LEG-01": frec(REV, ["Saving flows/saves"], when="2026-01-10", run=RUN1)}
        q = self.project(doc=legacy, state=make_state(flows, seed("Saving flows/saves", identifiers=False)))
        self.run_engine(q, "--only-stale", xcode=["auto"])
        self.assertEqual([t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)],
                         ["DemoUITests/HomeUITests"], "an unknown case runs the whole class, never a guessed method")

    def test_a_narrowed_run_whose_report_holds_no_cited_case_is_still_a_run_and_judges_its_flows(self):
        """Only the file-load-failure case is in the report: no cited key, yet the runner ran and wrote a report."""
        p = self.project("DemoSystem", doc=doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1)), state=KEEP)
        p.set_state(make_state({"F-CRY-01": frec(REV, [CRY_1], fp="old", when="2026-01-10", run=RUN1)}, dseed(CRY_1)))
        self.fake.load_failures = {U_SPEC}
        r = self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertNotIn("class without a result", r.out, "the report was read: the batch has a result")
        self.assertStatus(p, "F-CRY-01", BROKEN, "judged in this run, not left on its old status")
        self.assertIn("selected by name, absent from the run's report", self.note(p, "F-CRY-01"))
        self.assertTrue(p.state()["run"]["completed"])

    def test_a_cited_ui_case_the_narrowed_run_did_not_report_is_broken_never_green_from_its_old_result(self):
        """The same hole in an XCUITest class narrowed with -only-testing: a case the bundle lacks keeps its old Passed."""
        doc = doc_text(row("F-HOME-01", "Opens detail", "", "HomeUITests/testOpensDetail()"),
                       row("F-HOME-02", "Saves", "", "HomeUITests/testSaves()"))
        flows = {"F-HOME-01": frec(REV, ["HomeUITests/testOpensDetail()"], when="2026-01-10", run=RUN1),
                 "F-HOME-02": frec(REV, ["HomeUITests/testSaves()"], when="2026-01-10", run=RUN1)}
        p = self.project(doc=doc, state=make_state(flows, seed("HomeUITests/testOpensDetail()", "HomeUITests/testSaves()")))
        only_saves = xcresult_json([tc("DemoUITests", "HomeUITests", "testSaves()")])
        r = self.run_engine(p, "--only-stale", xcode=[only_saves])
        self.assertEqual([t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)],
                         ["DemoUITests/HomeUITests/testOpensDetail", "DemoUITests/HomeUITests/testSaves"],
                         "both cases were selected by name")
        self.assertStatus(p, "F-HOME-02", OK, "its case is in the bundle")
        self.assertStatus(p, "F-HOME-01", BROKEN, "its case was selected and the bundle lacks it")
        self.assertIn("selected by name, absent from the run's report", self.note(p, "F-HOME-01"))
        self.assertEqual(r.code, 1, r.text)

    def test_every_test_command_disables_diagnostics_and_parallel(self):
        """Defect: missing -collect-test-diagnostics never (run_batch)."""
        runs = [("Demo", doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"),
                                  row("F-HOME-01", "Home", "", "HomeUITests/testSaves()"))),
                ("DemoGame", doc_text(row("F-MENU-01", "Menu", "", "MenuTests/testOpens()"),
                                      row("F-ECO-02", "Cost", "", "EconomyTests/testCost()"))),
                ("DemoMac", doc_text(row("F-MAC-01", "Mac", "", "MacRulesTests/testMacRule()")))]
        for kind, doc in runs:
            self.fake.reset()
            p = self.project(kind, doc=doc, state=make_state())
            self.run_engine(p, "--only-stale", "--ui", xcode=["auto"] * 4)
            cmds = self.fake.xcode_calls()
            self.assertTrue(cmds, f"{kind}: nothing ran")
            for c in cmds:
                self.assertEqual(arg_after(c.cmd, "-collect-test-diagnostics"), "never", f"{kind}: {c.cmd}")
                self.assertEqual(arg_after(c.cmd, "-parallel-testing-enabled"), "NO", f"{kind}: {c.cmd}")

    def test_only_stale_scope(self):
        """Mode --only-stale and mode --ui: the scope is what is not green, UI included unless ui_needs_flag."""
        doc = doc_text(row("F-VAL-01", "Green", "Rules.swift", "Enum rules/enum sentence"),
                       row("F-REV-01", "Stale", "", "RulesTests/testA()"),
                       row("F-NEW-01", "No proof yet", "", "Tagged/tag sentence"),
                       row("F-BRK-01", "Red", "", "Actor rules/actor sentence"),
                       row("F-DEV-01", "Device only", "", "device:real camera capture of a receipt"),
                       row("F-UIR-01", "Stale UI", "", "HomeUITests/testSaves()"))

        def project(**config):
            p = self.project(doc=doc, state=KEEP, config=config or None)
            flows = {"F-VAL-01": frec(OK, ["Enum rules/enum sentence"], fp=p.fp("Rules.swift"), when="2026-01-10", run=RUN1),
                     "F-REV-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1),
                     "F-BRK-01": frec(BROKEN, ["Actor rules/actor sentence"], when="2026-01-10", run=RUN1, note="x"),
                     "F-DEV-01": frec(DEV, ["device:real camera capture of a receipt"]),
                     "F-UIR-01": frec(REV, ["HomeUITests/testSaves()"], when="2026-01-10", run=RUN1)}
            p.set_state(make_state(flows, seed("Enum rules/enum sentence", "RulesTests/testA()", "Tagged/tag sentence",
                                               "Actor rules/actor sentence", "HomeUITests/testSaves()")))
            return p

        unit = {"DemoTests/RulesTests", "DemoTests/TaggedTests", "DemoTests/ActorRulesTests"}
        p = project()
        self.run_engine(p, "--only-stale", xcode=["auto"] * 6)
        self.assertEqual(sorted(p.state()["run"]["scope_ids"]), ["F-BRK-01", "F-NEW-01", "F-REV-01", "F-UIR-01"],
                         "revalidar, sem prova and broken flows only: never validado or so em aparelho")
        self.assertEqual(self.planned(p), unit | {"DemoUITests/HomeUITests"}, "UI classes are included")
        self.assertNotIn("DemoTests/EnumRulesTests", self.planned(p), "a validated flow's class is not planned")
        gated = project(ui_needs_flag=True)
        self.run_engine(gated, "--only-stale", xcode=["auto"] * 6)
        self.assertEqual(self.planned(gated), unit, "with ui_needs_flag no mode adds UI classes without --ui")
        flagged = project(ui_needs_flag=True)
        self.run_engine(flagged, "--only-stale", "--ui", xcode=["auto"] * 6)
        self.assertEqual(self.planned(flagged), unit | {"DemoUITests/HomeUITests"}, "--ui adds them")

    def test_ui_flag_adds_ui_classes_to_full_and_changed(self):
        """Mode --ui and the full run: unit and package classes only, UI with --ui; --changed likewise."""
        doc = doc_text(row("F-MENU-01", "Menu", "Menu-View.swift", "MenuTests/testOpens()"),
                       row("F-ECO-02", "Cost", "Config.swift", "EconomyTests/testCost()"),
                       row("F-UI-01", "Tap", "Menu-View.swift", "MenuUITests/testTap()"))
        unit = {"DemoGameTests/MenuTests", "PhysicsTests/EconomyTests"}
        for args, expected in (([], unit), (["--ui"], unit | {"DemoGameUITests/MenuUITests"})):
            g = self.project("DemoGame", doc=doc, state=make_state())
            self.run_engine(g, *args, xcode=["auto"] * 4)
            self.assertEqual(self.planned(g), expected, f"full run {args}")
        self.fake.git_diff = ["DemoGame/Home/Menu-View.swift"]
        g = self.project("DemoGame", doc=doc, state=make_state())
        self.run_engine(g, "--changed", xcode=["auto"] * 4)
        self.assertEqual(self.planned(g), {"DemoGameTests/MenuTests"}, "--changed alone leaves UI out")
        g = self.project("DemoGame", doc=doc, state=make_state())
        self.run_engine(g, "--changed", "--ui", xcode=["auto"] * 4)
        self.assertEqual(self.planned(g), {"DemoGameTests/MenuTests", "DemoGameUITests/MenuUITests"},
                         "--changed --ui adds the UI class of the flows it touched")

    def test_changed_selects_from_git_and_patterns(self):
        """Mode --changed (--since): selected by the configured patterns, test files included, untracked too."""
        doc = doc_text(row("F-CAT-01", "Catalog", "Localizable.xcstrings", "Pricing rules/rounds half up"),
                       row("F-RT-01", "Rules A", "Rules.swift", "RulesTests/testA()"),
                       row("F-RT-02", "Rules B", "Rules.swift", "RulesTests/testB()"),
                       row("F-NEW-01", "New view", "NewView.swift", "HomeUITests/testSaves()"),
                       row("F-NONE-01", "Untouched", "HomeView.swift", "Tagged/tag sentence"))
        self.fake.git_diff = ["Demo/Localizable.xcstrings", "DemoTests/RulesTests.swift", "README.md"]
        self.fake.git_untracked = ["Demo/Home/NewView.swift"]
        self.fake.git_since = ["DemoTests/RulesTests.swift"]
        p = self.project(doc=doc, state=make_state())
        p.write("Demo/Home/NewView.swift", "import SwiftUI\nstruct NewView {}\n")
        r = self.run_engine(p, "--changed", xcode=["auto"] * 6)
        self.assertEqual(sorted(p.state()["run"]["scope_ids"]), ["F-CAT-01", "F-NEW-01", "F-RT-01", "F-RT-02"],
                         "the catalog, the test file's type and the untracked file select their flows; README.md does not")
        self.assertRegex(r.out, r"changed files: 3\b")
        self.assertRegex(r.out, r"flows touched: 4\b")
        q = self.project(doc=doc, state=make_state())
        q.write("Demo/Home/NewView.swift", "import SwiftUI\nstruct NewView {}\n")
        r = self.run_engine(q, "--changed", "--since", "main", xcode=["auto"] * 6)
        self.assertEqual(sorted(q.state()["run"]["scope_ids"]), ["F-NEW-01", "F-RT-01", "F-RT-02"],
                         "--since REF compares with the ref and still adds untracked files")
        self.assertRegex(r.out, r"changed files: 2\b")
        self.assertRegex(r.out, r"flows touched: 3\b")

    def test_batch_size_and_checkpoint(self):
        """Mode --batch-size; status rule: state written after every class."""
        doc = doc_text(row("F-BA-01", "Rules", "", "RulesTests/testA()"),
                       row("F-BB-01", "Pricing", "", "Pricing rules/rounds half up"),
                       row("F-BC-01", "Tagged", "", "Tagged/tag sentence"),
                       row("F-BD-01", "Enum", "", "Enum rules/enum sentence"),
                       row("F-BE-01", "Actor", "", "Actor rules/actor sentence"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--batch-size", "2", xcode=["auto"] * 3)
        sizes = sorted(len(only_testing(c.cmd)) for c in self.fake.xcode_calls())
        self.assertEqual(sizes, [1, 2, 2], "five classes in batches of two: 3 commands, 2 + 2 + 1 classes")
        self.fake.reset()
        q = self.project(doc=doc, state=make_state())
        r = self.run_engine(q, "--batch-size", "2", xcode=["auto", "!interrupt"])
        first = {class_of(t) for t in only_testing(self.fake.xcode_calls()[0].cmd)}
        run = q.state()["run"]
        self.assertEqual(set(run["classes_done"]), first, "after batch 1 the disk lists exactly batch 1's classes")
        self.assertEqual(len(first), 2)
        self.assertFalse(run["completed"])

    def test_today_drives_every_date(self):
        """Mode --today; extra: the scoreboard date ignored --today."""
        self.fake.now_value = datetime.datetime(2026, 1, 10, 9, 0, 0)
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale", "--today", "2026-01-10", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertIn(FOOTERS[LANG["code"]].replace("2026-01-01", "2026-01-10"), p.doc(), "the scoreboard line carries --today")
        state = p.state()
        self.assertTrue(state["run"]["run_id"].startswith("20260110T090000"), state["run"]["run_id"])
        self.assertEqual(state["flows"]["F-RULE-01"]["validated_at"], "2026-01-10")
        self.assertEqual(state["raw"]["by_path"]["RulesTests/testA()"]["when"], "2026-01-10")


class ModeTests(EngineCase):
    def test_renamed_test_breaks_flow_after_class_runs(self):
        """Defect: a renamed or deleted test keeps its last Passed (gone_proofs_pass, run-based prune)."""
        doc = doc_text(row("F-OLD-01", "Old name", "", "RulesTests/testA()", OK, "2026-01-10"),
                       row("F-OTHER-01", "Other case", "", "RulesTests/testB()", OK, "2026-01-10"))
        flows = {"F-OLD-01": frec(OK, ["RulesTests/testA()"], when="2026-01-10", run=RUN1),
                 "F-OTHER-01": frec(OK, ["RulesTests/testB()"], when="2026-01-10", run=RUN1)}
        p = self.project(doc=doc, state=make_state(flows, seed("RulesTests/testA()", "RulesTests/testB()")))
        p.write("DemoTests/RulesTests.swift", "import XCTest\n\nfinal class RulesTests: XCTestCase {\n"
                "    func testA() {}\n    func testA2() {}\n    func testB() {}\n}\n")
        r = self.run_engine(p, xcode=["unit_renamed"])
        raw = p.state()["raw"]["by_path"]
        self.assertNotIn("RulesTests/testA()", raw, "the class ran whole and its bundle has no testA: raw drops it")
        self.assertIn("RulesTests/testA2()", raw)
        self.assertStatus(p, "F-OLD-01", BROKEN, "the flow still cites the name the whole-class run no longer produced")
        self.assertIn("not in its class", self.note(p, "F-OLD-01"))
        self.assertStatus(p, "F-OTHER-01", OK, "the case that is still there stays validated")
        self.assertIn("RulesTests/testA2()", listing(r.out, "uncited", "passing") or [],
                      "the renamed case is a passing test no flow cites")

    def test_test_gone_from_source_breaks_flow_offline(self):
        """Defect: a renamed or deleted test keeps its last Passed (gone_proofs_pass, loose scan)."""
        rows = [("F-GA-01", "RulesTests/testRemoved()", BROKEN), ("F-GB-01", "Pricing rules/removed sentence", BROKEN),
                ("F-KA-01", "Enum rules/enum sentence", OK), ("F-KB-01", "Actor rules/actor sentence", OK),
                ("F-KC-01", "Pricing rules/extension sentence", OK), ("F-KD-01", "Tagged/tag sentence", OK),
                ("F-KE-01", "RulesTests/testA()", OK)]
        doc = doc_text(*[row(fid, fid, "", proof, OK, "2026-01-10") for fid, proof, _ in rows])
        flows = {fid: frec(OK, [proof], when="2026-01-10", run=RUN1) for fid, proof, _ in rows}
        p = self.project(doc=doc, state=make_state(flows, seed(*[proof for _, proof, _ in rows])))
        self.run_engine(p, "--from-cache")
        for fid, proof, expected in rows:
            self.assertStatus(p, fid, expected,
                              f"{proof}: " + ("declared nowhere in the source, so its old Passed proves nothing"
                                              if expected == BROKEN else "declared in the source (enum, actor, extension or tagged suite)"))
        self.assertIn("source", self.note(p, "F-GA-01").lower())
        self.assertIn("source", self.note(p, "F-GB-01").lower())

    def test_unrun_uncited_tests_listed(self):
        """Defect: tests no flow cites are listed only if some run executed them (unrun_uncited_tests)."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=make_state({"F-RULE-01": frec(OK, ["RulesTests/testA()"], when="2026-01-10", run=RUN1)},
                                                   seed("RulesTests/testA()")))
        r = self.run_engine(p, "--from-cache")
        unrun = listing(r.out, "uncited", "never run")
        self.assertIsNotNone(unrun, "the never-run listing is missing:\n" + r.out)
        self.assertIn("RulesTests/testB()", unrun, "declared in the source, cited by no flow, executed by no run")
        self.assertNotIn("RulesTests/testA()", unrun, "a cited test is not listed")

    def uncited_project(self):
        doc = doc_text(row("F-ONE-01", "Cited by path", "", "RulesTests/testA()", REV, "2026-01-10"),
                       row("F-TWO-01", "Cited by bare name", "", "rounds half up", OK, "2026-01-10"))
        flows = {"F-ONE-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP, when="2026-01-10", run=RUN1),
                 "F-TWO-01": frec(OK, ["rounds half up"], when="2026-01-10", run=RUN1)}
        raw = seed("RulesTests/testA()", "RulesTests/testB()", "Pricing rules/rounds half up", "Pricing rules/loads()")
        raw[0]["Tagged/tag sentence"] = {"result": "Failed", "when": "2026-01-10", "run_id": RUN1}
        raw[1]["Tagged/tag sentence"] = "DemoTests/TaggedTests"
        return self.project(doc=doc, state=make_state(flows, raw))

    def test_uncited_passing_tests_listed(self):
        """Defect: no listing of passing tests that no flow cites; it prints in every mode."""
        bundle = self.make_bundle("reuse.xcresult", [tc("DemoTests", "RulesTests", "testA()"),
                                                     tc("DemoTests", "RulesTests", "testB()")], when=ts(2026, 2, 20))
        for args, xcode in ((["--from-cache"], None), (["--reuse", str(bundle)], None), (["--only-stale"], ["auto"])):
            p = self.uncited_project()
            r = self.run_engine(p, *args, xcode=xcode)
            items = listing(r.out, "uncited", "passing")
            self.assertIsNotNone(items, f"{args[0]}: the listing is missing:\n" + r.out)
            self.assertIn("RulesTests/testB()", items, f"{args[0]}: passed and cited by no flow")
            self.assertNotIn("RulesTests/testA()", items, f"{args[0]}: cited by a flow")
            self.assertNotIn("Pricing rules/rounds half up", items, f"{args[0]}: cited by its bare name")
            self.assertNotIn("Tagged/tag sentence", items, f"{args[0]}: its last result was Failed")
        p = self.uncited_project()
        r = self.run_engine(p, "--from-cache")
        self.assertIn("Pricing rules/loads()", listing(r.out, "uncited", "passing"))

    def test_build_folder_is_project_claude_folder(self):
        """Defect: build folder under /tmp instead of flows-<Project>; mode --derived-data."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        dd = self.home / "Library" / "Developer" / "Xcode" / "DerivedData" / "flows-Demo"
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["auto"])
        cmd = self.fake.xcode_calls()[0].cmd
        self.assertEqual(os.path.realpath(arg_after(cmd, "-derivedDataPath")), os.path.realpath(dd))
        self.assertTrue(os.path.realpath(arg_after(cmd, "-resultBundlePath")).startswith(os.path.realpath(dd / "results") + os.sep),
                        "bundles go under <derived data>/results/")
        self.assertFalse([a for a in cmd if a.startswith("/tmp")], "no path may start with /tmp")
        self.fake.reset()
        q = self.project(doc=doc, state=make_state(), config={"derived_data": ".derived/flows-dd", "results_dir": ".derived/results"})
        self.run_engine(q, "--only-stale", xcode=["auto"])
        cmd = self.fake.xcode_calls()[0].cmd
        self.assertEqual(os.path.realpath(arg_after(cmd, "-derivedDataPath")), os.path.realpath(q.root / ".derived/flows-dd"),
                         "a configured relative derived data is taken from the project root, as given")
        self.assertTrue(os.path.realpath(arg_after(cmd, "-resultBundlePath")).startswith(
            os.path.realpath(q.root / ".derived/results") + os.sep))
        self.fake.reset()
        given = self.tmp / "my-dd"
        r = self.project(doc=doc, state=make_state())
        self.run_engine(r, "--only-stale", "--derived-data", str(given), xcode=["auto"])
        cmd = self.fake.xcode_calls()[0].cmd
        self.assertEqual(arg_after(cmd, "-derivedDataPath"), str(given), "--derived-data is used as given")
        self.assertTrue(arg_after(cmd, "-resultBundlePath").startswith(str(given / "results") + os.sep))

    def test_no_bundle_ever_deleted_and_names_unique(self):
        """Defect: a fixed result bundle removed with rm -rf before each batch; mode --result-path."""
        doc = doc_text(row("F-ONE-01", "Rules", "", "RulesTests/testA()"),
                       row("F-TWO-01", "Pricing", "", "Pricing rules/rounds half up"),
                       row("F-THREE-01", "Tagged", "", "Tagged/tag sentence"))
        p = self.project(doc=doc, state=make_state())
        with deletion_spy() as spy:
            self.run_engine(p, "--only-stale", xcode=["auto", "!interrupt"])
            self.fake.now_value = NOW + datetime.timedelta(minutes=5)
            second = self.run_engine(p, "--resume", xcode=["auto", "auto"])
        self.assertEqual(second.code, 0, second.text)
        paths = [arg_after(c.cmd, "-resultBundlePath") for c in self.fake.xcode_calls()]
        self.assertEqual(len(paths), 4, "batch 1, the killed batch 2, then batches 2 and 3 again")
        self.assertEqual(len(set(paths)), len(paths), "every batch gets a name of its own, also across --resume")
        self.assertEqual(len(self.fake.bundles_written), 3)
        for bundle in self.fake.bundles_written:
            self.assertTrue(bundle.exists(), f"a result bundle was deleted: {bundle}")
        self.assertFalse([c for c in self.fake.calls if c.cmd[0] == "rm"], "no rm command, ever")
        self.assertFalse([s for s in spy if ".xcresult" in s[1]], f"a bundle was deleted from Python: {spy}")
        # an existing --result-path refuses the batch
        self.fake.reset()
        q = self.project(doc=doc_text(row("F-ONE-01", "Rules", "", "RulesTests/testA()")), state=make_state())
        existing = self.tmp / "mine.xcresult"
        existing.mkdir()
        (existing / "marker.txt").write_text("keep")
        r = self.run_engine(q, "--only-stale", "--result-path", str(existing))
        self.assertEqual(self.fake.xcode_calls(), [], "no xcodebuild call may be made on a path that exists")
        self.assertTrue((existing / "marker.txt").exists(), "and the existing bundle is left alone")
        run = q.state()["run"]
        self.assertEqual(run["classes_done"], [], "the refused batch's classes are not done")
        self.assertFalse(run["completed"])
        self.assertNotEqual(r.code, 0)

    def test_two_runs_in_the_same_second_get_different_bundle_names(self):
        """An engine-made result bundle name takes the next free counter when two runs share a second."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["auto"])
        p.set_state(make_state())   # the same run again, with the same clock reading
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        paths = [arg_after(c.cmd, "-resultBundlePath") for c in self.fake.xcode_calls()]
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(set(paths)), 2, "two runs in one second must not share a bundle name: " + str(paths))
        self.assertTrue(paths[1].endswith("-2.xcresult"), paths[1])
        self.assertTrue(all(pathlib.Path(x).exists() for x in paths), "the first bundle is left alone")
        doc = doc_text(row("F-CORE-01", "Core", "", "DemoCoreTests/CoreRulesTests/testRule"))
        m = self.project("DemoMac", doc=doc, state=make_state())
        self.run_engine(m, "--only-stale", swift=["core"])
        m.set_state(make_state())
        self.run_engine(m, "--only-stale", swift=["core"])
        reports = [arg_after(c.cmd, "--xunit-output") for c in self.fake.swift_calls()]
        self.assertEqual(len(set(reports)), 2, "the xunit report of a second run in the same second is a new file: " + str(reports))
        self.assertTrue(pathlib.Path(reports[0]).exists())

    def test_swift_escapes_in_test_names_keep_a_present_test_present(self):
        """Defect: a Swift escape kept in a captured @Test name makes proof_gone() call a present test gone."""
        p = self.project()
        p.write("DemoTests/QuotingTests.swift", r'''import Testing

@Suite("Quoting rules")
struct QuotingTests {
    @Test("\"Quoted\" sentence") func quoted() {}
    @Test("back\\slash sentence") func slash() {}
    @Test("caf\u{e9} sentence") func accent() {}
}
''')
        names = {"F-QA-01": '"Quoted" sentence', "F-QB-01": "back\\slash sentence", "F-QC-01": "caf\u00e9 sentence",
                 "F-QD-01": '"Removed" sentence'}
        doc = doc_text(*[row(fid, fid, "", "Quoting rules/" + name, OK, "2026-01-10") for fid, name in names.items()])
        flows = {fid: frec(OK, ["Quoting rules/" + name], when="2026-01-10", run=RUN1) for fid, name in names.items()}
        by_path = {"Quoting rules/" + name: {"result": "Passed", "when": "2026-01-10", "run_id": RUN1,
                                             "identifier": "QuotingTests/x()"} for name in names.values()}
        runnable = {key: "DemoTests/QuotingTests" for key in by_path}
        p.set_doc(doc)
        p.set_state(make_state(flows, (by_path, runnable)))
        self.run_engine(p, "--from-cache")
        for fid in ("F-QA-01", "F-QB-01", "F-QC-01"):
            self.assertStatus(p, fid, OK, f"{names[fid]!r} is declared in the source with Swift escapes: not gone")
        self.assertStatus(p, "F-QD-01", BROKEN, "a name the source does not declare is still gone")
        self.assertIn("source", self.note(p, "F-QD-01").lower())

    def test_swift_escapes_in_suite_names_resolve_the_class(self):
        """Defect: a Swift escape kept in a captured @Suite name leaves the proof citing it without a class."""
        p = self.project(doc=doc_text(row("F-BIG-01", "Quoted suite", "", 'The "big" suite/plain')), state=make_state())
        p.write("DemoTests/BigTests.swift", r'''import Testing

@Suite("The \"big\" suite")
struct BigTests {
    @Test("plain") func plain() {}
}
''')
        p.master = p.master + [tc("DemoTests", 'The "big" suite', "plain", "BigTests/plain()")]
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertEqual(self.planned(p), {"DemoTests/BigTests"}, "the unescaped @Suite name gives the class:\n" + r.out)
        self.assertStatus(p, "F-BIG-01", OK, "and the run validates the flow")

    def test_a_parameterized_case_is_cited_by_the_name_with_labels_its_result_carries(self):
        """Defect: a parameterized Swift Testing case reports `name(label:)`, which was not a declared name, so citing it
        broke the flow as a test the source no longer declares."""
        doc = doc_text(row("F-PAR-01", "Labelled arguments", "", "ParamTests/rounds(value:expected:)"),
                       row("F-PAR-02", "Unlabelled argument", "", "ParamTests/scales(_:)"),
                       row("F-PAR-03", "Wrong labels", "", "ParamTests/rounds(amount:)"),
                       row("F-PAR-04", "A closure and a generic argument", "", "ParamTests/waits(handler:items:)"))
        p = self.project(doc=doc, state=make_state())
        p.write("DemoTests/ParamTests.swift", '''import Testing

struct ParamTests {
    @Test(arguments: [(1, 1), (2, 2)])
    func rounds(value: Int, expected: Int) {}
    @Test(arguments: [1, 2]) func scales(_ x: Int) {}
    @Test(arguments: [1]) func waits(handler h: (Int) -> Void = { _ in }, items: [Int: String] = [:]) {}
    func helper(value: Int) {}
}
''')
        p.master = p.master + [tc("DemoTests", "ParamTests", "rounds(value:expected:)"),
                               tc("DemoTests", "ParamTests", "scales(_:)"),
                               tc("DemoTests", "ParamTests", "waits(handler:items:)")]
        r = self.run_engine(p, "--only-stale", xcode=["auto"])
        self.assertStatus(p, "F-PAR-01", OK, "the result's name with labels is the case the source declares:\n" + r.out)
        self.assertStatus(p, "F-PAR-02", OK, "an unlabelled argument is written _:")
        self.assertStatus(p, "F-PAR-03", BROKEN, "labels the source does not declare are a test that is gone")
        self.assertStatus(p, "F-PAR-04", OK, "an arrow and a dictionary type inside the parameters close nothing")

    def engine_rules_project(self, raw_keys):
        """A project whose Swift Testing suite has a helper type nested above and among its tests."""
        p = self.project(doc=doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()")), state=KEEP)
        p.write("DemoTests/EngineRulesTests.swift", r'''import Testing

struct TopHelper {
    func help() {}
}

@Suite("Engine rules")
struct EngineRulesTests {
    struct FakeEngine {
        func run() { print("}") }  // a brace in a string and in a comment: }
    }
    @Test("starts cleanly") func startsCleanly() {}
    @Test func stops() {}

    final class Recorder {
        func record() {}
    }
    @Test("records calls") func recordsCalls() {}
    @Test func neverRan() {}
    @Test("says \"hi\" twice") func says() {}
}
''')
        by_path = {key: {"result": "Passed", "when": "2026-01-10", "run_id": RUN1} for key in raw_keys}
        p.set_state(make_state({}, (by_path, {key: "DemoTests/EngineRulesTests" for key in raw_keys})))
        return p

    def test_helper_types_own_no_tests_in_the_never_run_listing(self):
        """Defect: the owner of a test is the last type declared before it, so a helper takes the tests of a suite."""
        ran = ["Engine rules/starts cleanly", "Engine rules/stops()", "Engine rules/records calls",
               'Engine rules/says "hi" twice']
        p = self.engine_rules_project(ran)
        r = self.run_engine(p, "--from-cache")
        never = listing(r.out, "uncited", "never run")
        self.assertIsNotNone(never, r.out)
        self.assertIn("Engine rules/neverRan()", never, "a test of the suite that no run executed is listed under the suite")
        for name in ran:
            self.assertNotIn(name, never, f"{name} ran")
        self.assertEqual([n for n in never if n.startswith(("FakeEngine/", "Recorder/", "TopHelper/"))], [],
                         "a helper type owns no test: " + str(never))
        self.assertEqual([n for n in never if "\\" in n], [], "a listed name is the unescaped one: " + str(never))

    def test_a_result_with_the_cases_name_counts_as_run_under_any_suite(self):
        """Backstop: a raw result whose case part is the display name or method() says the case ran."""
        p = self.engine_rules_project(["Reported elsewhere/starts cleanly", "Reported elsewhere/stops()"])
        r = self.run_engine(p, "--from-cache")
        never = listing(r.out, "uncited", "never run") or []
        self.assertNotIn("Engine rules/starts cleanly", never, "its display name is a result's case part")
        self.assertNotIn("Engine rules/stops()", never, "its method() is a result's case part")
        self.assertIn("Engine rules/records calls", never, "no result carries this case: still listed")
        self.assertIn("Engine rules/neverRan()", never)
        self.assertIn('Engine rules/says "hi" twice', never, "its unescaped display name is what the listing shows")

    def test_display_name_suite_resolves_without_history(self):
        """Defect: a Swift Testing proof cited by its @Suite name never runs (suite_types)."""
        doc = doc_text(row("F-ONE-01", "Plain suite in a subfolder", "", "Pricing rules/rounds half up"),
                       row("F-TWO-01", "Suite with tags, nonisolated", "", "Tagged/tag sentence"),
                       row("F-THREE-01", "Enum suite", "", "Enum rules/enum sentence"),
                       row("F-FOUR-01", "Actor suite", "", "Actor rules/actor sentence"))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["auto"] * 4)
        self.assertEqual(self.planned(p), {"DemoTests/PricingTests", "DemoTests/TaggedTests",
                                           "DemoTests/EnumRulesTests", "DemoTests/ActorRulesTests"},
                         "with raw empty, the @Suite declarations alone must give each class")
        g_doc = doc_text(row("F-ECO-01", "Package suite", "", "Economy/cost doubles per level"))
        g = self.project("DemoGame", doc=g_doc, state=make_state())
        self.run_engine(g, "--only-stale", xcode=["auto"])
        self.assertEqual(self.planned(g), {"PhysicsTests/EconomyTests"}, "a suite in the package tests folder")
        h = self.project("DemoGame", doc=g_doc, state=make_state())
        h.write("DemoGameTests/EconomyDupTests.swift", 'import Testing\n\n@Suite("Economy") struct EconomyDupTests {}\n')
        r = self.run_engine(h, "--only-stale", xcode=["auto"])
        self.assertTrue([l for l in r.text.splitlines() if "declared in more than one" in l and "Economy" in l],
                        "a display name declared in two targets must be reported, not resolved by last-wins:\n" + r.text)

    def test_malformed_flow_id_stops_before_writing(self):
        """Status rule: the flow ID pattern."""
        p = self.project()
        p.set_doc((FIXTURES / "project_tree/Demo/docs/FLOWS-a11y.md").read_text())
        before = p.snapshot()
        r = self.run_engine(p, "--from-cache")
        self.assertEqual(r.code, 2, r.text)
        self.assertIn("F-A11Y-01", r.text, "the message names the row")
        self.assertUntouched(p, before, "nothing may be written when a flow ID is malformed")
        ok = self.project(doc=doc_text(row("F-ACC-01", "Accessible", "", "RulesTests/testA()")), state=make_state())
        r = self.run_engine(ok, "--from-cache")
        self.assertEqual(doc_row(ok.doc(), "F-ACC-01")[4], shown(MISS), "F-ACC-01 is a valid ID and is read")

    def test_empty_cell_does_not_shift_columns(self):
        """Extra: empty cells shift columns."""
        doc = doc_text(row("F-NOENT-01", "Empty Entrada", "", "RulesTests/testA()"),
                       row("F-NOPRF-01", "Empty Prova", "Rules.swift", ""))
        p = self.project(doc=doc, state=make_state())
        self.run_engine(p, "--only-stale", xcode=["auto"])
        rendered = doc_row(p.doc(), "F-NOPRF-01")
        self.assertEqual(len(rendered), 6, "the rendered row keeps 6 cells: " + str(rendered))
        self.assertEqual(rendered[2:4], ["Rules.swift", ""])
        self.assertStatus(p, "F-NOPRF-01", MISS, "no proof declared")
        self.assertEqual(p.state()["flows"]["F-NOPRF-01"]["proofs"], [], "the Status word must never be read as a proof")
        entrada = doc_row(p.doc(), "F-NOENT-01")
        self.assertEqual((len(entrada), entrada[2], entrada[3]), (6, "", "RulesTests/testA()"))
        self.assertStatus(p, "F-NOENT-01", OK, "its proof is in the Prova cell, not shifted into Entrada")

    def test_failed_batch_marks_no_class_done(self):
        """Extra: a failed batch counted done (a batch with no result)."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()", REV, "2026-01-10"))
        other = xcresult_json([tc("DemoTests", "Tagged", "tag sentence", "TaggedTests/tagCase()")])
        for label, queue, fail_tool in (("the runner exits 65 with no bundle", ["!exit65"], False),
                                        ("a bundle with no case", ["empty"], False),
                                        ("xcresulttool fails", ["auto"], True),
                                        ("a bundle with a case only for another class", [other], False)):
            self.fake.reset()
            self.fake.fail_xcresulttool = fail_tool
            flows = {"F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1)}
            p = self.project(doc=doc, state=make_state(flows, seed("RulesTests/testA()")))
            r = self.run_engine(p, "--only-stale", xcode=queue)
            run = p.state()["run"]
            self.assertEqual(run["classes_done"], [], f"{label}: the batch's class must not count as done")
            self.assertFalse(run["completed"], label)
            self.assertEqual(r.code, 1, f"{label}: the exit code is 1:\n{r.text}")
            self.assertStatus(p, "F-RULE-01", REV, f"{label}: an old Passed in raw must not validate the flow")

    def test_state_written_atomically_and_bad_state_stops(self):
        """Extra: non-atomic write; an unreadable or other-version state silently replaced."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state())
        replaced = []
        real_replace = os.replace

        def spy(src, dst, *args, **kwargs):
            replaced.append((str(src), str(dst)))
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch.object(os, "replace", spy):
            self.run_engine(p, "--only-stale", xcode=["auto"])
        target = os.path.realpath(p.state_path)
        hits = [(s, d) for s, d in replaced if os.path.realpath(d) == target]
        self.assertTrue(hits, "the state must be saved through os.replace of a temporary file")
        self.assertEqual(os.path.dirname(hits[0][0]), os.path.dirname(hits[0][1]),
                         "the temporary file lives in docs/, so the replace is atomic")
        self.assertNotEqual(hits[0][0], hits[0][1])
        for label, content in (("unreadable", "{not json"), ("version 3", json.dumps(make_state() | {"version": 3}))):
            q = self.project(doc=doc, state=make_state())
            q.state_path.write_text(content)
            before = q.snapshot()
            r = self.run_engine(q, "--from-cache")
            self.assertEqual(r.code, 2, f"{label} state must stop the engine:\n{r.text}")
            self.assertUntouched(q, before, f"{label} state: the file must stay byte-identical")
        m = self.project(doc=doc, state=None)
        r = self.run_engine(m, "--from-cache")
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(m.state()["version"], 2, "a missing state starts empty and is written as version 2")

    def test_duplicate_flow_id_aborts_before_writing(self):
        """Extra: duplicate IDs."""
        p = self.project()
        p.set_doc((FIXTURES / "project_tree/Demo/docs/FLOWS-dupe.md").read_text())
        before = p.snapshot()
        r = self.run_engine(p, "--from-cache")
        self.assertNotEqual(r.code, 0, r.text)
        self.assertIn("F-HOME-01", r.text)
        self.assertUntouched(p, before, "a duplicate ID must stop the run before anything is written")

    def test_resume_keeps_ui_choice_and_skips_removed_flow(self):
        """Mode --resume (an unbound variable and a missing key on resume)."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"),
                       row("F-HOME-01", "Opens detail", "", "HomeUITests/testOpensDetail()"),
                       row("F-HOME-02", "Saves", "", "HomeUITests/testSaves()"))
        p = self.project(doc=doc, state=make_state(), config={"ui_needs_flag": True})
        self.run_engine(p, "--only-stale", "--ui", xcode=["auto", "!interrupt"])
        self.assertEqual(p.state()["run"]["classes_done"], ["DemoTests/RulesTests"], "batch 1 finished before the kill")
        p.set_doc(doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"),
                           row("F-HOME-01", "Opens detail", "", "HomeUITests/testOpensDetail()")))
        self.fake.now_value = NOW + datetime.timedelta(minutes=5)
        self.fake.reset()
        r = self.run_engine(p, "--resume", xcode=["auto"])
        self.assertEqual(r.code, 0, "no exception, and the run ends clean:\n" + r.text)
        ui = [t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)]
        self.assertTrue([t for t in ui if t.startswith("DemoUITests/HomeUITests")],
                        "the stored --ui choice must survive the resume, so the UI class still runs")
        self.assertTrue([l for l in r.out.splitlines() if "no longer in the map" in l and "F-HOME-02" in l],
                        "the flow removed from the map is skipped and listed:\n" + r.out)
        self.assertStatus(p, "F-HOME-01", OK, "the flows still in the map were judged")

    def test_resume_refuses_without_open_run(self):
        """Mode --resume."""
        p = self.project(state=make_state())
        r = self.run_engine(p, "--resume")
        self.assertEqual(r.code, 2, "no run at all:\n" + r.text)
        self.assertIn("nothing to resume", r.text)
        q = self.project()
        self.assertTrue(q.state()["run"]["completed"], "the fixture state holds a completed run")
        r = self.run_engine(q, "--resume")
        self.assertEqual(r.code, 2, "a completed run:\n" + r.text)
        self.assertEqual(self.fake.runner_calls(), [], "a refused resume calls no runner")

    def test_invalidate_equals_from_cache_and_runs_nothing(self):
        """Mode --invalidate (an alias of --from-cache) and mode --from-cache."""
        a, b = self.project(), self.project()
        ra = self.run_engine(a, "--invalidate")
        rb = self.run_engine(b, "--from-cache")
        self.assertEqual(a.snapshot(), b.snapshot(), "--invalidate and --from-cache must write the same doc and state")
        self.assertEqual((ra.code, rb.code), (1, 1), "the static map has broken flows, so exit 1")
        self.assertEqual(self.fake.runner_calls(), [], "neither mode runs anything")
        clean = self.project(doc=doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()")), state=make_state())
        self.assertEqual(self.run_engine(clean, "--from-cache").code, 0, "exit 0 when no flow is broken")
        self.assertEqual(self.run_engine(self.project(doc=clean.doc(), state=make_state()), "--invalidate").code, 0)

    def test_invalidate_classes_demotes_only_whole_flows(self):
        """Mode --invalidate-classes."""
        both = ["RulesTests/testA()", "RulesTests/testB()"]
        proofs = {"F-WA-01": both, "F-WB-01": ["RulesTests/testA()", "Tagged/tag sentence"],
                  "F-WC-01": ["RulesTests/testA()", "Unknown sentence/x"], "F-WD-01": ["print:docs/prints/home.png"]}
        doc = doc_text(*[row(fid, fid, "", "<br>".join(pr), OK, "2026-01-10") for fid, pr in proofs.items()])
        flows = {fid: frec(OK, pr, when="2026-01-10", run=RUN1) for fid, pr in proofs.items()}
        raw = seed("RulesTests/testA()", "RulesTests/testB()", "Tagged/tag sentence")
        p = self.project(doc=doc, state=make_state(flows, raw))
        r = self.run_engine(p, "--invalidate-classes", "RulesTests")
        self.assertEqual(self.fake.runner_calls(), [], "alone, --invalidate-classes only recomputes")
        self.assertStatus(p, "F-WA-01", REV, "every proof resolves to RulesTests")
        for fid, why in (("F-WB-01", "one proof lives in another class"), ("F-WC-01", "one proof does not resolve"),
                         ("F-WD-01", "a print-only flow names no class")):
            self.assertStatus(p, fid, OK, why)
        q = self.project(doc=doc, state=make_state(flows, raw))
        self.run_engine(q, "--invalidate-classes", "RulesTests", "--only-stale", xcode=["auto"])
        self.assertEqual([t for c in self.fake.xcode_calls() for t in only_testing(c.cmd)], ["DemoTests/RulesTests"],
                         "with --only-stale the demoted class runs again")
        self.assertStatus(q, "F-WA-01", OK, "and the flow is validated by that run")

    def test_reuse_empty_bundle_writes_nothing(self):
        """Mode --reuse."""
        p = self.project()
        before = p.snapshot()
        bundle = self.make_bundle("empty.xcresult", [])
        r = self.run_engine(p, "--reuse", str(bundle))
        self.assertEqual(r.code, 2, r.text)
        self.assertUntouched(p, before, "a bundle with no test case must leave doc and state byte-identical")

    # -- --reuse with several bundles (one integration run leaves one bundle per test target)

    def split_project(self):
        """A flow whose proofs sit in two bundles (a unit case and a UI case), one whole in the first bundle, and one
        with a proof in neither."""
        doc = doc_text(row("F-SPLIT-01", "Half and half", "Rules.swift", "RulesTests/testA()<br>HomeUITests/testSaves()"),
                       row("F-ONE-01", "Whole in the first", "Rules.swift", "RulesTests/testB()"),
                       row("F-NONE-01", "Neither", "Rules.swift", "RulesTests/testA()<br>Pricing rules/loads()"))
        flows = {fid: frec(REV, proofs, fp=STALE_FP, when="2025-12-01", run=RUN_OLD) for fid, proofs in (
            ("F-SPLIT-01", ["RulesTests/testA()", "HomeUITests/testSaves()"]), ("F-ONE-01", ["RulesTests/testB()"]),
            ("F-NONE-01", ["RulesTests/testA()", "Pricing rules/loads()"]))}
        return self.project(doc=doc, state=make_state(flows, seed("Pricing rules/loads()", when="2025-12-01", run=RUN_OLD)))

    def test_reuse_takes_several_bundles_and_judges_a_flow_whose_proofs_are_split_between_them(self):
        """Defect: --reuse judged a flow only when every proof was in the one bundle, so a flow citing the cases of two
        test targets (two bundles of one integration run) was never judged from them."""
        p = self.split_project()
        first = self.make_bundle("first.xcresult", [tc("DemoTests", "RulesTests", "testA()"),
                                                    tc("DemoTests", "RulesTests", "testB()")], when=ts(2026, 2, 10))
        second = self.make_bundle("second.xcresult", [tc("DemoUITests", "HomeUITests", "testSaves()")], when=ts(2026, 2, 20))
        r = self.run_engine(p, "--reuse", str(first), str(second))
        self.assertEqual(r.code, 0, r.text)
        state = p.state()
        self.assertStatus(p, "F-SPLIT-01", OK, "one proof in each bundle: every proof is in the union")
        self.assertEqual(state["flows"]["F-SPLIT-01"]["validated_at"], "2026-02-20",
                         "the latest date of its proofs: the second bundle's own date")
        self.assertEqual(state["flows"]["F-SPLIT-01"]["source_fingerprint"], p.fp("Rules.swift"))
        self.assertStatus(p, "F-ONE-01", OK, "a flow whole in one bundle is judged as before")
        self.assertEqual(state["flows"]["F-ONE-01"]["validated_at"], "2026-02-10", "with the first bundle's date, not the second's")
        self.assertEqual(state["raw"]["by_path"]["RulesTests/testA()"]["when"], "2026-02-10")
        self.assertEqual(state["raw"]["by_path"]["HomeUITests/testSaves()"]["when"], "2026-02-20",
                         "each result keeps the date of the bundle it was read from")
        self.assertIn("RulesTests/testA()", state["raw"]["runnable"], "the first bundle's classes are kept")
        self.assertEqual(state["raw"]["runnable"]["RulesTests/testA()"], "DemoTests/RulesTests",
                         "the class of a case read from the first bundle is kept when the second is read")
        self.assertIn("HomeUITests/testSaves()", state["raw"]["runnable"], "the second bundle's classes are added")
        self.assertEqual(state["raw"]["runnable"]["HomeUITests/testSaves()"], "DemoUITests/HomeUITests")
        self.assertEqual(state["flows"]["F-SPLIT-01"]["run_id"], "20260220T120000-reuse",
                         "the run is dated by the latest bundle")
        self.assertStatus(p, "F-NONE-01", REV, "a flow with a proof in neither bundle keeps its status")
        self.assertEqual(state["flows"]["F-NONE-01"]["source_fingerprint"], STALE_FP)
        self.assertTrue([l for l in r.out.splitlines() if "partly" in l and "F-NONE-01" in l], r.out)
        done = [l for l in r.out.splitlines() if l.startswith("map rewritten from")]
        self.assertEqual(len(done), 1, r.out)
        for text in (str(first), "results dated 2026-02-10", str(second), "results dated 2026-02-20"):
            self.assertIn(text, done[0], "the printed line names every bundle and its date")
        # with only the first bundle read, the split flow cannot be judged: the failing form of the defect
        q = self.split_project()
        single = self.run_engine(q, "--reuse", str(first))
        self.assertEqual(single.code, 0, single.text)
        self.assertStatus(q, "F-SPLIT-01", REV, "half of its proofs: the status is kept")
        self.assertTrue([l for l in single.out.splitlines() if "partly" in l and "F-SPLIT-01" in l], single.out)
        self.assertIn(f"map rewritten from {first} (results dated 2026-02-10)", single.out,
                      "the line of a single bundle is the one it always was")

    def test_a_case_that_failed_in_any_bundle_counts_as_failed(self):
        """Failed in the second bundle (a flow split between them) or in either bundle of a case both hold."""
        p = self.split_project()
        first = self.make_bundle("first.xcresult", [tc("DemoTests", "RulesTests", "testA()"),
                                                    tc("DemoTests", "RulesTests", "testB()")], when=ts(2026, 2, 10))
        failing = self.make_bundle("failing.xcresult", [tc("DemoUITests", "HomeUITests", "testSaves()", result="Failed")],
                                   when=ts(2026, 2, 20))
        r = self.run_engine(p, "--reuse", str(first), str(failing))
        self.assertEqual(r.code, 1, "a flow is broken: exit 1 as for one bundle\n" + r.text)
        self.assertStatus(p, "F-SPLIT-01", BROKEN, "its proof in the second bundle failed")
        self.assertIn("failed: HomeUITests/testSaves()", self.note(p, "F-SPLIT-01"))
        self.assertStatus(p, "F-ONE-01", OK, "a flow of the first bundle is judged on its own proof")
        # the same case in both bundles, failed in one: failed whichever order the bundles come in
        doc = doc_text(row("F-ONE-01", "Whole", "Rules.swift", "RulesTests/testA()"))
        passed = self.make_bundle("passed.xcresult", [tc("DemoTests", "RulesTests", "testA()")], when=ts(2026, 2, 20))
        broke = self.make_bundle("broke.xcresult", [tc("DemoTests", "RulesTests", "testA()", result="Failed")],
                                 when=ts(2026, 2, 10))
        for order in ((passed, broke), (broke, passed)):
            q = self.project(doc=doc, state=make_state({"F-ONE-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP,
                                                                           when="2025-12-01", run=RUN_OLD)}))
            code = self.run_engine(q, "--reuse", *[str(b) for b in order]).code
            self.assertEqual(code, 1, f"{[b.name for b in order]}")
            self.assertStatus(q, "F-ONE-01", BROKEN, f"failed in one of {[b.name for b in order]}, passed in the other")
            self.assertEqual(q.state()["raw"]["by_path"]["RulesTests/testA()"]["result"], "Failed")
        # passed in both: the later date is the case's date
        older = self.make_bundle("older.xcresult", [tc("DemoTests", "RulesTests", "testA()")], when=ts(2026, 1, 15))
        for order in ((passed, older), (older, passed)):
            s = self.project(doc=doc, state=make_state({"F-ONE-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP,
                                                                           when="2025-12-01", run=RUN_OLD)}))
            self.run_engine(s, "--reuse", *[str(b) for b in order])
            self.assertStatus(s, "F-ONE-01", OK, "")
            self.assertEqual(s.state()["raw"]["by_path"]["RulesTests/testA()"]["when"], "2026-02-20",
                             f"{[b.name for b in order]}")
        # a case skipped in one bundle and passed in the other proves nothing
        skipped = self.make_bundle("skipped.xcresult", [tc("DemoTests", "RulesTests", "testA()", result="Skipped")],
                                   when=ts(2026, 2, 15))
        t = self.project(doc=doc, state=make_state({"F-ONE-01": frec(REV, ["RulesTests/testA()"], fp=STALE_FP,
                                                                       when="2025-12-01", run=RUN_OLD)}))
        self.run_engine(t, "--reuse", str(passed), str(skipped))
        self.assertStatus(t, "F-ONE-01", BROKEN, "Skipped in one of the two bundles")
        self.assertIn("skipped, proved nothing", self.note(t, "F-ONE-01"))

    def test_a_bundle_with_no_test_case_among_several_writes_nothing(self):
        p = self.split_project()
        before = p.snapshot()
        first = self.make_bundle("first.xcresult", [tc("DemoTests", "RulesTests", "testA()")], when=ts(2026, 2, 10))
        empty = self.make_bundle("empty.xcresult", [])
        r = self.run_engine(p, "--reuse", str(first), str(empty))
        self.assertEqual(r.code, 2, r.text)
        self.assertIn(f"no test case read from {empty}", r.err, "the bundle that holds no case is named")
        self.assertUntouched(p, before, "one unreadable bundle among several leaves doc and state byte-identical")
        r = self.run_engine(p, "--reuse", str(first), str(self.tmp / "missing.xcresult"))
        self.assertEqual(r.code, 2, "a bundle that does not exist is refused the same way\n" + r.text)
        self.assertIn("missing.xcresult; nothing written", r.err)
        self.assertUntouched(p, before, "")

    def test_check_writes_nothing_and_runs_nothing(self):
        """Mode --check; status rule: status is never typed."""
        p = self.project()
        before = p.snapshot()
        r = self.run_engine(p, "--check")
        self.assertUntouched(p, before, "--check writes neither the state nor the doc")
        self.assertEqual(self.fake.runner_calls(), [], "--check opens no device and runs no test")
        self.assertEqual(r.code, 1, "the static map has broken flows:\n" + r.text)
        typed = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()", OK, "2026-01-10"))
        state = make_state({"F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1)})
        q = self.project(doc=typed, state=state)
        self.assertEqual(self.run_engine(q, "--check").code, 1, "a Status cell typed validated against a revalidate state")
        agree = self.project(doc=typed.replace(f"| {shown(OK)} |", f"| {shown(REV)} |"), state=state)
        self.assertEqual(self.run_engine(agree, "--check").code, 0, "a doc that agrees with the state passes")
        date = self.project(doc=typed.replace(f"| {shown(OK)} | 2026-01-10 |", f"| {shown(REV)} | 2026-02-02 |"), state=state)
        self.assertEqual(self.run_engine(date, "--check").code, 1, "a typed validation date that differs")
        for extra in ("--only-stale", "--ui"):
            before = q.snapshot()
            self.assertEqual(self.run_engine(q, "--check", extra).code, 2, f"--check beside {extra} is refused")
            self.assertUntouched(q, before, f"--check {extra}")
        self.assertEqual(self.fake.runner_calls(), [])

    def test_render_overwrites_typed_status(self):
        """Status rule: status is never typed."""
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()", OK, "2026-02-02"),
                       row("F-NEW-01", "New", "", "RulesTests/testB()", OK, "2026-02-03"))
        state = make_state({"F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-15", run=RUN1)})
        p = self.project(doc=doc, state=state)
        self.run_engine(p, "--from-cache")
        self.assertEqual(doc_row(p.doc(), "F-RULE-01")[4:], [shown(REV), "2026-01-15"], "typed words and dates are overwritten")
        self.assertEqual(doc_row(p.doc(), "F-NEW-01")[4:], [shown(MISS), "—"], "a typed validated status with no state is no proof")
        board = scoreboard(p.doc())
        self.assertEqual((board["total"], board[OK], board[REV], board[MISS]), (2, 0, 1, 1),
                         "the scoreboard counts what the state says, not what was typed")


class ConfigurationTests(EngineCase):
    def test_since_alone_runs_the_changed_scope(self):
        """Mode --since: alone it means --changed --since, never a full run."""
        doc = doc_text(row("F-RT-01", "Rules A", "Rules.swift", "RulesTests/testA()"),
                       row("F-TAG-01", "Untouched", "HomeView.swift", "Tagged/tag sentence"))
        self.fake.git_since = ["Demo/Core/Rules.swift"]
        p = self.project(doc=doc, state=make_state())
        r = self.run_engine(p, "--since", "main", xcode=["auto"] * 2)
        self.assertEqual(p.state()["run"]["mode"], "changed", r.text)
        self.assertEqual(p.state()["run"]["scope_ids"], ["F-RT-01"], "only the flow citing the changed file")

    def test_default_sources_index_app_and_test_folders(self):
        """Configuration: without a sources key the app folder and the test folders are indexed."""
        doc = doc_text(row("F-RULE-01", "Rules", "Rules.swift", "RulesTests/testA()", OK, "2026-01-10"))
        p = self.project(doc=doc, state=KEEP, config={"sources": DEL})
        p.set_state(make_state({"F-RULE-01": frec(OK, ["RulesTests/testA()"], fp=p.fp("Rules.swift"),
                                                  when="2026-01-10", run=RUN1)}, seed("RulesTests/testA()")))
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-RULE-01", OK, "Demo/Core/Rules.swift is found under the default sources")


class ToolingTests(EngineCase):
    def test_motor_package_runs_from_its_folder(self):
        """Package branch (games): an xcodebuild package runs from inside the package with its own scheme and folder."""
        doc = doc_text(row("F-MENU-01", "Menu", "", "MenuTests/testOpens()"),
                       row("F-ECO-01", "Economy", "", "Economy/cost doubles per level"))
        g = self.project("DemoGame", doc=doc, state=make_state())
        r = self.run_engine(g, "--only-stale", xcode=["auto", "auto"])
        self.assertEqual(r.code, 0, r.text)
        cmds = self.fake.xcode_calls()
        package = [c for c in cmds if "-scheme" in c.cmd and arg_after(c.cmd, "-scheme") == "Physics"]
        app = [c for c in cmds if c not in package]
        self.assertEqual((len(package), len(app)), (1, 1), "one command for the package, one for the app")
        pc = package[0]
        self.assertEqual(os.path.realpath(pc.cwd), os.path.realpath(g.root / "Packages/Physics"), "run from inside the package")
        self.assertNotIn("-project", pc.cmd)
        dd = self.home / "Library/Developer/Xcode/DerivedData/flows-DemoGame"
        self.assertEqual(os.path.realpath(arg_after(pc.cmd, "-derivedDataPath")), os.path.realpath(str(dd / "Physics")))
        self.assertEqual(only_testing(pc.cmd), ["PhysicsTests/EconomyTests"])
        self.assertRegex(arg_after(pc.cmd, "-resultBundlePath"), r"-(Physics|PhysicsTests)\.xcresult$")
        ac = app[0]
        self.assertEqual(os.path.realpath(arg_after(ac.cmd, "-project")), os.path.realpath(g.root / "DemoGame.xcodeproj"))
        self.assertEqual(arg_after(ac.cmd, "-scheme"), "DemoGame")
        self.assertEqual(only_testing(ac.cmd), ["DemoGameTests/MenuTests"])
        self.assertNotEqual(arg_after(ac.cmd, "-resultBundlePath"), arg_after(pc.cmd, "-resultBundlePath"))

    def test_package_build_folder_is_a_subfolder_of_the_project_build_folder(self):
        """Package branch: a project has one build folder, so the package's derived data is
        <derived_data>/<scheme>, never the sibling <derived_data>-<scheme>; --derived-data moves both together."""
        doc = doc_text(row("F-ECO-01", "Economy", "", "Economy/cost doubles per level"))
        g = self.project("DemoGame", doc=doc, state=make_state())
        r = self.run_engine(g, "--only-stale", xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        dd = self.home / "Library/Developer/Xcode/DerivedData/flows-DemoGame"
        got = arg_after(self.fake.xcode_calls()[0].cmd, "-derivedDataPath")
        self.assertEqual(os.path.realpath(got), os.path.realpath(str(dd / "Physics")), "a subfolder of the build folder")
        self.assertFalse(got.endswith("flows-DemoGame-Physics"), f"never the sibling folder: {got}")
        self.assertEqual(os.path.realpath(os.path.dirname(got)), os.path.realpath(str(dd)), "its parent is the build folder")
        custom = self.tmp / "custom-dd"
        h = self.project("DemoGame", doc=doc, state=make_state())
        self.fake.calls.clear()
        r = self.run_engine(h, "--only-stale", "--derived-data", str(custom), xcode=["auto"])
        self.assertEqual(r.code, 0, r.text)
        got = arg_after(self.fake.xcode_calls()[0].cmd, "-derivedDataPath")
        self.assertEqual(os.path.realpath(got), os.path.realpath(str(custom / "Physics")), "--derived-data parents the package folder")

    def test_swift_test_package_reads_both_reports(self):
        """Package branch (swift test): both xunit reports are read, a failure in either fails the case."""
        doc = doc_text(row("F-CA-01", "XCTest case", "", "DemoCoreTests/CoreRulesTests/testRule"),
                       row("F-CB-01", "Swift Testing case", "", "DemoCoreTests/EngineTests/engineRuns()"),
                       row("F-CC-01", "Swift Testing failure", "", "DemoCoreTests/EngineTests/engineFails()"),
                       row("F-CD-01", "XCTest failure", "", "DemoCoreTests/CoreRulesTests/testBroken"))
        m = self.project("DemoMac", doc=doc, state=make_state())
        r = self.run_engine(m, "--only-stale", swift=["core"])
        calls = self.fake.swift_calls()
        self.assertEqual(len(calls), 1, "swift test has no per-class selection: one run for the package")
        cmd = calls[0].cmd
        self.assertEqual(cmd[:2], ["swift", "test"])
        package = arg_after(cmd, "--package-path")
        self.assertTrue(package == "Packages/DemoCore" or os.path.realpath(package) == os.path.realpath(m.root / "Packages/DemoCore"))
        out = arg_after(cmd, "--xunit-output")
        self.assertTrue(os.path.realpath(out).startswith(os.path.realpath(
            self.home / "Library/Developer/Xcode/DerivedData/flows-DemoMac/results") + os.sep), out)
        self.assertEqual(self.fake.xcode_calls(), [], "a package of the swift-test kind never goes through xcodebuild")
        self.assertStatus(m, "F-CA-01", OK, "passed in the XCTest report")
        self.assertStatus(m, "F-CB-01", OK, "passed in the Swift Testing report")
        self.assertStatus(m, "F-CC-01", BROKEN, "failed in the Swift Testing report")
        self.assertIn("failed", self.note(m, "F-CC-01"))
        self.assertStatus(m, "F-CD-01", BROKEN, "failed in the XCTest report")
        # Swift Testing only: the main report file is absent
        only = doc_text(row("F-CB-01", "Swift Testing case", "", "DemoCoreTests/EngineTests/engineRuns()"))
        q = self.project("DemoMac", doc=only, state=make_state())
        self.run_engine(q, "--only-stale", swift=["stonly"])
        self.assertStatus(q, "F-CB-01", OK, "a package with no XCTest case writes no main report; the sibling is enough")

    def test_help_lists_every_mode(self):
        """The script is runnable by itself and its --help names every documented mode and flag."""
        proc = subprocess.run([sys.executable, "-B", str(ENGINE_PATH), "--help"], cwd=self.tmp,
                              capture_output=True, text=True, env=dict(os.environ, HOME=str(self.home)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--only-stale", "--resume", "--invalidate", "--invalidate-classes", "--from-cache", "--reuse", "--ui",
                     "--check", "--changed", "--since", "--device", "--derived-data", "--result-path", "--batch-size",
                     "--today", "--prune-state", "--project", "--language", "--init"):
            self.assertIn(flag, proc.stdout, f"--help does not name {flag}")

    def test_script_forwards_args_and_exit_code_and_finds_its_project(self):
        """Run as a program: the engine's exit code, the project found from a folder inside it, no bytecode."""
        a, b = self.project(), self.project()
        env = dict(os.environ, HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        inside = a.root / "docs"
        proc = subprocess.run([sys.executable, "-B", str(ENGINE_PATH), "--from-cache", "--today", "2026-03-01"],
                              cwd=inside, capture_output=True, text=True, env=env)
        direct = self.run_engine(b, "--from-cache", "--today", "2026-03-01")
        self.assertEqual(proc.returncode, 1, "the engine's exit code for a map with a broken flow:\n" + proc.stdout + proc.stderr)
        self.assertEqual(direct.code, 1)
        self.assertEqual(a.snapshot(), b.snapshot(), "from a subfolder, with no --project, the same project is judged")
        self.assertEqual(list(a.root.parent.rglob("__pycache__")), [], "no bytecode is left in the project")
        outside = subprocess.run([sys.executable, "-B", str(ENGINE_PATH), "--from-cache"], cwd=self.tmp,
                                 capture_output=True, text=True, env=env)
        self.assertEqual(outside.returncode, 2, "with no scripts/flows.json above the folder the configuration is missing")
        self.assertIn("configuration not found", outside.stderr)

    def test_the_bin_wrapper_runs_the_script(self):
        sh = find_sh()
        if sh is None:
            self.skipTest("no POSIX shell on this machine")
        wrapper = HERE.parent / "bin" / "check-flows"
        proc = subprocess.run([sh, posix(wrapper), "--help"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--only-stale", proc.stdout)

    def test_console_output_is_english(self):
        """Extra: output language; status words stay the map's."""
        p = self.project()
        r = self.run_engine(p, "--from-cache")
        counts = summary(r.out)
        self.assertEqual(counts, {"total": 19, OK: 7, REV: 4, MISS: 2, DEV: 1, BROKEN: 5},
                         "the summary line, counted by hand from the static map:\n" + r.out)
        self.assertEqual(scoreboard(p.doc()), counts, "the doc's scoreboard and the console agree")
        outputs = [r.out]
        q = self.project(doc=doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()")), state=make_state())
        outputs.append(self.run_engine(q, "--only-stale", xcode=["auto"]).out)
        outputs.append(self.run_engine(self.project(state=make_state()), "--resume").text)
        for text in outputs:
            self.assertTrue(text.isascii(), "console text must be plain ASCII English: " + text[:300])
            for phrase in ("lote ", "fluxos", "nada para retomar", "nenhum", "rodada", "classe sem", "prova nao"):
                self.assertNotIn(phrase, text.lower(), f"pt-BR phrase '{phrase}' in the console")
        self.assertIn("nothing to resume", outputs[2])


# ---------------------------------------------------------------------------- docker projects (Vitest and Playwright in containers)

CRY_1 = "crypto.service.spec/CryptoService > encrypts and decrypts a key"
CRY_2 = "crypto.service.spec/CryptoService > with a wrong master key > fails to decrypt"
CRY_SLOW = "crypto.service.spec/CryptoService > with a wrong master key > is slow"
CRY_TODO = "crypto.service.spec/CryptoService > rotates the master key"
CRY_TOP = "crypto.service.spec/top level case"
GAT_1 = "gateway.int.spec/gateway > returns 429 with Retry-After when the rate limit is exceeded"
CAT_1 = "catalog.unit/catalog parity > both languages have the same keys"
PRV_1 = "providers.e2e/providers > creates a provider"
PRV_2 = "providers.e2e/providers > syncs the catalog"
PAGE_PROVIDERS = "apps/web/src/app/(dashboard)/providers/page.tsx"


def system_doc(*extra):
    return doc_text(row("F-CRY-01", "A key is encrypted", "crypto.service.ts", CRY_1),
                    row("F-CRY-02", "A wrong master key fails", "crypto.service.ts", CRY_2),
                    row("F-GAT-01", "The gateway limits calls", "gateway.service.ts", GAT_1),
                    row("F-CAT-01", "Catalog parity", "en.json", CAT_1),
                    row("F-PRV-01", "A provider is created", PAGE_PROVIDERS, PRV_1), *extra)


def dseed(*proofs, runner="api", result="Passed", when="2026-01-10", run=RUN1):
    """(by_path, runnable) for these docker proofs, as a past run would have stored them."""
    return ({p: {"result": result, "when": when, "run_id": run} for p in proofs},
            {p: f"{runner}/run" for p in proofs})


def sha1_paths(*paths):
    hasher = hashlib.sha1()
    for path in paths:
        hasher.update(pathlib.Path(path).read_bytes())
    return hasher.hexdigest()


class TypeScriptScanTests(unittest.TestCase):
    """The declared tests of a TypeScript test file: the scan the offline 'gone' check stands on."""

    def declared(self, text):
        tests, loose = check_flows.declared_ts_tests(text)
        return [t for t, _ in tests], loose

    def test_describe_chain_and_test_name_are_joined_by_the_reporters_separator(self):
        tests, loose = self.declared("""
describe('outer', () => {
  it('first', () => {});
  describe('inner', () => {
    test("second", async () => {});
  });
});
it('top', () => {});
""")
        self.assertEqual(tests, ["outer > first", "outer > inner > second", "top"])
        self.assertFalse(loose)

    def test_skip_todo_fixme_and_only_are_declared_with_their_mode(self):
        tests, loose = check_flows.declared_ts_tests("""
describe('suite', () => {
  it.skip('skipped', () => {});
  it.todo('todo');
  test.fixme('fixme', () => {});
  it.only('only', () => {});
  xit('x-prefixed', () => {});
  it('plain', () => {});
});
describe.skip('skipped suite', () => { it('inherits', () => {}); });
""")
        self.assertEqual(tests, [("suite > skipped", "skip"), ("suite > todo", "skip"), ("suite > fixme", "skip"),
                                 ("suite > only", "only"), ("suite > x-prefixed", "skip"), ("suite > plain", "run"),
                                 ("skipped suite > inherits", "skip")])
        self.assertFalse(loose)

    def test_comments_strings_templates_and_regexes_never_declare_or_break_a_test(self):
        tests, loose = self.declared("""
// it('line comment', () => {})
/* see a/b: describe('block comment', () => { it('inside', () => {}) }) */
describe('real', () => {
  it('has a string with ) and ( in it', () => {
    const text = "it('fake', () => {})";
    const other = `describe('fake too', () => {})`;
    expect(/it\\(['"]x['"]\\)/.test(text)).toBe(false);
    expect(10 / 2 / 5).toBe(1);
  });
  it(`literal template`, () => {});
  const ratio = 10 / 2; it('after a division', () => {}); const back = 1 / 2;
});
""")
        self.assertEqual(tests, ["real > has a string with ) and ( in it", "real > literal template",
                                 "real > after a division"])
        self.assertFalse(loose)

    def test_member_calls_and_annotations_are_not_declarations(self):
        tests, loose = self.declared("""
test.describe.configure({ mode: 'serial' });
test.use({ locale: 'pt-BR' });
test.describe('page', () => {
  test.slow();
  helper.test('not a test', () => {});
  test.beforeEach(async () => {});
  test('works', async ({ page }) => {
    await test.step('inside a test', async () => { regex.test('x'); });
    test.slow();
  });
  test.afterAll(async () => {});
});
""")
        self.assertEqual(tests, ["page > works"])
        self.assertFalse(loose)

    def test_a_title_the_scan_cannot_read_makes_the_file_loose(self):
        for source in ("it.each([1, 2])('case %i', () => {});",
                       "const name = 'x'; it(name, () => {});",
                       "it(`built ${name}`, () => {});",
                       "it('a' + 'b', () => {});",
                       "describe(title, () => { it('inner', () => {}); });",
                       "it.each`a|b`('case', () => {});",
                       "describe('open', () => { it('unbalanced', () => {};"):
            tests, loose = check_flows.declared_ts_tests(source)
            self.assertTrue(loose, f"must be loose: {source}")
        tests, loose = self.declared("describe(title, () => { it('inner', () => {}); });")
        self.assertEqual(tests, [], "a test inside a describe with an unreadable title has no known full title")

    def test_javascript_escapes_in_a_title_are_read_as_the_reporter_prints_them(self):
        tests, _ = self.declared(r"it('it\'s \"quoted\" \\ \u00e9 \x41 \u{1F600} tab\tend', () => {});")
        self.assertEqual(tests, ["it's \"quoted\" \\ \u00e9 A \U0001F600 tab\tend"])

    def test_a_playwright_separator_reads_as_the_engines_ascii_one(self):
        self.assertEqual(check_flows.norm_title("providers › creates a provider"), "providers > creates a provider")
        self.assertEqual(check_flows.norm_title("no separator"), "no separator")

    def test_the_file_stem_drops_the_last_extension_only(self):
        for given, stem in (("test/unit/crypto.service.spec.ts", "crypto.service.spec"),
                            ("providers.e2e.ts", "providers.e2e"), ("auth\\login.e2e.tsx", "login.e2e"),
                            ("page.test.mjs", "page.test"), ("vitest unhandled errors", "vitest unhandled errors")):
            self.assertEqual(check_flows.stem_of(given), stem, given)

    def test_a_test_declared_through_a_renamed_extend_base_is_found_with_the_same_forms(self):
        tests, loose = check_flows.declared_ts_tests("""
import { test as base } from '@playwright/test';
const myTest = base.extend<{ page: Page }>({ page: async ({}, use) => { await use(1); } });
export const other = myTest.extend({});
const fixtureTest = test.extend({});
myTest('plain', async () => {});
myTest.describe('group', () => { other('inner', async () => {}); myTest.beforeEach(async () => {}); });
myTest.skip('skipped', async () => {});
myTest.describe.configure({ mode: 'serial' });
fixtureTest('starts with f, is not an only', () => {});
const schema = z.object({}).extend({});
""")
        self.assertEqual(tests, [("plain", "run"), ("group > inner", "run"), ("skipped", "skip"),
                                 ("starts with f, is not an only", "run")])
        self.assertFalse(loose)

    def test_a_slash_after_an_increment_or_a_decrement_is_a_division(self):
        for operator in ("++", "--"):
            tests, loose = self.declared(f"let i = 0; i{operator} / 2; it('after', () => {{}}); const r = a / b;")
            self.assertEqual(tests, ["after"], operator)
            self.assertFalse(loose, operator)

    def test_a_slash_after_a_closing_tag_opener_is_not_a_regular_expression(self):
        tests, loose = self.declared("it('x', () => { render(<a>x</a>); }); it('second', () => { render(<b>y</b>); });")
        self.assertEqual(tests, ["x", "second"])
        self.assertFalse(loose)

    def test_jsx_in_a_tsx_file_is_read_so_common_testing_library_files_are_not_loose(self):
        source = """
import { render, screen } from '@testing-library/react';
describe('Button', () => {
  it('renders the label', () => {
    render(<Button onClick={() => {}}>Don't click, it's "odd" // not a comment</Button>);
    expect(screen.getByText(/click/i)).toBeTruthy();
  });
  it('renders a self closing tag and a list', () => {
    const { container } = render(<><Icon name="a/b" />{items.map((i) => <li key={i}>{i}'s</li>)}</>);
  });
  it('compares values', () => { expect(a < b).toBe(true); const r = a<b ? 1 : 2; });
  it('has a generic arrow', () => { const f = <T,>(x: T) => x; const g = <T extends object>(x: T) => x; });
  it('keeps going after a nested element with text', () => { return <p>a <b>/x</b>, /y</p>; });
});
"""
        tests, loose = check_flows.declared_ts_tests(source, jsx=True)
        self.assertEqual([t for t, _ in tests],
                         ["Button > renders the label", "Button > renders a self closing tag and a list",
                          "Button > compares values", "Button > has a generic arrow",
                          "Button > keeps going after a nested element with text"])
        self.assertFalse(loose)

    def test_every_jsx_form_is_skipped_whole_so_the_quote_in_its_text_starts_nothing(self):
        """Each line holds one JSX form with an apostrophe in its text: read as code, the apostrophe would open a string
        that swallows the rest of the line and leave the parentheses unbalanced (the file loose)."""
        forms = {
            "after return": "it('a', () => { return <p>don't</p>; });",
            "inside a {} expression of an element": "it('a', () => { render(<ul>{ok && <li>it's</li>}don't</ul>); });",
            "nested in an element": "it('a', () => { render(<div><b>x</b>don't</div>); });",
            "self closing inside an element": "it('a', () => { render(<div><Icon />don't</div>); });",
            "a fragment": "it('a', () => { render(<>don't</>); });",
            "with attributes": "it('a', () => { render(<Button kind=\"x\" onClick={() => {}} {...rest}>don't</Button>); });",
        }
        for name, line in forms.items():
            tests, loose = check_flows.declared_ts_tests(line + "\nit('b', () => {});\n", jsx=True)
            self.assertEqual([t for t, _ in tests], ["a", "b"], name)
            self.assertFalse(loose, name)
            if "{ok" not in line:   # its two apostrophes pair up as code; the mutant of its skip is what loosens it
                _, as_code = check_flows.declared_ts_tests(line + "\nit('b', () => {});\n")
                self.assertTrue(as_code, f"{name}: read as code the line is loose, so the case proves the skip")

    def test_a_closing_tag_that_is_not_one_leaves_the_scan_reading_code_not_swallowing_the_file(self):
        tests, loose = check_flows.declared_ts_tests("it('a', () => { render(<div>x</ ); });\nit('b', () => {});\n", jsx=True)
        self.assertEqual([t for t, _ in tests], ["a", "b"], "an element that cannot close is read as ordinary code")
        self.assertFalse(loose)

    def test_a_jsx_element_the_scan_cannot_close_leaves_the_file_loose(self):
        tests, loose = check_flows.declared_ts_tests("it('a', () => { render(<div>don't); });", jsx=True)
        self.assertTrue(loose, "an element with no closing tag is not skipped, and the quote then breaks the parentheses")

    def test_a_name_pattern_matches_the_title_literally(self):
        for title in ("a (b) [c] {d} e|f g.h i+j k*l m?n ^o$ p\\q r/s", "plain words", "Retry-After 429"):
            pattern = check_flows.js_regex_escape(title)
            self.assertIsNotNone(re.fullmatch(pattern, title), title)
            self.assertIsNone(re.fullmatch(pattern, title + "x"), title)


class RustScanTests(unittest.TestCase):
    def test_module_paths_nest_and_comments_strings_and_macros_are_read_right(self):
        text = """
#[cfg(test)]
mod tests {
    // #[test] fn commented() {}
    const S: &str = "#[test] fn in_text() { {";
    const R: &str = r#"mod fake { "#;
    const C: char = '{';
    mod deep {
        #[test]
        fn blink() { let a = 1; { let _ = a; } }
    }
    fn helper() {}
    #[test]
    fn after() {}
}
"""
        tests, loose = check_flows.declared_rust_tests(text, "face")
        self.assertEqual([t for t, _ in tests], ["face::tests::deep::blink", "face::tests::after"])
        self.assertFalse(loose)
        self.assertTrue(check_flows.declared_rust_tests("macro_rules! m { () => {} }")[1],
                        "a macro may declare tests the scan cannot see")

    def test_each_file_maps_to_the_binary_nextest_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            crate = root / "crates" / "core"
            for name in ("src/a/mod.rs", "src/a/b.rs", "src/lib.rs", "src/main.rs", "src/bin/x.rs", "tests/one.rs",
                         "tests/two/main.rs", "tests/two/helper.rs"):
                (crate / name).parent.mkdir(parents=True, exist_ok=True)
                (crate / name).write_text("")
            (crate / "Cargo.toml").write_text('[package]\nname = "alfa-core"\n')
            place = lambda name: check_flows.rust_binary(crate / name, root)
            self.assertEqual(place("src/lib.rs"), ("alfa-core", ""))
            self.assertEqual(place("src/a/mod.rs"), ("alfa-core", "a"))
            self.assertEqual(place("src/a/b.rs"), ("alfa-core", "a::b"))
            self.assertEqual(place("tests/one.rs"), ("alfa-core::one", ""))
            self.assertEqual(place("tests/two/main.rs"), ("alfa-core::two", ""))
            for unknown in ("src/main.rs", "src/bin/x.rs", "tests/two/helper.rs"):
                self.assertIsNone(place(unknown), unknown)


class JUnitReportTests(unittest.TestCase):
    """Reading the reports Vitest and Playwright write, built from their documented shapes."""

    def test_the_vitest_shape_is_read_as_the_reporter_writes_it(self):
        by_path, runnable, counts = check_flows.parse_junit(FIXTURES / "junit" / "vitest-shape.xml", "api",
                                                            "2026-03-01", "R")
        self.assertEqual({k: v["result"] for k, v in by_path.items()},
                         {"sample.spec/sample suite > adds two numbers": "Failed",
                          "sample.spec/sample suite > subtracts two numbers": "Passed"})
        self.assertEqual(set(runnable.values()), {"api/run"})
        self.assertEqual(counts, {"Passed": 1, "Failed": 1, "Skipped": 0})

    def test_the_playwright_shape_reads_the_guillemet_path_and_the_file_in_a_folder(self):
        by_path, runnable, counts = check_flows.parse_junit(FIXTURES / "junit" / "playwright-shape.xml", "e2e",
                                                            "2026-03-01", "R")
        self.assertEqual({k: v["result"] for k, v in by_path.items()},
                         {"providers.e2e/providers > creates a provider": "Passed",
                          "providers.e2e/providers > syncs the catalog": "Failed",
                          "providers.e2e/providers > keeps a note": "Skipped",
                          "login.e2e/signs in": "Passed"})
        self.assertEqual(counts, {"Passed": 2, "Failed": 1, "Skipped": 1})

    def test_an_error_fails_the_case_and_the_worst_of_a_repeated_key_wins(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "r.xml"
        path.write_text('''<testsuites>
<testsuite name="a.e2e.ts"><testcase name="t › one" classname="a.e2e.ts"/>
<testcase name="t › one" classname="a.e2e.ts"><failure message="x"/></testcase>
<testcase name="t › two" classname="a.e2e.ts"><error message="boom"/></testcase>
<testcase name="t › three" classname="a.e2e.ts"/><testcase name="t › three" classname="a.e2e.ts"><skipped/></testcase>
<testcase name="t › four" classname="a.e2e.ts"><skipped/></testcase><testcase name="t › four" classname="a.e2e.ts"><skipped/></testcase>
<testcase name="t › flaky" classname="a.e2e.ts"><flakyFailure message="first try"/></testcase>
<testcase name="" classname="a.e2e.ts"/></testsuite></testsuites>''')
        by_path, _, _ = check_flows.parse_junit(path, "e2e", "2026-03-01", "R")
        self.assertEqual({k.split("/", 1)[1]: v["result"] for k, v in by_path.items()},
                         {"t > one": "Failed", "t > two": "Failed", "t > three": "Passed", "t > four": "Skipped",
                          "t > flaky": "Passed"})

    def test_the_fake_runner_writes_what_the_documented_shapes_say(self):
        """The fake docker seam is checked against the fixtures' own shapes before any engine test relies on it."""
        text = junit_xml("vitest", [jc("api", "test/a.spec.ts", ["outer", "inner", "it"], "Failed")])
        root = ET.fromstring(text)
        case = root.find("testsuite/testcase")
        self.assertEqual((root.tag, case.get("classname"), case.get("name")), ("testsuites", "test/a.spec.ts", "outer > inner > it"))
        self.assertIsNotNone(case.find("failure"))
        text = junit_xml("playwright", [jc("e2e", "a.e2e.ts", ["outer", "it"], "Skipped")])
        case = ET.fromstring(text).find("testsuite/testcase")
        self.assertEqual((case.get("classname"), case.get("name")), ("a.e2e.ts", "outer › it"))
        self.assertIsNotNone(case.find("skipped"))
        for fixture in ("vitest-shape.xml", "playwright-shape.xml"):
            for case in ET.parse(str(FIXTURES / "junit" / fixture)).getroot().iter("testcase"):
                self.assertTrue(case.get("classname") and case.get("name"), fixture)



class DockerConfigurationTests(EngineCase):
    def refused(self, message, **changes):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state(), config=changes)
        before = p.snapshot()
        r = self.run_engine(p, "--from-cache")
        self.assertEqual(r.code, 2, f"{message}:\n" + r.text)
        self.assertIn(message, r.err)
        self.assertUntouched(p, before, "an unusable configuration writes nothing")
        self.assertEqual(self.fake.calls, [], "and runs nothing")

    def runner(self, **changes):
        base = {"name": "api", "framework": "vitest", "command": ["docker", "compose", "run", "api-test"],
                "junit_dir": "test-results/api", "tests": ["apps/api/test"]}
        base = {k: v for k, v in base.items() if changes.get(k, 0) is not DEL}
        base.update({k: v for k, v in changes.items() if v is not DEL})
        return base

    def test_a_docker_configuration_that_cannot_run_is_refused_naming_the_key(self):
        self.refused('a docker project needs a non-empty "runners" list', runners=[])
        self.refused("runner 'api' is declared twice", runners=[self.runner(), self.runner()])
        self.refused("needs a \"name\"", runners=[self.runner(name="bad/name")])
        self.refused('runner api needs "command"', runners=[self.runner(command=[])])
        self.refused('runner api needs "framework"', runners=[self.runner(framework="jest")])
        self.refused('runner api needs "junit_dir"', runners=[self.runner(junit_dir=DEL)])
        self.refused('runner api needs "junit_dir"', runners=[self.runner(junit_dir="/abs/place")])
        self.refused('runner api needs "junit_dir"', runners=[self.runner(junit_dir="../outside")])
        self.refused('runner api needs "tests"', runners=[self.runner(tests=[])])
        self.refused('"only_args" must be a list of strings', runners=[self.runner(only_args="-t")])
        self.refused('"ui" must be true or false', runners=[self.runner(ui="yes")])
        self.refused('a docker project needs a "sources" key', sources=DEL)
        self.refused("unit_targets do not apply to a docker project",
                     unit_targets=[{"name": "T", "dir": "T"}])
        self.refused("platform must be ios, macos, docker or rust", platform="android")

    def test_docker_defaults_are_typescript_sources_and_a_results_folder_inside_the_project(self):
        cfg = check_flows.Config(self.project("DemoSystem", config={"source_patterns": DEL}).root, mock.Mock(derived_data=None))
        self.assertEqual(cfg.source_patterns, ["*.ts", "*.tsx", "*.json"])
        self.assertTrue(cfg.results_dir.as_posix().endswith("DemoSystem/test-results/flows"))
        self.assertEqual(cfg.ui_names, {"e2e"})
        self.assertEqual(cfg.runners["api"]["only_args"], ["-t", "{pattern}"], "Vitest narrows by test name")
        self.assertEqual(cfg.runners["e2e"]["only_args"], ["--grep", "{pattern}"], "Playwright narrows by grep")
        self.assertEqual(cfg.runners["api"]["tests"][1], ("apps/api/test/integration", ["*.int.spec.ts"]))
        p = self.project("DemoSystem", config={"runners": [self.runner(tests=["apps/api/test"])]})
        again = check_flows.Config(p.root, mock.Mock(derived_data=None))
        self.assertEqual(again.runners["api"]["tests"],
                         [("apps/api/test", ["*.spec.ts", "*.test.ts", "*.spec.tsx", "*.test.tsx", "*.e2e.ts"])],
                         "a bare folder scans the usual Vitest and Playwright test file patterns")

    def test_the_map_files_of_a_docker_project_are_cited_with_dots_and_folders(self):
        cfg = check_flows.Config(self.project("DemoSystem").root, mock.Mock(derived_data=None))
        cell = "crypto.service.ts:12, apps/web/src/app/(dashboard)/providers/page.tsx and systems/page.tsx, en.json; (see index.ts)"
        self.assertEqual(sorted(set(cfg.source_re.findall(cell))),
                         ["apps/web/src/app/(dashboard)/providers/page.tsx", "crypto.service.ts", "en.json",
                          "index.ts", "systems/page.tsx"])


class DockerRunTests(EngineCase):
    """A docker project's modes, run on a fake runtime that answers the compose commands with JUnit XML."""

    def shown(self, p, fid):
        """The Status word the map shows for a flow (a flow never judged has no state record)."""
        return norm(doc_row(p.doc(), fid)[4])

    def assertShown(self, p, fid, expected, message):
        self.assertEqual(self.shown(p, fid), expected, f"{fid}: {message}")

    def results_folders(self, p):
        base = p.root / "test-results" / "flows"
        return sorted(base.iterdir()) if base.is_dir() else []

    def test_a_full_run_runs_every_non_ui_runner_and_judges_by_the_reports(self):
        """None mode: every non-UI runner runs (the Playwright one waits for --ui), proofs are `stem/full title`."""
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual([c.cmd for c in self.fake.docker_calls()],
                         [COMPOSE_RUN + ["api-test"], COMPOSE_RUN + ["web-test"]],
                         "the api runner, then the web one; the browser runner needs --ui; no narrowing in a full run")
        self.assertTrue(all(c.cwd == str(p.root) for c in self.fake.docker_calls()), "run from the project folder")
        for fid in ("F-CRY-01", "F-CRY-02", "F-GAT-01", "F-CAT-01"):
            self.assertStatus(p, fid, OK, "its test passed in the report")
        self.assertShown(p, "F-PRV-01", MISS, "its runner is a UI one and did not run")
        self.assertEqual(p.state()["flows"]["F-CRY-01"]["validated_at"], "2026-03-01")
        self.assertEqual(summary(r.out), {"total": 5, OK: 4, REV: 0, MISS: 1, DEV: 0, BROKEN: 0})
        self.assertEqual(scoreboard(p.doc()), summary(r.out))
        self.assertIn("runner api: 9 passed, 0 failed, 2 skipped (exit 0)", r.out,
                      "the unit and the integration report of one command are both read")
        self.assertEqual(p.state()["raw"]["runnable"][CRY_1], "api/run")
        self.assertEqual(p.state()["raw"]["runnable"][CAT_1], "web/run")

    def test_ui_adds_the_browser_runner_and_its_playwright_titles_read_the_same_proof(self):
        """Mode --ui: Playwright's report (title path joined by a guillemet) is cited with the ASCII separator."""
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, "--ui", docker=["auto", "auto", "auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual([c.cmd[-1] for c in self.fake.docker_calls()], ["api-test", "web-test", "e2e"],
                         "the UI runner goes last")
        self.assertStatus(p, "F-PRV-01", OK, "providers › creates a provider is providers > creates a provider")
        self.assertEqual(p.state()["run"]["classes_planned"], ["api/run", "web/run", "e2e/run"])
        # the same proof typed with the reporter's own separator reads the same test
        typed = doc_text(row("F-PRV-01", "Provider", PAGE_PROVIDERS, "providers.e2e/providers › creates a provider"))
        q = self.project("DemoSystem", doc=typed, state=make_state())
        self.run_engine(q, "--ui", docker=["auto", "auto", "auto"])
        self.assertStatus(q, "F-PRV-01", OK, "a guillemet in the Prova cell is read as the ASCII separator")
        self.assertEqual(q.state()["flows"]["F-PRV-01"]["proofs"], [PRV_1])

    def test_a_full_run_runs_a_runner_no_flow_cites_yet(self):
        p = self.project("DemoSystem", doc=doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1)), state=make_state())
        self.run_engine(p, docker=["auto", "auto"])
        self.assertEqual([c.cmd[-1] for c in self.fake.docker_calls()], ["api-test", "web-test"],
                         "a full run is every non-UI runner, cited or not")

    def test_each_run_archives_its_reports_in_a_new_folder_and_nothing_is_overwritten_or_removed(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        with deletion_spy() as deleted:
            self.run_engine(p, docker=["auto", "auto"])
            first = self.results_folders(p)
            self.assertEqual([f.name for f in first],
                             ["flows-20260301T100000-full-b1-100000", "flows-20260301T100000-full-b2-100000"],
                             "one folder per batch, named by run id, batch and time")
            archived = sorted(x.relative_to(first[0]).as_posix() for x in first[0].rglob("*.xml"))
            self.assertEqual(archived, ["api/integration.xml", "api/unit.xml"],
                             "batch 1 holds the api runner; web is batch 2")
            self.assertEqual(sorted(x.relative_to(first[1]).as_posix() for x in first[1].rglob("*.xml")), ["web/junit.xml"])
        self.assertEqual(deleted, [], "no report, folder or result is ever deleted")
        again = self.results_folders(p)
        before = {str(x): hashlib.sha1(x.read_bytes()).hexdigest() for f in again for x in f.rglob("*.xml")}
        self.fake.reset()
        self.run_engine(p, docker=["auto", "auto"])
        later = self.results_folders(p)
        self.assertEqual(len(later), 4, "a second run in the same second takes new, numbered folders")
        for path, digest in before.items():
            self.assertEqual(hashlib.sha1(pathlib.Path(path).read_bytes()).hexdigest(), digest,
                             "an earlier run's archived report is never rewritten")
        self.assertTrue(any(f.name.endswith("-2") for f in later), [f.name for f in later])
        runner_xml = sorted(x.name for x in (p.root / "test-results" / "api").glob("*.xml"))
        self.assertEqual(runner_xml, ["integration.xml", "unit.xml"], "the runner's own folder is left as it wrote it")

    def test_only_stale_runs_only_the_cited_tests_of_each_runner(self):
        """Mode --only-stale narrows: Vitest by -t, Playwright by --grep, one regular expression per runner."""
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, "--only-stale", docker=["auto", "auto", "auto"])
        self.assertEqual(r.code, 0, r.text)
        calls = [c.cmd for c in self.fake.docker_calls()]
        pattern = ("CryptoService.*encrypts and decrypts a key|CryptoService.*with a wrong master key.*fails to decrypt"
                   "|gateway.*returns 429 with Retry-After when the rate limit is exceeded")
        self.assertEqual(calls, [COMPOSE_RUN + ["api-test", "-t", pattern],
                                 COMPOSE_RUN + ["web-test", "-t", "catalog parity.*both languages have the same keys"],
                                 COMPOSE_RUN + ["e2e", "--grep", "providers.*creates a provider"]])
        for fid in ("F-CRY-01", "F-CRY-02", "F-GAT-01", "F-CAT-01", "F-PRV-01"):
            self.assertStatus(p, fid, OK, "the narrowed run still proves exactly the tests cited")
        self.assertEqual(sorted(p.state()["run"]["cases_by_class"]), ["api/run", "e2e/run", "web/run"])
        names = sorted(x.name for x in self.results_folders(p)[0].rglob("*.xml"))
        self.assertEqual(names, ["integration.xml", "unit.xml"], "only the api runner's two reports in batch 1")
        self.assertEqual(sorted(k for k in p.state()["raw"]["by_path"] if k.startswith("crypto")), sorted([CRY_1, CRY_2]),
                         "Vitest reports the other cases of the file <skipped/>, but only the cited ones are stored")
        archived = (self.results_folders(p)[0] / "api" / "unit.xml").read_text()
        self.assertIn("<skipped/>", archived, "the archive holds the report as Vitest wrote it")
        self.assertNotIn("Skipped", {e["result"] for e in p.state()["raw"]["by_path"].values()})

    def test_a_cited_test_the_narrowed_run_did_not_report_is_broken_never_green_from_its_old_result(self):
        """A narrowed run proves only what its reports hold. Vitest reports a file that fails to load as one case named
        after the file, with a failure, and no case of the file: the tests selected by name from it have no result in
        this run, and their old Passed must not turn the flow green."""
        doc = doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1), row("F-GAT-01", "Gateway", "gateway.service.ts", GAT_1))
        p = self.project("DemoSystem", doc=doc, state=KEEP)
        flows = {"F-CRY-01": frec(REV, [CRY_1], fp="old", when="2026-01-10", run=RUN1, note="source changed"),
                 "F-GAT-01": frec(REV, [GAT_1], fp="old", when="2026-01-10", run=RUN1, note="source changed")}
        p.set_state(make_state(flows, dseed(CRY_1, GAT_1)))
        self.fake.load_failures = {U_SPEC}
        r = self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertEqual(r.code, 1, r.text)
        self.assertStatus(p, "F-CRY-01", BROKEN, "its file did not load, so the test it selected has no result in this run")
        self.assertIn("selected by name, absent from the run's report: " + CRY_1, self.note(p, "F-CRY-01"))
        self.assertStatus(p, "F-GAT-01", OK, "a test the report holds is judged by it")
        self.assertEqual(p.state()["raw"]["by_path"][CRY_1]["result"], "Absent",
                         "the stored result is replaced, so no later mode reads the old Passed")
        self.assertEqual(p.state()["raw"]["by_path"][GAT_1]["run_id"], p.state()["run"]["run_id"])
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-CRY-01", BROKEN, "the offline pass cannot turn it green either")
        # the file loads again: the next narrowed run proves the test and the marker is gone
        self.fake.load_failures = set()
        self.fake.now_value = NOW + datetime.timedelta(minutes=5)
        self.fake.reset()
        self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertStatus(p, "F-CRY-01", OK, "")
        self.assertEqual(p.state()["raw"]["by_path"][CRY_1]["result"], "Passed")

    def test_a_narrowed_vitest_run_stores_only_the_cited_tests_and_never_skipped_for_the_rest(self):
        """Vitest -t keeps the unselected tests in the file and reports them <skipped/>: the engine keeps only the
        cited keys, so no earlier result is overwritten, in the state and in --reuse of the engine's own archive."""
        doc = doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1), row("F-TOP-01", "Top", "crypto.service.ts", CRY_TOP))
        p = self.project("DemoSystem", doc=doc, state=KEEP)
        fp = p.fp("crypto.service.ts")
        flows = {"F-TOP-01": frec(OK, [CRY_TOP], fp=fp, when="2026-01-10", run=RUN1),
                 "F-CRY-01": frec(REV, [CRY_1], fp=fp, when="2026-01-10", run=RUN1)}
        p.set_state(make_state(flows, dseed(CRY_TOP, CRY_1)))
        r = self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertEqual(r.code, 0, r.text)
        archive = self.results_folders(p)[0]
        written = (archive / "api" / "unit.xml").read_text()
        self.assertEqual(written.count("<skipped/>"), written.count("<testcase") - 1,
                         "the fake models Vitest: every test -t did not select is reported <skipped/>")
        raw = p.state()["raw"]["by_path"]
        self.assertEqual(raw[CRY_TOP], {"result": "Passed", "when": "2026-01-10", "run_id": RUN1},
                         "an uncited test's earlier result is not overwritten")
        self.assertEqual(sorted(k for k in raw if k.startswith("crypto")), sorted([CRY_1, CRY_TOP]))
        self.assertNotIn("Skipped", {e["result"] for e in raw.values()}, "no Skipped is stored for a test no one selected")
        self.assertStatus(p, "F-TOP-01", OK, "")
        self.fake.reset()
        self.run_engine(p, "--reuse", str(archive))
        self.assertStatus(p, "F-TOP-01", OK, "the archive of a narrowed run is read for what it selected, not for the rest")
        self.assertEqual(p.state()["raw"]["by_path"][CRY_TOP]["result"], "Passed")
        self.assertStatus(p, "F-CRY-01", OK, "")
        self.assertEqual(sorted(k for k in p.state()["raw"]["runnable"] if k.startswith("crypto")),
                         sorted([CRY_1, CRY_TOP]), "and no class is recorded for a result that was not kept")
        # a fresh state reads the same archive: only what the narrowed run selected enters it
        q = self.project("DemoSystem", doc=doc, state=make_state())
        self.run_engine(q, "--reuse", str(archive))
        self.assertEqual(sorted(q.state()["raw"]["by_path"]), [CRY_1])
        self.assertEqual(sorted(q.state()["raw"]["runnable"]), [CRY_1])
        # a report in a folder below the runner's one is still covered by the runner's list
        nested = self.tmp / "nested" / "api" / "sub"
        nested.mkdir(parents=True)
        (nested.parent / "narrowed.json").write_text(json.dumps({"selected": [CRY_1]}))
        (nested / "x.xml").write_text(junit_xml("vitest", SYSTEM_API[:1] + SYSTEM_API[5:6]))
        t = self.project("DemoSystem", doc=doc, state=make_state())
        self.run_engine(t, "--reuse", str(nested.parent.parent))
        self.assertEqual(sorted(t.state()["raw"]["by_path"]), [CRY_1])
        # a list that cannot be read is no list: the whole report is read
        (nested.parent / "narrowed.json").write_text("{not json")
        u = self.project("DemoSystem", doc=doc, state=make_state())
        self.run_engine(u, "--reuse", str(nested.parent.parent))
        self.assertEqual(sorted(u.state()["raw"]["by_path"]), sorted([CRY_1, CRY_TOP]))

    def test_the_worst_result_of_a_key_reported_in_two_files_wins_in_a_run_and_in_reuse(self):
        """The same test in two report files (a unit and an integration run, or two browsers): Failed beats Passed
        whichever file is read first (integration.xml is read before unit.xml)."""
        first, second = SYSTEM_API[0]["chain"], SYSTEM_API[1]["chain"]
        master = [c for c in SYSTEM_MASTER if c["chain"] not in (first, second) or c["file"] != U_SPEC]
        master += [jc("api", U_SPEC, first, "Failed", report="integration.xml"), jc("api", U_SPEC, first, report="unit.xml"),
                   jc("api", U_SPEC, second, report="integration.xml"), jc("api", U_SPEC, second, "Failed", report="unit.xml")]
        doc = doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1), row("F-CRY-02", "Wrong key", "crypto.service.ts", CRY_2))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        p.master = master
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertStatus(p, "F-CRY-01", BROKEN, "Failed in the report read first, Passed in the one read last")
        self.assertStatus(p, "F-CRY-02", BROKEN, "Passed in the report read first, Failed in the one read last")
        self.assertIn("failed", self.note(p, "F-CRY-01"))
        q = self.project("DemoSystem", doc=doc, state=make_state())
        folder = self.tmp / "two-files" / "api"
        folder.mkdir(parents=True)
        for name in ("integration.xml", "unit.xml"):
            (folder / name).write_text(junit_xml("vitest", [c for c in master if c["report"] == name and c["file"] == U_SPEC
                                                            and c["chain"] in (first, second)]))
        self.run_engine(q, "--reuse", str(folder.parent))
        self.assertStatus(q, "F-CRY-01", BROKEN, "--reuse combines the files the same way")
        self.assertStatus(q, "F-CRY-02", BROKEN, "")

    def test_a_runners_summary_names_the_failed_cases_no_flow_cites_and_the_unhandled_errors(self):
        unhandled = jc("api", "vitest unhandled errors", ["Unhandled error: boom"], "Failed", report="unit.xml")
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        p.master = SYSTEM_MASTER + [unhandled]
        self.fake.overrides[("api", ("settings", "case 1"))] = "Failed"
        self.fake.overrides[("api", ("CryptoService", "encrypts and decrypts a key"))] = "Failed"
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertEqual(r.code, 1, r.text)
        self.assertIn("runner api: failed cases no flow cites (1):\n    settings.spec/settings > case 1\n", r.out,
                      "the cited failure (F-CRY-01) is a flow's business, the uncited one is listed")
        self.assertIn("runner api: vitest unhandled errors (1):\n    vitest unhandled errors/Unhandled error: boom\n", r.out)
        self.assertNotIn("runner web: failed cases", r.out, "a runner with nothing to name prints nothing more")
        self.assertNotIn("runner web: vitest unhandled", r.out)
        self.assertEqual(r.out.count("failed cases no flow cites"), 1)

    def test_a_narrowed_run_names_the_uncited_failure_the_load_of_a_file_reported(self):
        p = self.project("DemoSystem", doc=doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1)), state=make_state())
        self.fake.load_failures = {U_SPEC}
        r = self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertIn("runner api: failed cases no flow cites (1):\n    crypto.service.spec/" + U_SPEC + "\n", r.out,
                      "the case named after the file is the only trace of the load failure")

    def test_a_tsx_test_file_with_jsx_is_read_so_a_test_it_no_longer_declares_is_called_gone(self):
        """A loose file never calls a test gone: JSX in a .tsx file must not make the file loose."""
        proofs = ("button.unit/Button > renders the label", "button.unit/Button > was renamed")
        p = self.project("DemoSystem", doc=doc_text(row("F-BTN-01", "Button", "", proofs[0]), row("F-BTN-02", "Gone", "", proofs[1])),
                         state=KEEP)
        p.write("apps/web/test/button.unit.tsx", """import { render } from '@testing-library/react';
describe('Button', () => {
  it('renders the label', () => {
    render(<Button>Don't</Button>);
  });
  it('renders another', () => { render(<a>x</a>); });
});
""")
        flows = {"F-BTN-01": frec(OK, [proofs[0]], when="2026-01-10", run=RUN1),
                 "F-BTN-02": frec(OK, [proofs[1]], when="2026-01-10", run=RUN1)}
        p.set_state(make_state(flows, dseed(*proofs, runner="web")))
        self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-BTN-01", OK, "declared")
        self.assertStatus(p, "F-BTN-02", BROKEN, "the file is read whole, so what it does not declare is gone")
        self.assertIn("test no longer in the source", self.note(p, "F-BTN-02"))

    def test_a_runner_without_narrowing_or_with_a_file_filter_runs_as_configured(self):
        runners = json.loads((FIXTURES / "project_tree/DemoSystem/scripts/flows.json").read_text())["runners"]
        runners[0]["only_args"] = []                                        # api cannot narrow
        runners[1]["only_args"] = ["{files}", "-t", "{pattern}"]
        p = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"runners": runners})
        self.run_engine(p, "--only-stale", docker=["auto", "auto", "auto"])
        calls = [c.cmd for c in self.fake.docker_calls()]
        self.assertEqual(calls[0], COMPOSE_RUN + ["api-test"], "only_args [] keeps the runner whole")
        self.assertEqual(calls[1], COMPOSE_RUN + ["web-test", "catalog.unit.ts", "-t",
                                                  "catalog parity.*both languages have the same keys"],
                         "{files} is the basenames of the cited tests' files, {pattern} one expression")
        self.assertStatus(p, "F-CRY-01", OK, "a whole run judges its flows too")
        self.assertEqual(sorted(p.state()["run"]["cases_by_class"]), ["e2e/run", "web/run"])
        self.assertIn(CRY_TOP, p.state()["raw"]["by_path"], "a whole run stores every case of the runner")

    def test_a_special_character_in_a_title_is_escaped_in_the_name_pattern(self):
        doc = doc_text(row("F-A-01", "A", "", "x.spec/a (b) > c+d [e]"), row("F-B-01", "B", "", CRY_TOP))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        p.write("apps/api/test/unit/x.spec.ts", "describe('a (b)', () => { it('c+d [e]', () => {}); });\n")
        self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertEqual(self.fake.docker_calls()[0].cmd[-2:], ["-t", "a \\(b\\).*c\\+d \\[e\\]|top level case"],
                         "regular expression characters in a title are escaped; the pieces of a path are joined by .*")

    def test_a_report_left_by_an_earlier_run_is_never_read_as_this_runs(self):
        p = self.project("DemoSystem", doc=doc_text(row("F-CRY-01", "Key", "crypto.service.ts", CRY_1)), state=make_state())
        old = p.write("test-results/api/unit.xml", junit_xml("vitest", [SYSTEM_API[0]]), when=T_SRC)
        r = self.run_engine(p, "--only-stale", docker=["!exit1"])
        self.assertEqual(r.code, 1, "a runner that wrote no report is a failed batch\n" + r.text)
        self.assertIn("runner api wrote no report into test-results/api (exit 1)", r.out)
        self.assertIn("the image did not build", r.out, "the cause is printed")
        self.assertShown(p, "F-CRY-01", MISS, "the leftover XML proves nothing")
        self.assertEqual(self.results_folders(p), [], "nothing was archived")
        self.assertIn("class without a result in this run: api/run", r.out)
        self.assertTrue(old.exists(), "and the leftover is left where it is")

    def test_only_the_reports_a_run_wrote_or_rewrote_are_read(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        ghost = junit_xml("vitest", [jc("api", "test/ghost.spec.ts", ["ghost"])])
        p.write("test-results/api/ghost.xml", ghost)
        p.write("test-results/api/unit.xml", ghost)    # a leftover the command rewrites
        self.run_engine(p, "--ui", docker=["auto", "auto", "auto"])
        self.assertFalse([k for k in p.state()["raw"]["by_path"] if k.startswith("ghost")],
                         "a report the command did not touch is not this run's")
        self.assertStatus(p, "F-CRY-01", OK, "while the reports it wrote, or wrote over an old one, are")

    def test_a_failed_skipped_or_todo_test_breaks_its_flow_with_its_own_note(self):
        doc = doc_text(row("F-A-01", "Fails", "", CRY_1), row("F-B-01", "Skipped", "", CRY_SLOW),
                       row("F-C-01", "Todo", "", CRY_TODO), row("F-D-01", "Passes", "", CRY_TOP))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        self.fake.overrides[("api", ("CryptoService", "encrypts and decrypts a key"))] = "Failed"
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertEqual(r.code, 1, r.text)
        self.assertStatus(p, "F-A-01", BROKEN, "failed in the report")
        self.assertIn("failed", self.note(p, "F-A-01"))
        self.assertStatus(p, "F-B-01", BROKEN, ".skip is reported <skipped/>")
        self.assertIn("skipped", self.note(p, "F-B-01"))
        self.assertStatus(p, "F-C-01", BROKEN, ".todo is reported <skipped/> too")
        self.assertStatus(p, "F-D-01", OK, "")
        self.assertIn("runner api: 8 passed, 1 failed, 2 skipped (exit 1)", r.out, "a failing run still has its reports read")

    def test_a_test_the_run_no_longer_reports_breaks_its_flow_after_the_whole_runner_ran(self):
        doc = doc_text(row("F-A-01", "Renamed", "", "crypto.service.spec/CryptoService > an old name"),
                       row("F-B-01", "Kept", "", CRY_1))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        both = "describe('CryptoService', () => { it('an old name', () => {}); it('encrypts and decrypts a key', () => {}); });\n"
        p.write("apps/api/test/unit/crypto.service.spec.ts", both)
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertStatus(p, "F-A-01", BROKEN, "the source declares it, the whole runner ran, the report lacks it")
        self.assertIn("not in its class's run", self.note(p, "F-A-01"))
        # narrowed (--only-stale): a miss is only a lookup miss
        q = self.project("DemoSystem", doc=doc, state=make_state())
        q.write("apps/api/test/unit/crypto.service.spec.ts", both)
        self.run_engine(q, "--only-stale", docker=["auto"])
        self.assertStatus(q, "F-A-01", MISS, "a narrowed run proves nothing about a test it did not select")
        self.assertIn("lookup", self.note(q, "F-A-01"))
        self.assertStatus(q, "F-B-01", OK, "")

    def test_a_proof_with_no_known_test_file_is_a_lookup_miss_never_a_failure(self):
        doc = doc_text(row("F-A-01", "Unknown file", "", "nowhere.spec/x"))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        r = self.run_engine(p, "--only-stale")
        self.assertEqual(r.code, 0, r.text)
        self.assertStatus(p, "F-A-01", MISS, "")
        self.assertNotIn("failed", self.note(p, "F-A-01"))
        self.assertIn("resolve to no test class", r.out)
        self.assertEqual(self.fake.docker_calls(), [])

    def test_an_unreadable_report_is_printed_and_a_run_with_nothing_readable_fails(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, "--only-stale", docker=["!malformed", "auto", "auto"])
        self.assertEqual(r.code, 1, r.text)
        self.assertIn("unreadable JUnit report", r.out)
        self.assertIn("the reports hold no test case", r.out)
        self.assertShown(p, "F-CRY-01", MISS, "")
        self.assertStatus(p, "F-CAT-01", OK, "the next runner still ran")
        q = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(q, "--only-stale", docker=["!empty", "auto", "auto"])
        self.assertEqual(r.code, 1, "a report with no testcase is a batch without a result:\n" + r.text)

    def test_from_cache_runs_nothing_and_a_cited_source_that_moved_names_its_path(self):
        """Modes --from-cache and the fingerprint: a docker flow cites TypeScript by name or by the path's tail."""
        p = self.project("DemoSystem", doc=system_doc(
            row("F-SYS-01", "Systems page", "systems/page.tsx", CRY_1),
            row("F-PAGE-01", "First page.tsx wins a bare name", "page.tsx", CRY_1),
            row("F-DEC-01", "A dependency's file is not indexed", "index.ts", CRY_1)), state=KEEP)
        systems = p.root / "apps/web/src/app/(dashboard)/systems/page.tsx"
        providers = p.root / PAGE_PROVIDERS
        flows = {fid: frec(OK, [CRY_1], fp=fp, when="2026-01-10", run=RUN1) for fid, fp in
                 (("F-CRY-01", p.fp("crypto.service.ts")), ("F-CRY-02", p.fp("crypto.service.ts")),
                  ("F-PRV-01", sha1_paths(providers)), ("F-SYS-01", sha1_paths(systems)),
                  ("F-PAGE-01", sha1_paths(providers)))}
        flows["F-CRY-02"]["proofs"] = [CRY_2]
        flows["F-PRV-01"]["proofs"] = [PRV_1]
        flows["F-DEC-01"] = frec(OK, [CRY_1], fp=STALE_FP, when="2026-01-10", run=RUN1)
        p.set_state(make_state(flows, dseed(CRY_1, CRY_2)))
        p.edit(systems)
        p.edit("crypto.service.ts")
        r = self.run_engine(p, "--from-cache")
        self.assertEqual(self.fake.docker_calls(), [], "no runner is started")
        self.assertStatus(p, "F-SYS-01", REV, "systems/page.tsx moved")
        self.assertIn("systems/page.tsx", self.note(p, "F-SYS-01"))
        self.assertStatus(p, "F-PRV-01", OK, "the other page.tsx is a different file, named by its full path")
        self.assertStatus(p, "F-PAGE-01", OK, "a bare page.tsx is the first one listed (providers), which did not move")
        self.assertTrue([l for l in r.out.splitlines() if "clash" in l and "page.tsx" in l], r.out)
        self.assertStatus(p, "F-CRY-01", REV, "crypto.service.ts moved: a name with dots is one name")
        self.assertIn("crypto.service.ts", self.note(p, "F-CRY-01"))
        self.assertStatus(p, "F-DEC-01", REV, "node_modules is never walked, so index.ts is not on disk")
        self.assertIn("cited file not found: index.ts", self.note(p, "F-DEC-01"))

    def test_a_test_the_source_no_longer_declares_breaks_its_flow_offline(self):
        doc = doc_text(row("F-A-01", "Gone test", "", CRY_TOP), row("F-B-01", "Kept test", "", CRY_1),
                       row("F-C-01", "Gone file", "", "removed.spec/x"),
                       row("F-D-01", "Loose file", "", "settings.spec/settings > case 1"),
                       row("F-E-01", "Gone from a loose file", "", "settings.spec/settings > not declared"))
        p = self.project("DemoSystem", doc=doc, state=KEEP)
        flows = {"F-A-01": frec(OK, [CRY_TOP], when="2026-01-10", run=RUN1),
                 "F-B-01": frec(OK, [CRY_1], when="2026-01-10", run=RUN1),
                 "F-C-01": frec(OK, ["removed.spec/x"], when="2026-01-10", run=RUN1),
                 "F-D-01": frec(OK, ["settings.spec/settings > case 1"], when="2026-01-10", run=RUN1),
                 "F-E-01": frec(OK, ["settings.spec/settings > not declared"], when="2026-01-10", run=RUN1)}
        raw = dseed(CRY_TOP, CRY_1, "removed.spec/x", "settings.spec/settings > case 1", "settings.spec/settings > not declared")
        p.set_state(make_state(flows, raw))
        p.write("apps/api/test/unit/crypto.service.spec.ts", "describe('CryptoService', () => {\n  it('encrypts and decrypts a key', () => {});\n});\n")
        r = self.run_engine(p, "--from-cache")
        self.assertStatus(p, "F-A-01", BROKEN, "the top level case is no longer declared")
        self.assertIn("source", self.note(p, "F-A-01"))
        self.assertStatus(p, "F-B-01", OK, "")
        self.assertStatus(p, "F-C-01", BROKEN, "no scanned file carries that stem any more")
        self.assertStatus(p, "F-D-01", OK, "a .each title cannot be known gone")
        self.assertStatus(p, "F-E-01", OK, "a loose file never calls a test gone")
        self.assertIn("2 flow(s) cite a test the source no longer declares: F-A-01, F-C-01", r.out)

    def test_listings_name_uncited_passing_tests_and_declared_tests_no_run_executed(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertEqual(listing(r.out, "uncited", "passing"),
                         sorted(["crypto.service.spec/CryptoService > reads a template without an expression",
                                 CRY_TOP, "settings.spec/settings > parses effort", "settings.spec/settings > case 1",
                                 "settings.spec/settings > case 2", "gateway.int.spec/gateway > refuses a request with tools"]),
                         "Passed cases no flow cites; skipped ones are not Passed")
        self.assertEqual(listing(r.out, "uncited", "never run"), [PRV_2],
                         "declared by the Playwright file, cited by no flow, executed by no run")

    def test_changed_selects_flows_by_cited_source_path_tail_and_by_changed_test_file(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        self.fake.git_diff = ["apps/api/src/crypto/crypto.service.ts", "README.md"]
        r = self.run_engine(p, "--changed", docker=["auto"])
        self.assertIn("changed files: 1, flows touched: 2", r.out, "README.md is no source; crypto.service.ts is cited twice")
        self.assertEqual(p.state()["run"]["scope_ids"], ["F-CRY-01", "F-CRY-02"])
        self.assertEqual([c.cmd for c in self.fake.docker_calls()], [COMPOSE_RUN + ["api-test"]],
                         "a changed run runs the whole runner")
        self.fake.reset()
        q = self.project("DemoSystem", doc=system_doc(), state=make_state())
        self.fake.git_diff = ["apps/api/test/integration/gateway.int.spec.ts"]
        self.run_engine(q, "--changed", docker=["auto"])
        self.assertEqual(q.state()["run"]["scope_ids"], ["F-GAT-01"], "a changed test file selects the flows citing its stem")
        self.fake.reset()
        s = self.project("DemoSystem", doc=system_doc(), state=make_state())
        self.fake.git_untracked = ["apps/web/messages/en.json"]
        self.fake.git_diff = ["apps/web/src/app/(dashboard)/systems/page.tsx"]
        self.run_engine(s, "--changed", "--ui", docker=["auto"])
        self.assertEqual(s.state()["run"]["scope_ids"], ["F-CAT-01"],
                         "an untracked en.json counts; systems/page.tsx is not the page.tsx F-PRV-01 cites by its full path")
        self.fake.reset()
        self.fake.git_untracked = []
        t = self.project("DemoSystem", doc=system_doc(row("F-FORM-01", "Form", "form.tsx", CRY_1)), state=make_state())
        self.fake.git_diff = ["apps/web/src/platform.tsx"]
        r = self.run_engine(t, "--changed")
        self.assertIn("changed files: 1, flows touched: 0", r.out, "form.tsx is not the tail of platform.tsx")
        self.fake.reset()
        u = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"source_patterns": ["*.json"]})
        self.fake.git_diff = ["apps/api/test/integration/gateway.int.spec.ts"]
        self.run_engine(u, "--changed", docker=["auto"])
        self.assertEqual(u.state()["run"]["scope_ids"], ["F-GAT-01"], "a runner's test file patterns are watched too")

    def test_resume_continues_a_docker_run_after_an_interrupt(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, docker=["auto", "!interrupt"])
        self.assertTrue(r.interrupted)
        run = p.state()["run"]
        self.assertEqual((run["classes_planned"], run["classes_done"], run["completed"]),
                         (["api/run", "web/run"], ["api/run"], False))
        self.assertStatus(p, "F-CRY-01", OK, "the finished runner is checkpointed")
        self.fake.reset()
        r = self.run_engine(p, "--resume", docker=["auto"])
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual([c.cmd[-1] for c in self.fake.docker_calls()], ["web-test"], "only the runner left runs")
        self.assertStatus(p, "F-CAT-01", OK, "")
        self.assertTrue(p.state()["run"]["completed"])

    def test_reuse_reads_a_junit_file_or_a_folder_without_running_anything(self):
        p = self.project("DemoSystem", doc=system_doc(row("F-TWO-01", "Two proofs", "crypto.service.ts", CRY_1 + "<br>" + GAT_1)),
                         state=make_state())
        folder = self.tmp / "archive"
        (folder / "api").mkdir(parents=True)
        (folder / "api" / "unit.xml").write_text(junit_xml("vitest", SYSTEM_API[:2]))
        (folder / "web").mkdir()
        (folder / "web" / "junit.xml").write_text(junit_xml("vitest", SYSTEM_WEB))
        os.utime(folder / "api" / "unit.xml", (ts(2026, 2, 20), ts(2026, 2, 20)))
        r = self.run_engine(p, "--reuse", str(folder))
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(self.fake.runner_calls(), [], "--reuse starts no container and reads no xcresult")
        self.assertStatus(p, "F-CRY-01", OK, "")
        self.assertEqual(p.state()["flows"]["F-CRY-01"]["validated_at"], "2026-02-20", "the report file's date, not today's")
        self.assertStatus(p, "F-CRY-02", OK, "")
        self.assertStatus(p, "F-CAT-01", OK, "every report of the folder is read, the web runner's too")
        self.assertShown(p, "F-GAT-01", MISS, "not in the report: its status is kept")
        self.assertShown(p, "F-TWO-01", MISS, "only one of its two proofs is in the report")
        self.assertTrue([l for l in r.out.splitlines() if "partly" in l and "F-TWO-01" in l], r.out)
        # a single file, the runner found from the test file stem
        q = self.project("DemoSystem", doc=system_doc(), state=make_state())
        one = self.tmp / "web.xml"
        one.write_text(junit_xml("vitest", SYSTEM_WEB))
        self.run_engine(q, "--reuse", str(one))
        self.assertStatus(q, "F-CAT-01", OK, "a file outside a runner's folder: the stem says which runner")
        self.assertEqual(q.state()["flows"]["F-CAT-01"]["proofs"], [CAT_1])
        self.assertNotIn("None/run", set(q.state()["raw"]["runnable"].values()), "no runner is made up")
        self.assertEqual(p.state()["raw"]["runnable"][CRY_1], "api/run", "a report in a runner's folder belongs to it")
        # nothing readable: refused
        empty = self.tmp / "none"
        empty.mkdir()
        before = q.snapshot()
        self.assertEqual(self.run_engine(q, "--reuse", str(empty)).code, 2)
        self.assertUntouched(q, before, "a folder without reports writes nothing")

    def test_reuse_takes_several_junit_files_or_folders_each_read_with_its_own_date(self):
        """A flow citing a test of the api runner and one of the web runner is judged from their two reports; a failure
        in the second report breaks it; an unreadable target among several writes nothing."""
        doc = system_doc(row("F-BOTH-01", "Both runners", "crypto.service.ts", CRY_1 + "<br>" + CAT_1))
        api = self.tmp / "api-archive" / "api"
        api.mkdir(parents=True)
        (api / "unit.xml").write_text(junit_xml("vitest", SYSTEM_API[:2]))
        os.utime(api / "unit.xml", (ts(2026, 2, 10), ts(2026, 2, 10)))
        web = self.tmp / "web.xml"
        web.write_text(junit_xml("vitest", SYSTEM_WEB))
        os.utime(web, (ts(2026, 2, 20), ts(2026, 2, 20)))
        p = self.project("DemoSystem", doc=doc, state=make_state())
        r = self.run_engine(p, "--reuse", str(api.parent), str(web))
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(self.fake.runner_calls(), [], "--reuse starts no container")
        self.assertStatus(p, "F-BOTH-01", OK, "one proof in each target")
        self.assertEqual(p.state()["flows"]["F-BOTH-01"]["validated_at"], "2026-02-20")
        self.assertEqual(p.state()["flows"]["F-CRY-01"]["validated_at"], "2026-02-10", "each report keeps its own date")
        self.assertEqual(p.state()["flows"]["F-BOTH-01"]["run_id"], "20260220T120000-reuse", "dated by the latest target")
        done = [l for l in r.out.splitlines() if l.startswith("map rewritten from")]
        for text in (str(api.parent), "results dated 2026-02-10", str(web), "results dated 2026-02-20"):
            self.assertIn(text, done[0], "the printed line names every target and its date")
        failing = self.tmp / "failing.xml"
        failing.write_text(junit_xml("vitest", [dict(SYSTEM_WEB[0], result="Failed")]))
        q = self.project("DemoSystem", doc=doc, state=make_state())
        self.assertEqual(self.run_engine(q, "--reuse", str(api.parent), str(failing)).code, 1)
        self.assertStatus(q, "F-BOTH-01", BROKEN, "its proof in the second target failed")
        self.assertStatus(q, "F-CRY-01", OK, "the flow of the first target is judged on its own proof")
        # the same test in two targets, failed in one: failed whichever target is read first
        both = doc_text(row("F-CAT-01", "Catalog", "catalog.unit.ts", CAT_1))
        for order in ((web, failing), (failing, web)):
            t = self.project("DemoSystem", doc=both, state=make_state())
            self.assertEqual(self.run_engine(t, "--reuse", *[str(x) for x in order]).code, 1, f"{[x.name for x in order]}")
            self.assertStatus(t, "F-CAT-01", BROKEN, f"{[x.name for x in order]}")
        # the same passing test in two targets: the later date whichever target is read first
        older = self.tmp / "web-older.xml"
        older.write_text(junit_xml("vitest", SYSTEM_WEB))
        os.utime(older, (ts(2026, 2, 5), ts(2026, 2, 5)))
        for order in ((web, older), (older, web)):
            t = self.project("DemoSystem", doc=both, state=make_state())
            self.assertEqual(self.run_engine(t, "--reuse", *[str(x) for x in order]).code, 0, f"{[x.name for x in order]}")
            self.assertEqual(t.state()["flows"]["F-CAT-01"]["validated_at"], "2026-02-20", f"{[x.name for x in order]}")
            self.assertEqual(t.state()["flows"]["F-CAT-01"]["run_id"], "20260220T120000-reuse", f"{[x.name for x in order]}")
        # a target whose reports hold no test is refused, among several
        hollow = self.tmp / "hollow.xml"
        hollow.write_text("<testsuites></testsuites>")
        before = q.snapshot()
        self.assertEqual(self.run_engine(q, "--reuse", str(api.parent), str(hollow)).code, 2)
        self.assertIn(f"no test result read from {hollow}", self.run_engine(q, "--reuse", str(api.parent), str(hollow)).err)
        self.assertUntouched(q, before, "a target holding no test among several writes nothing")
        empty = self.tmp / "none"
        empty.mkdir()
        before = p.snapshot()
        self.assertEqual(self.run_engine(p, "--reuse", str(api.parent), str(empty)).code, 2)
        self.assertUntouched(p, before, "a target without reports among several writes nothing")

    def test_flags_that_mean_a_simulator_or_a_build_folder_are_refused_for_a_docker_project(self):
        for flag, value in (("--device", "Demo-iPhone-abc123"), ("--derived-data", "/tmp/dd"), ("--result-path", "/tmp/r.xcresult")):
            p = self.project("DemoSystem", doc=system_doc(), state=make_state())
            before = p.snapshot()
            r = self.run_engine(p, "--only-stale", flag, value)
            self.assertEqual(r.code, 2, f"{flag}:\n" + r.text)
            self.assertIn(flag, r.err)
            self.assertIn("docker project", r.err)
            self.assertEqual(self.fake.runner_calls(), [], f"{flag} starts nothing")
            self.assertUntouched(p, before, f"{flag} writes nothing")

    def test_check_prune_state_and_invalidate_classes_work_on_runners(self):
        flows = {"F-CRY-01": frec(OK, [CRY_1], when="2026-01-10", run=RUN1),
                 "F-CAT-01": frec(OK, [CAT_1], when="2026-01-10", run=RUN1),
                 "F-OLD-09": frec(OK, ["old.spec/x"], when="2026-01-10", run=RUN1)}
        by_path, runnable = dseed(CRY_1, runner="api")
        web_path, web_run = dseed(CAT_1, runner="web")
        by_path.update(web_path)
        runnable.update(web_run)
        doc = doc_text(row("F-CRY-01", "Key", "", CRY_1, OK, "2026-01-10"), row("F-CAT-01", "Catalog", "", CAT_1, OK, "2026-01-10"))
        p = self.project("DemoSystem", doc=doc, state=make_state(flows, (by_path, runnable)))
        before = p.snapshot()
        r = self.run_engine(p, "--check")
        self.assertEqual(r.code, 0, r.text)
        self.assertEqual(self.fake.calls, [], "--check runs nothing, not even git")
        self.assertUntouched(p, before, "--check writes nothing")
        r = self.run_engine(p, "--invalidate-classes", "api")
        self.assertStatus(p, "F-CRY-01", REV, "a runner name demotes the flows whose proofs are all in it")
        self.assertStatus(p, "F-CAT-01", OK, "the other runner's flow keeps its status")
        self.assertIn("class invalidated: api/run", self.note(p, "F-CRY-01"))
        r = self.run_engine(p, "--prune-state")
        self.assertNotIn("F-OLD-09", p.state()["flows"], "the state entry of a flow that left the map goes")
        self.assertEqual(self.fake.calls, [])

    def test_a_flow_mixing_a_browser_proof_and_a_unit_proof_is_refused_when_configured(self):
        doc = doc_text(row("F-MIX-01", "Mixed", "", CRY_1 + "<br>" + PRV_1))
        p = self.project("DemoSystem", doc=doc, state=make_state(), config={"refuse_mixed_ui_unit": True})
        r = self.run_engine(p, "--only-stale", docker=["auto", "auto"])
        self.assertStatus(p, "F-MIX-01", BROKEN, "a path walked in a browser and a pure rule are two flows")
        self.assertIn("mix", self.note(p, "F-MIX-01"))

    def test_a_proof_whose_file_the_scan_does_not_know_runs_its_runner_whole(self):
        runners = json.loads((FIXTURES / "project_tree/DemoSystem/scripts/flows.json").read_text())["runners"]
        runners[0]["only_args"] = ["{files}", "-t", "{pattern}"]
        doc = doc_text(row("F-A-01", "A deleted file", "", "gone.spec/was here"))
        p = self.project("DemoSystem", doc=doc, state=KEEP, config={"runners": runners})
        p.set_state(make_state({"F-A-01": frec(OK, ["gone.spec/was here"], when="2026-01-10", run=RUN1)},
                               dseed("gone.spec/was here")))
        r = self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertEqual([c.cmd for c in self.fake.docker_calls()], [COMPOSE_RUN + ["api-test"]],
                         "no file to hand to {files}: the whole runner")
        self.assertEqual(p.state()["run"]["cases_by_class"], {})
        self.assertIn("1 flow(s) cite a test the source no longer declares: F-A-01", r.out,
                      "offline, its file is not among the scanned ones: the test is gone")
        self.assertStatus(p, "F-A-01", MISS, "the whole run dropped the stored result of a test no one reports: a lookup miss")
        self.assertNotIn("gone.spec/was here", p.state()["raw"]["by_path"])

    def test_a_bare_name_proof_runs_its_runner_whole_and_finds_its_test_in_the_report(self):
        doc = doc_text(row("F-B-01", "Bare name", "", "top level case"))
        p = self.project("DemoSystem", doc=doc, state=KEEP)
        p.set_state(make_state({"F-B-01": frec(REV, ["top level case"], when="2026-01-10", run=RUN1)}, dseed(CRY_TOP)))
        self.run_engine(p, "--only-stale", docker=["auto"])
        self.assertEqual([c.cmd for c in self.fake.docker_calls()], [COMPOSE_RUN + ["api-test"]],
                         "a bare name has no test file stem to narrow by")
        self.assertStatus(p, "F-B-01", OK, "a bare name finds its test in the report")

    def test_overlapping_test_folders_of_a_runner_count_each_file_once(self):
        runners = json.loads((FIXTURES / "project_tree/DemoSystem/scripts/flows.json").read_text())["runners"]
        runners[0]["tests"] = [{"dir": "apps/api/test", "patterns": ["*.spec.ts"]},
                               {"dir": "apps/api/test/unit", "patterns": ["*.spec.ts"]}]
        p = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"runners": runners})
        r = self.run_engine(p, "--from-cache")
        self.assertNotIn("declared in more than one", r.out)
        r = self.run_engine(p, "--only-stale", docker=["auto"] * 3)
        self.assertStatus(p, "F-CRY-01", OK, "the stem resolves although two folders reach its file")

    def test_a_file_outside_the_runners_patterns_declares_no_test(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        r = self.run_engine(p, docker=["auto", "auto"])
        self.assertNotIn("helpers/from a helper", listing(r.out, "uncited", "never run"), "helpers.ts matches no test pattern")

    def test_ui_needs_flag_keeps_the_browser_runner_out_of_only_stale(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"ui_needs_flag": True})
        self.run_engine(p, "--only-stale", docker=["auto", "auto"])
        self.assertEqual(self.planned(p), {"api/run", "web/run"})
        self.assertShown(p, "F-PRV-01", MISS, "")
        q = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"ui_needs_flag": True})
        self.run_engine(q, "--only-stale", "--ui", docker=["auto", "auto", "auto"])
        self.assertEqual(self.planned(q), {"api/run", "web/run", "e2e/run"})

    def test_two_test_files_with_one_stem_are_reported_and_their_proofs_do_not_resolve_offline(self):
        p = self.project("DemoSystem", doc=doc_text(row("F-A-01", "Settings", "", "settings.spec/settings > parses effort")),
                         state=make_state())
        p.write("apps/api/test/unit/sub/settings.spec.ts", "describe('settings', () => { it('parses effort', () => {}); });\n")
        r = self.run_engine(p, "--only-stale")
        self.assertTrue([l for l in r.out.splitlines() if "declared in more than one" in l and "settings.spec" in l], r.out)
        self.assertIn("resolve to no test class", r.out)
        self.assertEqual(self.fake.docker_calls(), [])

    def test_a_runner_whose_test_folders_hold_no_file_says_so(self):
        runners = json.loads((FIXTURES / "project_tree/DemoSystem/scripts/flows.json").read_text())["runners"]
        runners[1]["tests"] = [{"dir": "apps/web/missing", "patterns": ["*.test.ts"]}]
        p = self.project("DemoSystem", doc=system_doc(), state=make_state(), config={"runners": runners})
        r = self.run_engine(p, "--from-cache")
        self.assertIn("runner web: no test file found", r.out)

    def test_the_docker_commands_carry_no_simulator_flag_and_no_device(self):
        p = self.project("DemoSystem", doc=system_doc(), state=make_state())
        self.run_engine(p, "--only-stale", docker=["auto"] * 3)
        flat = [a for c in self.fake.calls for a in c.cmd]
        self.assertEqual(self.fake.xcode_calls(), [])
        self.assertFalse([a for a in flat if a in ("xcodebuild", "xcrun", "-sdk", "-destination")], flat)


# ---------------------------------------------------------------------------- languages


class PortugueseStatusRuleTests(StatusRuleTests):
    """The status rules on a Brazilian Portuguese map (docs/FLUXOS.md, validado, placar, state words in Portuguese)."""
    language = "pt-BR"


class PortugueseModeTests(ModeTests):
    """Every mode on a Brazilian Portuguese map."""
    language = "pt-BR"


class PortugueseSourceAndFingerprintTests(SourceAndFingerprintTests):
    language = "pt-BR"


class LanguageTests(EngineCase):
    """The language of a map is read from the map; a map in Portuguese keeps working unchanged."""

    def portuguese_demo(self):
        """The Demo project as a Portuguese project keeps it: docs/FLUXOS.md and a state with Portuguese words."""
        p = self.project()
        for doc in (p.root / "docs").glob("FLOWS*.md"):
            doc.unlink()
        for name in ("FLUXOS.md", ".flows-state.json"):
            shutil.copy(FIXTURES / "pt_tree" / "Demo" / "docs" / name, p.root / "docs" / name)
        p.doc_path = p.root / "docs" / "FLUXOS.md"
        return p

    def without_maps(self):
        p = self.project()
        for doc in (p.root / "docs").glob("FLOWS*.md"):
            doc.unlink()
        return p

    def test_detect_language_reads_markers_then_header_then_status_words_then_the_file_name(self):
        detect = check_flows.detect_language
        self.assertEqual(detect(placar("en")), "en")
        self.assertEqual(detect(placar("pt-BR")), "pt-BR")
        self.assertEqual(detect("text\n" + HEADERS["pt-BR"] + "\n"), "pt-BR")
        self.assertEqual(detect("text\n" + HEADERS["en"] + "\n"), "en")
        rows = "| F-A-01 | n | e | p | sem prova | — |\n| F-A-02 | n | e | p | validado | 2026-01-01 |\n"
        self.assertEqual(detect(rows), "pt-BR")
        self.assertEqual(detect("| F-A-01 | n | e | p | broken | — |\n"), "en")
        self.assertIsNone(detect("| F-A-01 | n | e | p | | — |\n"), "no mark, no language")
        self.assertIsNone(detect("| F-A-01 | n | e | p | | — |\n", "FLOWS.md"))
        self.assertEqual(detect("| F-A-01 | n | e | p | | — |\n", "FLUXOS.md"), "pt-BR")
        self.assertIsNone(detect("| F-A-01 | n | e | p | validated | — |\n| F-A-02 | n | e | p | validado | — |\n"),
                          "one word of each language is a tie")

    def test_a_portuguese_map_is_found_without_flows_md_and_rewritten_in_portuguese(self):
        p = self.portuguese_demo()
        r = self.run_engine(p, "--from-cache")
        self.assertEqual(r.code, 1, r.text)
        self.assertFalse((p.root / "docs" / "FLOWS.md").exists(), "no English map is created beside the Portuguese one")
        text = p.doc_path.read_text(encoding="utf-8")
        self.assertIn("<!-- placar:inicio -->", text)
        self.assertNotIn("scoreboard", text)
        self.assertIn("Este placar não é escrito à mão", text)
        self.assertIn("| Total | validados | revalidar | sem prova | so em aparelho | quebrados |", text)
        self.assertEqual(doc_row(text, "F-DEV-03")[4], "so em aparelho")
        self.assertEqual(doc_row(text, "F-RULE-01")[4], "validado")
        self.assertRegex(r.out, r"(?m)^flows: 19 total, 7 validado, 4 revalidar, 2 sem prova, 1 so em aparelho, 5 quebrado$")
        state = json.loads(p.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["flows"]["F-DEV-03"]["status"], "só em aparelho", "the stored word keeps its accent")
        self.assertEqual(state["flows"]["F-RULE-01"]["status"], "validado")
        again = p.snapshot()
        self.assertEqual(self.run_engine(p, "--from-cache").code, 1)
        self.assertEqual(p.snapshot(), again, "a second pass over the Portuguese map changes nothing")

    def test_the_english_and_the_portuguese_demo_reach_the_same_counts(self):
        english = self.project()
        portuguese = self.portuguese_demo()
        a = summary(self.run_engine(english, "--from-cache").out)
        LANG["code"] = "pt-BR"
        try:
            b = summary(self.run_engine(portuguese, "--from-cache").out)
        finally:
            LANG["code"] = "en"
        self.assertEqual(a, b)
        self.assertEqual(a["total"], 19)

    def test_flows_md_wins_when_both_maps_exist_and_the_other_is_left_alone(self):
        p = self.project()
        portuguese = p.root / "docs" / "FLUXOS.md"
        portuguese.write_text("# old map\n")
        self.run_engine(p, "--from-cache")
        self.assertEqual(portuguese.read_text(), "# old map\n")
        self.assertIn("<!-- scoreboard:start -->", p.doc())

    def test_a_map_with_no_marks_takes_the_language_flag_and_english_by_default(self):
        bare = ("# map\n\n| ID | a | b | c | d | e |\n|---|---|---|---|---|---|\n"
                "| F-A-01 | n | Rules.swift | RulesTests/testA() | | |\n")
        english = self.project(doc=bare, state=make_state())
        self.run_engine(english, "--from-cache")
        self.assertEqual(doc_row(english.doc(), "F-A-01")[4], "no proof")
        pt = self.project(doc=bare, state=make_state())
        r = self.run_engine(pt, "--from-cache", "--language", "pt-BR")
        self.assertEqual(doc_row(pt.doc(), "F-A-01")[4], "sem prova", r.text)
        self.assertIn("1 sem prova", r.out)

    def test_a_language_flag_that_contradicts_the_map_is_refused_and_nothing_is_written(self):
        p = self.portuguese_demo()
        before = p.snapshot()
        r = self.run_engine(p, "--from-cache", "--language", "en")
        self.assertEqual(r.code, 2, r.text)
        self.assertIn("contradicts", r.err)
        self.assertEqual(p.snapshot(), before)
        q = self.project()
        before = q.snapshot()
        self.assertEqual(self.run_engine(q, "--from-cache", "--language", "pt-BR").code, 2)
        self.assertEqual(q.snapshot(), before)

    def test_a_state_stored_in_the_other_language_is_read_and_rewritten_in_the_maps_language(self):
        doc = doc_text(row("F-RULE-01", "Rules", "", "RulesTests/testA()"))
        p = self.project(doc=doc, state=make_state({"F-RULE-01": frec(REV, ["RulesTests/testA()"], when="2026-01-10", run=RUN1)}))
        state = json.loads(p.state_path.read_text())
        state["flows"]["F-RULE-01"]["status"] = "revalidar"
        p.state_path.write_text(json.dumps(state))
        self.run_engine(p, "--from-cache")
        self.assertEqual(p.state()["flows"]["F-RULE-01"]["status"], "revalidate")
        self.assertEqual(doc_row(p.doc(), "F-RULE-01")[4], "revalidate")

    def test_init_writes_a_starter_map_in_the_chosen_language_and_never_overwrites(self):
        for code, name, marker in (("en", "FLOWS.md", "<!-- scoreboard:start -->"),
                                   ("pt-BR", "FLUXOS.md", "<!-- placar:inicio -->")):
            p = self.without_maps()
            args = ["--init"] + (["--language", code] if code != "en" else [])
            r = self.run_engine(p, *args)
            self.assertEqual(r.code, 0, r.text)
            path = p.root / "docs" / name
            text = path.read_text(encoding="utf-8")
            self.assertIn(marker, text)
            self.assertIn(HEADERS[code], text)
            self.assertEqual(check_flows.detect_language(text, name), code)
            self.assertNotIn("\r", text)
            before = path.read_bytes()
            self.assertEqual(self.run_engine(p, *args).code, 2, "an existing map is never overwritten")
            self.assertEqual(path.read_bytes(), before)
            other = "pt-BR" if code == "en" else "en"
            self.assertEqual(self.run_engine(p, "--init", "--language", other).code, 2,
                             "nor is the other language's file written beside it")
            # the starter map has no flow yet, and says so; with a row added it works in its own language
            self.assertEqual(self.run_engine(p, "--from-cache").code, 2)
            path.write_text(text + "| F-NEW-01 | Rules | Rules.swift | RulesTests/testA() | | |\n", encoding="utf-8")
            LANG["code"] = code
            try:
                r = self.run_engine(p, "--from-cache")
                self.assertEqual(summary(r.out)["total"], 1, r.text)
                self.assertEqual(doc_row(path.read_text(encoding="utf-8"), "F-NEW-01")[4], shown(MISS))
            finally:
                LANG["code"] = "en"

    def test_init_needs_no_configuration_and_goes_alone(self):
        p = self.without_maps()
        (p.root / "scripts" / "flows.json").unlink()
        self.assertEqual(self.run_engine(p, "--init").code, 0, "a starter map comes before the configuration")
        for other in ("--check", "--only-stale", "--from-cache", "--ui"):
            q = self.without_maps()
            self.assertEqual(self.run_engine(q, "--init", other).code, 2, f"--init beside {other}")
            self.assertFalse((q.root / "docs" / "FLOWS.md").exists())


if __name__ == "__main__":
    unittest.main()
