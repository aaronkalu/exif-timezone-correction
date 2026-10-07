from exif_timezone_correction.files import find_images


def test_finds_images_case_insensitively(tmp_path):
    for name in ["a.JPG", "b.cr3", "c.tif", "notes.txt"]:
        (tmp_path / name).touch()

    assert [p.name for p in find_images(tmp_path)] == ["a.JPG", "b.cr3", "c.tif"]


def test_ignores_directories_and_apple_double_files(tmp_path):
    (tmp_path / "album.jpg").mkdir()
    (tmp_path / "._photo.jpg").touch()
    (tmp_path / "photo.jpg").touch()

    assert [p.name for p in find_images(tmp_path)] == ["photo.jpg"]


def test_recursive_search(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.jpg").touch()
    (tmp_path / "sub" / "nested.jpg").touch()

    assert [p.name for p in find_images(tmp_path)] == ["top.jpg"]
    assert sorted(p.name for p in find_images(tmp_path, recursive=True)) == ["nested.jpg", "top.jpg"]
