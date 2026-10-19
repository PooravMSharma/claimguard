import pytest

from app.services.storage import LocalStorage, StorageError


def test_put_get_delete(tmp_path):
    storage = LocalStorage(str(tmp_path))
    storage.put("claims/C1/bill.pdf", b"data")
    assert storage.exists("claims/C1/bill.pdf")
    assert storage.get("claims/C1/bill.pdf") == b"data"
    storage.delete("claims/C1/bill.pdf")
    assert not storage.exists("claims/C1/bill.pdf")


def test_rejects_path_traversal(tmp_path):
    storage = LocalStorage(str(tmp_path / "root"))
    with pytest.raises(StorageError):
        storage.put("../outside.txt", b"x")


def test_missing_file_raises(tmp_path):
    storage = LocalStorage(str(tmp_path))
    with pytest.raises(StorageError):
        storage.get("nope.pdf")


def test_check_passes(tmp_path):
    LocalStorage(str(tmp_path)).check()