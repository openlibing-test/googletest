#!/usr/bin/env python3
"""Check scan evidence without treating ordinary findings as execution failure."""
import collections
import json
import pathlib
import sys

cdb_path, sarif_path = map(pathlib.Path, sys.argv[1:3])
entries = json.loads(cdb_path.read_text())
files = sorted({entry["file"] for entry in entries})
report = json.loads(sarif_path.read_text())
assert report["version"] == "2.1.0", "Unexpected SARIF version"
assert files and report["runs"], "Missing compilation units or SARIF runs"
results = [result for run in report["runs"] for result in run.get("results", [])]
rules = collections.Counter(result.get("ruleId", "") for result in results)
summary = {
    "compilation_entries": len(entries),
    "unique_translation_units": len(files),
    "translation_units": files,
    "sarif_results": len(results),
    "levels": dict(collections.Counter(r.get("level", "warning") for r in results)),
    "rules": dict(rules.most_common()),
    "compiler_errors": rules.get("clang-diagnostic-error", 0),
}
pathlib.Path("build/clang-tidy-summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({k: v for k, v in summary.items() if k not in ("translation_units", "rules")}, indent=2))
assert not summary["compiler_errors"], "Compiler errors invalidate the scan; inspect retained SARIF"
