"""Package-layout helpers for external assets referenced by the graphic IR."""

from __future__ import annotations

import hashlib
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from .model import Document, Group, LinkedImage

SUPPORTED_RASTER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})


@dataclass(frozen=True, slots=True)
class PackagedLink:
    """One source file copied or reused in a package's Links directory."""

    image_id: str
    source: Path
    destination: Path
    status: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "image_id": self.image_id,
            "source": str(self.source),
            "destination": str(self.destination),
            "status": self.status,
            "sha256": self.sha256,
        }


def iter_linked_images(document: Document) -> list[LinkedImage]:
    """Return linked images in document/container order."""

    def in_group(group: Group) -> list[LinkedImage]:
        return [
            *group.linked_images,
            *(image for child in group.groups for image in in_group(child)),
        ]

    return [
        image
        for layer in document.layers
        for image in [
            *layer.linked_images,
            *(image for group in layer.groups for image in in_group(group)),
        ]
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _available_destination(source: Path, links_directory: Path, digest: str) -> tuple[Path, str]:
    candidate = links_directory / source.name
    if not candidate.exists():
        return candidate, "copied"
    if candidate.is_file() and _sha256(candidate) == digest:
        return candidate, "reused"

    for length in range(8, len(digest) + 1, 4):
        candidate = links_directory / f"{source.stem}-{digest[:length]}{source.suffix.lower()}"
        if not candidate.exists():
            return candidate, "copied"
        if candidate.is_file() and _sha256(candidate) == digest:
            return candidate, "reused"
    raise ValueError(f"Could not allocate a collision-safe link name for {source.name!r}")


def package_linked_images(
    document: Document,
    package_directory: str | Path,
    *,
    source_base: str | Path | None = None,
) -> tuple[Document, list[PackagedLink]]:
    """Copy raster links into ``Links/`` and return a rewritten document copy.

    Existing files are reused only when their SHA-256 content matches. A same-name
    file with different content receives a deterministic hash suffix and is never
    overwritten.
    """

    packaged = deepcopy(document)
    images = iter_linked_images(packaged)
    if not images:
        return packaged, []

    base = Path(source_base).resolve() if source_base is not None else Path.cwd().resolve()
    resolved: list[tuple[LinkedImage, Path, str]] = []
    for image in images:
        source = Path(image.source).expanduser()
        source = source.resolve() if source.is_absolute() else (base / source).resolve()
        if not source.is_file():
            raise ValueError(f"Linked image does not exist or is not a file: {source}")
        if source.suffix.lower() not in SUPPORTED_RASTER_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_RASTER_SUFFIXES))
            raise ValueError(f"Unsupported linked image type {source.suffix!r}; use {supported}")
        resolved.append((image, source, _sha256(source)))

    package_root = Path(package_directory).resolve()
    links_directory = package_root / "Links"
    links_directory.mkdir(parents=True, exist_ok=True)
    results: list[PackagedLink] = []
    for image, source, digest in resolved:
        destination, status = _available_destination(source, links_directory, digest)
        if status == "copied":
            shutil.copy2(source, destination)
        image.source = str(Path("Links") / destination.name)
        results.append(
            PackagedLink(
                image_id=image.id,
                source=source,
                destination=destination,
                status=status,
                sha256=digest,
            )
        )
    return packaged, results
