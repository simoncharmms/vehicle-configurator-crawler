"""Package the dashboard and local generated data for static deployment."""

import argparse
import gzip
import json
import os
import shutil
import tempfile
from pathlib import Path


MARKER = ".dashboard-build.json"
BUILDER = "dashboard-site-v1"
MAX_SITE_BYTES = 900 * 1024 * 1024


def reject_symlink_path(path):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"Symlinks are forbidden: {component}")


def validate_tree(root):
    reject_symlink_path(root)
    if not root.is_dir():
        raise ValueError(f"Source directory does not exist: {root}")
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            entry = Path(directory) / name
            if entry.is_symlink():
                raise ValueError(f"Symlinks are forbidden: {entry}")
            if not entry.is_dir() and not entry.is_file():
                raise ValueError(f"Unsupported source entry: {entry}")


def validate_output(output):
    reject_symlink_path(output)
    if not output.exists():
        return
    if not output.is_dir():
        raise ValueError(f"Output is not a directory: {output}")
    if not any(output.iterdir()):
        return
    marker = output / MARKER
    try:
        if marker.is_symlink():
            raise ValueError("Symlink marker")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        owned = isinstance(manifest, dict) and manifest.get("builder") == BUILDER
    except (OSError, ValueError):
        owned = False
    if not owned:
        raise ValueError(f"Refusing to replace nonempty output without an owned {MARKER}: {output}")


def copy_prices(source, destination):
    destination.mkdir()
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict):
        raise ValueError("Price index must be a JSON object")
    index["snapshot_encoding"] = "gzip"
    (destination / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8")
    snapshot_count = 0
    snapshot_bytes = 0
    compressed_bytes = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if relative == Path("index.json"):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".json":
            target = target.with_name(target.name + ".gz")
            if path.with_name(path.name + ".gz").exists():
                raise ValueError(f"Compressed snapshot conflicts with source asset: {path}")
            with path.open("rb") as incoming, target.open("wb") as outgoing:
                with gzip.GzipFile(filename="", mode="wb", fileobj=outgoing, mtime=0) as compressed:
                    shutil.copyfileobj(incoming, compressed, length=1024 * 1024)
            snapshot_count += 1
            snapshot_bytes += path.stat().st_size
            compressed_bytes += target.stat().st_size
        else:
            shutil.copy2(path, target)
    return snapshot_count, snapshot_bytes, compressed_bytes


def publish(stage, output):
    validate_output(output)
    backup = None
    if output.exists():
        backup = Path(tempfile.mkdtemp(prefix=f".{output.name}-backup-", dir=output.parent))
        backup.rmdir()
        os.replace(output, backup)
    try:
        os.replace(stage, output)
    except OSError:
        if backup is not None:
            os.replace(backup, output)
        raise
    if backup is not None:
        shutil.rmtree(backup)


def build_site(docs_dir, data_dir, output_dir, *, max_bytes=MAX_SITE_BYTES):
    docs = Path(docs_dir).absolute()
    data = Path(data_dir).absolute()
    output = Path(output_dir).absolute()
    for path in (docs, data, output):
        reject_symlink_path(path)
    docs, data, output = docs.resolve(), data.resolve(), output.resolve()
    for source in (docs, data):
        if output == source or output in source.parents or source in output.parents:
            raise ValueError(f"Output and source directories must not overlap: {output}, {source}")
    validate_output(output)
    validate_tree(docs)
    validate_tree(data)
    if (docs / "data").exists() or (docs / MARKER).exists():
        raise ValueError("Docs contains a reserved data directory or build marker")
    if not (data / "prices" / "index.json").is_file():
        raise ValueError("Missing data/prices/index.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-stage-", dir=output.parent) as temporary:
        stage = Path(temporary) / "site"
        shutil.copytree(docs, stage)
        shutil.copytree(data, stage / "data", ignore=lambda directory, names:
                        ["prices"] if Path(directory) == data else [])
        count, raw_bytes, gzip_bytes = copy_prices(data / "prices", stage / "data" / "prices")
        artifacts = [path for path in stage.rglob("*") if path.is_file()]
        payload_bytes = sum(path.stat().st_size for path in artifacts)
        manifest = {
            "builder": BUILDER,
            "snapshot_encoding": "gzip",
            "snapshot_count": count,
            "snapshot_source_bytes": raw_bytes,
            "snapshot_gzip_bytes": gzip_bytes,
            "file_count": len(artifacts) + 1,
            "total_bytes": payload_bytes,
        }
        while True:
            encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
            total_bytes = payload_bytes + len(encoded)
            if total_bytes == manifest["total_bytes"]:
                break
            manifest["total_bytes"] = total_bytes
        (stage / MARKER).write_bytes(encoded)
        if total_bytes > max_bytes:
            raise ValueError(f"Site is {total_bytes} bytes; limit is {max_bytes} bytes (900 MiB by default)")
        publish(stage, output)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("_site"))
    args = parser.parse_args(argv)
    try:
        manifest = build_site(args.docs_dir, args.data_dir, args.output_dir)
    except (OSError, ValueError, shutil.Error) as error:
        parser.exit(1, f"Dashboard build failed: {error}\n")
    print(f"Built {manifest['snapshot_count']} gzip snapshots: "
          f"{manifest['snapshot_source_bytes']} source bytes -> "
          f"{manifest['snapshot_gzip_bytes']} gzip bytes; "
          f"{manifest['file_count']} files, {manifest['total_bytes']} total bytes "
          f"({manifest['total_bytes'] / (1024 * 1024):.2f} MiB) in {args.output_dir}")


if __name__ == "__main__":
    main()