import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_dashboard_site as builder


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_dashboard_site.py"


def tree_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_cli_packages_snapshots_and_preserves_sources(tmp_path):
    docs = tmp_path / "docs"
    data = tmp_path / "data"
    output = tmp_path / "site"
    for folder in (docs / "assets", data / "prices", data / "analytics", data / "demand"):
        folder.mkdir(parents=True)
    (docs / "index.html").write_text("<html>Dashboard</html>")
    (docs / "assets" / "app.js").write_text("const ready = true;")
    index = {"brands": {"test": {"snapshots": [{"file": "test.json"}]}}}
    (data / "prices" / "index.json").write_text(json.dumps(index))
    snapshot = b'{"vehicles": [1, 2, 3]}\n'
    (data / "prices" / "test.json").write_bytes(snapshot)
    (data / "prices" / "notes.txt").write_text("metadata")
    (data / "analytics" / "model.json.gz").write_bytes(b"generated analytics")
    (data / "demand" / "cohorts.csv").write_text("eligible,selected\n10,2\n")
    before = tree_hashes(docs), tree_hashes(data)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--docs-dir", str(docs),
         "--data-dir", str(data), "--output-dir", str(output)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "bytes" in result.stdout and "snapshot" in result.stdout
    assert (tree_hashes(docs), tree_hashes(data)) == before
    assert (output / "assets" / "app.js").read_bytes() == (docs / "assets" / "app.js").read_bytes()
    for name in ("analytics/model.json.gz", "demand/cohorts.csv", "prices/notes.txt"):
        assert (output / "data" / name).read_bytes() == (data / name).read_bytes()
    assert json.loads((output / "data/prices/index.json").read_text()) == dict(
        index, snapshot_encoding="gzip")
    compressed = output / "data/prices/test.json.gz"
    assert gzip.decompress(compressed.read_bytes()) == snapshot
    assert compressed.read_bytes()[4:8] == b"\x00\x00\x00\x00"
    assert not (output / "data/prices/test.json").exists()
    assert (output / ".dashboard-build.json").is_file()


@pytest.fixture
def sources(tmp_path):
    docs = tmp_path / "docs"
    data = tmp_path / "data"
    docs.mkdir()
    (data / "prices" / "nested").mkdir(parents=True)
    (docs / "index.html").write_text("dashboard")
    (data / "prices" / "index.json").write_text('{"snapshots": ["test.json"]}')
    (data / "prices" / "test.json").write_text('{"snapshot": true}')
    (data / "prices" / "nested" / "second.json").write_text('[1, 2, 3]')
    return docs, data, tmp_path / "site"


def test_rebuild_is_deterministic_and_manifest_counts_actual_bytes(sources):
    docs, data, output = sources
    manifest = builder.build_site(docs, data, output)
    first = tree_hashes(output)
    assert manifest["snapshot_count"] == 2
    assert manifest["file_count"] == len(first)
    assert manifest["total_bytes"] == sum(path.stat().st_size for path in output.rglob("*")
                                          if path.is_file())
    assert not any(path.is_symlink() for path in output.rglob("*"))
    assert gzip.decompress((output / "data/prices/nested/second.json.gz").read_bytes()) == b"[1, 2, 3]"
    assert builder.build_site(docs, data, output) == manifest
    assert tree_hashes(output) == first
    assert not list(output.parent.glob(".site-*-*"))


@pytest.mark.parametrize("relation", ["docs", "data", "ancestor", "inside_docs", "inside_data"])
def test_rejects_overlapping_directories(sources, relation):
    docs, data, output = sources
    unsafe = {"docs": docs, "data": data, "ancestor": docs.parent,
              "inside_docs": docs / "site", "inside_data": data / "site"}[relation]
    before = tree_hashes(docs), tree_hashes(data)
    with pytest.raises(ValueError, match="overlap"):
        builder.build_site(docs, data, unsafe)
    assert (tree_hashes(docs), tree_hashes(data)) == before
    assert not output.exists()


@pytest.mark.parametrize("marker", [None, "not JSON", '{}', '{"builder": "another-tool"}'])
def test_rejects_unowned_nonempty_output(sources, marker):
    docs, data, output = sources
    output.mkdir()
    (output / "unrelated.txt").write_text("keep me")
    if marker is not None:
        (output / builder.MARKER).write_text(marker)
    before = tree_hashes(output)
    with pytest.raises(ValueError, match="Refusing to replace"):
        builder.build_site(docs, data, output)
    assert tree_hashes(output) == before


def test_allows_existing_empty_output(sources):
    docs, data, output = sources
    output.mkdir()
    builder.build_site(docs, data, output)
    assert (output / "index.html").is_file()


@pytest.mark.parametrize("location", ["docs", "data", "output", "output_parent", "source_root"])
def test_rejects_symlinks(sources, location):
    docs, data, output = sources
    if location in ("docs", "data"):
        root = docs if location == "docs" else data
        (root / "linked").symlink_to(docs / "index.html")
    elif location == "output":
        output.symlink_to(docs, target_is_directory=True)
    elif location == "output_parent":
        linked = output.parent / "linked"
        linked.symlink_to(output.parent, target_is_directory=True)
        output = linked / "site"
    else:
        linked = output.parent / "linked"
        linked.symlink_to(docs, target_is_directory=True)
        docs = linked
    with pytest.raises(ValueError, match="Symlinks"):
        builder.build_site(docs, data, output)


@pytest.mark.parametrize("failure", ["size", "bad_index", "gzip_collision", "publish"])
def test_failure_preserves_previous_site_and_cleans_staging(sources, monkeypatch, failure):
    docs, data, output = sources
    builder.build_site(docs, data, output)
    before = tree_hashes(output)
    (docs / "index.html").write_text("new dashboard")
    options = {}
    if failure == "size":
        options["max_bytes"] = 1
    elif failure == "bad_index":
        (data / "prices/index.json").write_text("malformed")
    elif failure == "gzip_collision":
        (data / "prices/test.json.gz").write_bytes(b"existing asset")
    else:
        replace = builder.os.replace

        def fail_publish(source, destination):
            if Path(source).parent.name.startswith(".site-stage-"):
                raise OSError("injected publish failure")
            return replace(source, destination)

        monkeypatch.setattr(builder.os, "replace", fail_publish)
    with pytest.raises((OSError, ValueError)):
        builder.build_site(docs, data, output, **options)
    assert tree_hashes(output) == before
    assert not list(output.parent.glob(".site-*-*"))


def test_size_limit_includes_manifest_and_accepts_exact_limit(sources):
    docs, data, output = sources
    manifest = builder.build_site(docs, data, output)
    assert builder.MAX_SITE_BYTES == 900 * 1024 * 1024
    builder.build_site(docs, data, output, max_bytes=manifest["total_bytes"])
    with pytest.raises(ValueError, match="limit"):
        builder.build_site(docs, data, output, max_bytes=manifest["total_bytes"] - 1)


def test_cli_defaults():
    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--docs-dir" in result.stdout
    assert "--data-dir" in result.stdout
    assert "--output-dir" in result.stdout