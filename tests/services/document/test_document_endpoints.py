from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from src.app.services.document import endpoints as document_endpoints


@pytest.mark.asyncio
async def test_document_endpoints(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    folder_id = uuid4()
    doc_id = uuid4()

    async def fake_list(self, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "id": str(folder_id),
                "name": "Folder",
                "type": "folder",
                "size": None,
                "extension": None,
                "created_at": datetime.utcnow().isoformat(),
                "created_by_id": None,
                "created_by_name": None,
                "parent_id": None,
            }
        ]

    async def fake_create_folder(self, folder_data, user_id, user_role):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=folder_id, name=folder_data.name, parent_id=None, created_by_id=user_id, creator_name=None, created_at=datetime.utcnow()
        )

    async def fake_upload(self, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=doc_id,
            case_id=None,
            folder_id=None,
            title="Doc",
            file_size=10,
            file_extension="txt",
            uploaded_by_id=None,
            uploaded_by_name=None,
            created_at=datetime.utcnow(),
        )

    async def fake_url(self, document_id, download=False):  # type: ignore[no-untyped-def]
        return "http://download" if str(document_id) == str(doc_id) else None

    async def fake_add_zip(self, zip_file, folder_id, path, user_id, user_role):  # type: ignore[no-untyped-def]
        zip_file.writestr("a.txt", b"a")

    async def fake_delete_document(self, document_id):  # type: ignore[no-untyped-def]
        return str(document_id) == str(doc_id)

    async def fake_update_document(self, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=doc_id,
            case_id=None,
            folder_id=None,
            title="Updated",
            file_size=10,
            file_extension="txt",
            uploaded_by_id=None,
            uploaded_by_name=None,
            created_at=datetime.utcnow(),
        )

    async def fake_update_folder(self, **kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id=folder_id, name="Updated folder", parent_id=None, created_by_id=None, creator_name=None, created_at=datetime.utcnow()
        )

    monkeypatch.setattr(document_endpoints.DocumentService, "get_unified_list", fake_list)
    monkeypatch.setattr(document_endpoints.DocumentService, "create_folder", fake_create_folder)
    monkeypatch.setattr(document_endpoints.DocumentService, "upload_document", fake_upload)
    monkeypatch.setattr(document_endpoints.DocumentService, "get_presigned_url", fake_url)
    monkeypatch.setattr(document_endpoints.DocumentService, "add_folder_to_zip", fake_add_zip)
    monkeypatch.setattr(document_endpoints.DocumentService, "delete_document", fake_delete_document)
    monkeypatch.setattr(document_endpoints.DocumentService, "update_document", fake_update_document)
    monkeypatch.setattr(document_endpoints.DocumentService, "update_folder", fake_update_folder)

    l_resp = await client.get("/api/documents")
    c_resp = await client.post("/api/documents/folders", json={"name": "Folder", "parent_id": None})
    u_resp = await client.post("/api/documents/upload", files={"file": ("a.txt", b"abc", "text/plain")})
    url_ok = await client.get(f"/api/documents/{doc_id}/url")
    url_404 = await client.get(f"/api/documents/{uuid4()}/url")
    zip_404 = await client.get(f"/api/documents/folders/{uuid4()}/download")
    d_ok = await client.delete(f"/api/documents/{doc_id}")
    d_404 = await client.delete(f"/api/documents/{uuid4()}")
    upd_file = await client.patch("/api/documents/update", json={"asset_id": str(doc_id), "asset_type": "file", "data": {"title": "x"}})
    upd_folder = await client.patch("/api/documents/update", json={"asset_id": str(folder_id), "asset_type": "folder", "data": {"name": "x"}})

    assert l_resp.status_code == status.HTTP_200_OK
    assert c_resp.status_code == status.HTTP_201_CREATED
    assert u_resp.status_code == status.HTTP_201_CREATED
    assert url_ok.status_code == status.HTTP_200_OK
    assert url_404.status_code == status.HTTP_404_NOT_FOUND
    assert zip_404.status_code == status.HTTP_404_NOT_FOUND
    assert d_ok.status_code == status.HTTP_204_NO_CONTENT
    assert d_404.status_code == status.HTTP_404_NOT_FOUND
    assert upd_file.status_code == status.HTTP_200_OK
    assert upd_folder.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_delete_folder_and_update_not_found(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_update_document(self, **kwargs):  # type: ignore[no-untyped-def]
        return None

    async def fake_update_folder(self, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(document_endpoints.DocumentService, "update_document", fake_update_document)
    monkeypatch.setattr(document_endpoints.DocumentService, "update_folder", fake_update_folder)

    folder_id = uuid4()
    delete_resp = await client.delete(f"/api/documents/folders/{folder_id}")
    upd_file = await client.patch("/api/documents/update", json={"asset_id": str(uuid4()), "asset_type": "file", "data": {"title": "x"}})
    upd_folder = await client.patch("/api/documents/update", json={"asset_id": str(uuid4()), "asset_type": "folder", "data": {"name": "x"}})

    assert delete_resp.status_code == status.HTTP_204_NO_CONTENT
    assert upd_file.status_code == status.HTTP_404_NOT_FOUND
    assert upd_folder.status_code == status.HTTP_404_NOT_FOUND
