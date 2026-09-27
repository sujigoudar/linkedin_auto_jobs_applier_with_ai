# Run checks of this design package

Use a dedicated local environment with `pytest`, `jsonschema` and `referencing`. These checks validate schemas, coverage mappings, bindings and atlas content; they do not exercise the trading applications.

```bash
python tools/check_pack.py
python -m pytest tests/test_pack.py -q
python tools/inspect_screen.py TR-12
python tools/build_scope.py --output /tmp/dashboard-application-scope.json
```

To exercise the offline atlas, install Playwright and an appropriate Chromium browser, then run `python tests/audit_atlas.py`. Set `CHROMIUM_EXECUTABLE` only when using an existing system installation. The isolated preparation container used a no-sandbox Chromium flag because of its container configuration; that is not production browser guidance. The atlas test forbids network requests and loads the local HTML into a fresh browser page per viewport.

The original initial browser attempt reused one document realm and caused a top-level variable redeclaration. The harness was corrected to use fresh pages. A subsequent search check found that searching for a field such as trailing omitted its parent screen; the atlas search now includes field labels and form titles. Earlier reports remain in evidence.

Application tests are separately listed in catalog/test_cases.json. Bind them to actual collected drivers and deployed/test fixture identities. Do not import the design atlas into the application to replace real screen behavior.
