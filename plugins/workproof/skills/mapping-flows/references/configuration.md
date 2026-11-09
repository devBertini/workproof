# scripts/flows.json and a new map

The source of truth is class `Config` in `scripts/check_flows.py`. Only `project` is required; paths are relative to the project root.

## Keys

| Key | Meaning (default) |
|---|---|
| `project` | The project's name; the root of the defaults below. |
| `xcodeproj`, `scheme` | The Xcode project and scheme the app and UI targets run in (`<project>.xcodeproj`, `<project>`). |
| `platform` | `ios`, `macos`, `docker` or `rust` (`ios`). `docker` means test runners that write JUnit XML (typically containers); `rust` takes the same keys, with `"framework": "nextest"` and a command that needs no container. A Mac project runs on `platform=macOS,arch=arm64` with no device and no `--device`. |
| `device_kind` | The kind the project's own classes run on: `phone`, `small`, `se` or `ipad` (`phone`). A kind only decides which simulator runs a class and which family (phone or tablet) it belongs to. |
| `devices` | `{"<kind>": "<simulator name or UDID>"}`: the simulator each kind runs on, resolved through `xcrun simctl list`. Required for an iOS run unless `--device` is given. The engine never creates, boots or shuts down a simulator. |
| `device_classes` | `{"<Class>": "<kind>"}` for a class that runs on another kind (`{"IPadLayoutUITests": "ipad"}`). With `--device`, classes of the other family print `waiting for a <kind> pass` and keep their status. |
| `unit_targets`, `ui_targets` | Lists of `{"name", "dir"}` (`dir` defaults to `name`): the test targets and the folders scanned for declared tests; UI classes are told apart by their target. |
| `packages` | Local packages with their own tests: `{"target", "dir", "tests_dir", "runner", "scheme"}`. `runner` is `xcodebuild` (needs `scheme`) or `swift-test` (results keyed `Target/Suite/case`); `tests_dir` defaults to `<dir>/Tests`. |
| `sources` | The folders indexed for the cited files, each a name or `{"dir", "patterns"}`. Default: the project folder and the test folders. List every folder an `Entry` cell can cite. Required with `runners`. |
| `source_patterns` | The file patterns the fingerprint reads (`["*.swift", "*.xcstrings"]`; with runners `["*.ts", "*.tsx", "*.json"]`; Rust `["*.rs", "*.toml", "*.json"]`). |
| `derived_data` | The build folder (`~/Library/Developer/Xcode/DerivedData/flows-<project>`); `--derived-data` overrides it. Not used with runners. |
| `results_dir` | Where result bundles go (`<derived_data>/results`; with runners `test-results/flows`, a new folder of JUnit reports per batch). |
| `ui_needs_flag` | `true` when UI classes run only with `--ui` (a Mac project whose UI tests take over the screen). |
| `refuse_mixed_ui_unit` | `true` breaks a flow that mixes a UI and a unit proof ("split it in two flows"); `false` judges it once both classes ran in the same run. A choice each project makes and writes down. |
| `runners` | With `docker` or `rust`, required, non-empty: `{"name", "command" (argv list), "framework" ("vitest", "playwright" or "nextest"), "ui" (false), "junit_dir" (a folder inside the project), "only_args", "tests"}`. `only_args` narrows a run to the cited tests (default `["-t", "{pattern}"]` for Vitest, `["--grep", "{pattern}"]` for Playwright; `[]` never narrows; `{files}` expands to the test files' names); `tests` lists the folders or `{"dir", "patterns"}` scanned for declared tests. `unit_targets`, `ui_targets`, `packages` and `device_classes` are refused with runners. |

A Rust workspace sets in `.config/nextest.toml` `[profile.default.junit] path = "junit.xml"`, cites `target/nextest/default` as `junit_dir`, and keeps its tests in library and integration targets: a binary target's id holds a slash and is not scanned.

## A new map

1. `check-flows --init` writes `docs/FLOWS.md` (an English header row and an empty scoreboard); `--init --language pt-BR` writes `docs/FLUXOS.md`. It never overwrites a map and needs no configuration. Add the first flow before its code, one row under the header (an ID like `F-AREA-01`, the flow in a few words, its source files, its proofs, and leave the last two cells to the engine).
2. Write `scripts/flows.json` from the keys above.
3. `check-flows --from-cache`, then `--only-stale`. The engine creates `docs/.flows-state.json`.
4. Name the map and its configuration in the project's own instructions, with the `refuse_mixed_ui_unit` choice.

A map whose rows carry none of the language's marks takes `--language` (English by default); a `--language` that contradicts the map's own marks stops the engine with exit 2. The state file stores each status in the map's language and reads either.

With runners, add the report folders (`test-results/`) to `.gitignore`, make each runner's `junit_dir` reachable from the folder the command runs in (a bind mount when the tests run in a container), keep the reporters' default titles and class names, and bring any services the runner needs up before `--only-stale`: the engine never starts them.
