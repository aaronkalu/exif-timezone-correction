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


def test_symlinks_are_handled_the_same_with_and_without_recursion(tmp_path):
    (tmp_path / "photo.jpg").touch()
    (tmp_path / "broken.jpg").symlink_to(tmp_path / "missing.jpg")
    (tmp_path / "alias.jpg").symlink_to(tmp_path / "photo.jpg")

    assert [p.name for p in find_images(tmp_path)] == ["alias.jpg"]
    assert [p.name for p in find_images(tmp_path, recursive=True)] == ["alias.jpg"]


def test_recursive_search_follows_symlinked_folders_without_looping(tmp_path):
    album = tmp_path / "elsewhere" / "album"
    album.mkdir(parents=True)
    (album / "nested.jpg").touch()
    root = tmp_path / "root"
    root.mkdir()
    (root / "linked").symlink_to(album, target_is_directory=True)
    (album / "loop").symlink_to(root, target_is_directory=True)

    assert [p.name for p in find_images(root, recursive=True)] == ["nested.jpg"]


def test_recursive_search(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.jpg").touch()
    (tmp_path / "sub" / "nested.jpg").touch()

    assert [p.name for p in find_images(tmp_path)] == ["top.jpg"]
    assert [p.name for p in find_images(tmp_path, recursive=True)] == ["nested.jpg", "top.jpg"]
