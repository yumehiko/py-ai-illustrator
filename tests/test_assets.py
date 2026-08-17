from pathlib import Path

import pytest

from py_ai_illustrator.assets import package_linked_images
from py_ai_illustrator.legacy import dump_ai7, load_ai7
from py_ai_illustrator.model import Document, Layer, LinkedImage


def image_document(source: Path) -> Document:
    return Document(
        width=200,
        height=160,
        layers=[
            Layer(
                id="artwork",
                name="Artwork",
                linked_images=[
                    LinkedImage(
                        id="photo",
                        source=str(source),
                        x=20,
                        y=140,
                        width=120,
                        height=90,
                    )
                ],
            )
        ],
    )


def test_package_copies_then_reuses_an_identical_link(tmp_path: Path) -> None:
    source = tmp_path / "source" / "photo.png"
    source.parent.mkdir()
    source.write_bytes(b"png-content")
    package = tmp_path / "package"

    first, first_results = package_linked_images(image_document(source), package)
    second, second_results = package_linked_images(image_document(source), package)

    assert first.layers[0].linked_images[0].source == "Links/photo.png"
    assert second.layers[0].linked_images[0].source == "Links/photo.png"
    assert first_results[0].status == "copied"
    assert second_results[0].status == "reused"
    assert (package / "Links" / "photo.png").read_bytes() == b"png-content"


def test_package_never_overwrites_a_different_same_name_link(tmp_path: Path) -> None:
    first_source = tmp_path / "first" / "photo.jpg"
    second_source = tmp_path / "second" / "photo.jpg"
    first_source.parent.mkdir()
    second_source.parent.mkdir()
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    package = tmp_path / "package"

    package_linked_images(image_document(first_source), package)
    second, results = package_linked_images(image_document(second_source), package)

    linked_name = Path(second.layers[0].linked_images[0].source).name
    assert linked_name.startswith("photo-")
    assert linked_name.endswith(".jpg")
    assert results[0].status == "copied"
    assert (package / "Links" / "photo.jpg").read_bytes() == b"first"
    assert (package / "Links" / linked_name).read_bytes() == b"second"


def test_dump_ai7_creates_a_portable_links_directory(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"image")
    output = tmp_path / "deliverable" / "layout.ai"
    output.parent.mkdir()

    dump_ai7(image_document(source), output)

    restored = load_ai7(output)
    assert restored.layers[0].linked_images[0].source == "Links/source.png"
    assert (output.parent / "Links" / "source.png").read_bytes() == b"image"


def test_package_rejects_unsupported_raster_types_before_creating_links(tmp_path: Path) -> None:
    source = tmp_path / "photo.webp"
    source.write_bytes(b"webp")
    package = tmp_path / "package"

    with pytest.raises(ValueError, match="Unsupported linked image type"):
        package_linked_images(image_document(source), package)

    assert not (package / "Links").exists()
