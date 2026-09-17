"""Durable, provider-neutral project hand-offs for Ariadne media production.

The production project is intentionally a thin local record layer.  It does not
run a music, image, timing, or video engine; it records the accepted inputs and
candidate outputs so those engines can be changed without losing provenance.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_SCHEMA = "ariadne.media-project/v1"
ASSET_SCHEMA = "ariadne.media-asset/v1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_project_root() -> Path:
    configured = os.environ.get("ARIADNE_SEQUENCE_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured)
    try:
        from ariadne_config import configuration_snapshot
        music = configuration_snapshot().get("storage", {}).get("music")
        if music:
            return Path(str(music)) / "Ariadne Projects"
    except (ImportError, KeyError, TypeError, ValueError, OSError):
        pass
    return Path(r"D:\Downloads\Music\Ariadne Projects")


def safe_project_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:64]


class ProductionProjectStore:
    """File-backed project manifest store with no dependency on a media engine."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_project_root()).expanduser()

    def _project_dir(self, project_id: str) -> Path:
        candidate = self.root / project_id
        resolved_root = self.root.resolve()
        resolved_candidate = candidate.resolve()
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("Invalid project identifier.") from exc
        return resolved_candidate

    @staticmethod
    def _project_id(value: object) -> str:
        project_id = str(value or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", project_id):
            raise ValueError("Invalid project identifier.")
        return project_id

    @staticmethod
    def _asset_id(value: object) -> str:
        asset_id = str(value or "").strip()
        if not re.fullmatch(r"(?:image|music)-[a-f0-9]{12}", asset_id):
            raise ValueError("Invalid asset identifier.")
        return asset_id

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _manifest_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def create(self, name: str) -> dict[str, Any]:
        title = str(name or "").strip()
        if not title:
            raise ValueError("Enter a project name.")
        if len(title) > 120:
            raise ValueError("Project names must be 120 characters or fewer.")
        slug = safe_project_slug(title)
        if not slug:
            raise ValueError("Use letters or numbers in the project name.")
        project_id = slug
        self.root.mkdir(parents=True, exist_ok=True)
        suffix = 2
        while self._project_dir(project_id).exists():
            project_id = f"{slug}-{suffix}"
            suffix += 1
        directory = self._project_dir(project_id)
        for relative in (
            "music/candidates",
            "music/accepted",
            "analysis",
            "shot-plan",
            "candidates/images",
            "accepted/images",
            "candidates/video",
            "accepted/video",
            "handoff",
        ):
            (directory / relative).mkdir(parents=True, exist_ok=True)
        now = utc_now()
        manifest: dict[str, Any] = {
            "schema": PROJECT_SCHEMA,
            "project_id": project_id,
            "name": title,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
            "components": {
                "music": {"state": "provider_required", "asset": None},
                "analysis": {"state": "provider_required", "artifact": None},
                "shot_plan": {"state": "not_started", "artifact": None},
                "image": {"state": "available", "engine": "provider_neutral"},
                "video": {"state": "provider_required", "engine": "provider_neutral"},
            },
            "assets": {"music": [], "images": [], "video": []},
        }
        self._write_json(directory / "project.json", manifest)
        return self.project(project_id)

    def project(self, project_id: object) -> dict[str, Any]:
        normalized = self._project_id(project_id)
        path = self._manifest_path(normalized)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise FileNotFoundError(normalized) from exc
        if not isinstance(payload, dict) or payload.get("schema") != PROJECT_SCHEMA:
            raise ValueError("Project manifest is invalid.")
        return payload

    def _save_project(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = utc_now()
        self._write_json(self._manifest_path(manifest["project_id"]), manifest)

    def projects(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for candidate in self.root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                manifest = self.project(candidate.name)
            except (FileNotFoundError, ValueError):
                continue
            assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
            rows.append({
                "project_id": manifest["project_id"],
                "name": manifest.get("name", manifest["project_id"]),
                "status": manifest.get("status", "draft"),
                "updated_at": manifest.get("updated_at"),
                "music_count": len(assets.get("music", [])) if isinstance(assets.get("music"), list) else 0,
                "image_count": len(assets.get("images", [])) if isinstance(assets.get("images"), list) else 0,
            })
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def image_candidate_directory(self, project_id: object) -> Path:
        normalized = self._project_id(project_id)
        self.project(normalized)
        return self._project_dir(normalized) / "candidates" / "images"

    def music_candidate_directory(self, project_id: object) -> Path:
        normalized = self._project_id(project_id)
        self.project(normalized)
        return self._project_dir(normalized) / "music" / "candidates"

    def project_directory(self, project_id: object) -> Path:
        normalized = self._project_id(project_id)
        self.project(normalized)
        return self._project_dir(normalized)

    def image_asset(self, project_id: object, asset_id: object) -> dict[str, Any]:
        manifest = self.project(project_id)
        normalized_asset = self._asset_id(asset_id)
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        for asset in assets.get("images", []) if isinstance(assets.get("images"), list) else []:
            if isinstance(asset, dict) and asset.get("asset_id") == normalized_asset:
                return asset
        raise FileNotFoundError(normalized_asset)

    def music_asset(self, project_id: object, asset_id: object) -> dict[str, Any]:
        manifest = self.project(project_id)
        normalized_asset = self._asset_id(asset_id)
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        for asset in assets.get("music", []) if isinstance(assets.get("music"), list) else []:
            if isinstance(asset, dict) and asset.get("asset_id") == normalized_asset:
                return asset
        raise FileNotFoundError(normalized_asset)

    def record_image_candidate(self, project_id: object, image_path: Path, provenance: dict[str, Any]) -> dict[str, Any]:
        manifest = self.project(project_id)
        candidate_root = self.image_candidate_directory(manifest["project_id"]).resolve()
        resolved_image = image_path.resolve()
        try:
            resolved_image.relative_to(candidate_root)
        except ValueError as exc:
            raise ValueError("Image candidate must be inside its project folder.") from exc
        if not resolved_image.is_file() or resolved_image.suffix.casefold() != ".png":
            raise FileNotFoundError(resolved_image)
        asset_id = f"image-{uuid.uuid4().hex[:12]}"
        sidecar = resolved_image.with_suffix(".json")
        asset = {
            "schema": ASSET_SCHEMA,
            "asset_id": asset_id,
            "type": "image",
            "status": "candidate",
            "project_id": manifest["project_id"],
            "created_at": utc_now(),
            "files": {"media": str(resolved_image.relative_to(self._project_dir(manifest["project_id"]))), "metadata": str(sidecar.relative_to(self._project_dir(manifest["project_id"])))},
            "provenance": provenance,
        }
        self._write_json(sidecar, asset)
        assets = manifest.setdefault("assets", {}).setdefault("images", [])
        if not isinstance(assets, list):
            raise ValueError("Project manifest images collection is invalid.")
        assets.append(asset)
        self._save_project(manifest)
        return asset

    def accept_image(self, project_id: object, asset_id: object) -> dict[str, Any]:
        manifest = self.project(project_id)
        normalized_asset = self._asset_id(asset_id)
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        image_assets = assets.get("images") if isinstance(assets.get("images"), list) else []
        asset = next((item for item in image_assets if isinstance(item, dict) and item.get("asset_id") == normalized_asset), None)
        if asset is None:
            raise FileNotFoundError(normalized_asset)
        if asset.get("status") == "accepted":
            return asset
        files = asset.get("files") if isinstance(asset.get("files"), dict) else {}
        project_dir = self._project_dir(manifest["project_id"])
        source_media = (project_dir / str(files.get("media") or "")).resolve()
        source_metadata = (project_dir / str(files.get("metadata") or "")).resolve()
        source_root = self.image_candidate_directory(manifest["project_id"]).resolve()
        for source in (source_media, source_metadata):
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError("Project asset path is invalid.") from exc
            if not source.is_file():
                raise FileNotFoundError(source)
        accepted_root = (project_dir / "accepted" / "images").resolve()
        accepted_root.mkdir(parents=True, exist_ok=True)
        target_media = accepted_root / source_media.name
        target_metadata = accepted_root / source_metadata.name
        if target_media.exists() or target_metadata.exists():
            raise FileExistsError("An accepted copy of this asset already exists.")
        shutil.move(str(source_media), str(target_media))
        try:
            shutil.move(str(source_metadata), str(target_metadata))
        except Exception:
            shutil.move(str(target_media), str(source_media))
            raise
        asset["status"] = "accepted"
        asset["accepted_at"] = utc_now()
        asset["files"] = {"media": str(target_media.relative_to(project_dir)), "metadata": str(target_metadata.relative_to(project_dir))}
        self._write_json(target_metadata, asset)
        self._save_project(manifest)
        return asset

    def record_music_candidate(self, project_id: object, music_path: Path, provenance: dict[str, Any], mp3_path: Path | None = None) -> dict[str, Any]:
        manifest = self.project(project_id)
        candidate_root = self.music_candidate_directory(manifest["project_id"]).resolve()
        resolved_music = music_path.resolve()
        try:
            resolved_music.relative_to(candidate_root)
        except ValueError as exc:
            raise ValueError("Music candidate must be inside its project folder.") from exc
        if not resolved_music.is_file() or resolved_music.suffix.casefold() != ".wav":
            raise FileNotFoundError(resolved_music)
        resolved_mp3 = mp3_path.resolve() if mp3_path else None
        if resolved_mp3 is not None:
            try:
                resolved_mp3.relative_to(candidate_root)
            except ValueError as exc:
                raise ValueError("Music MP3 companion must be inside its project folder.") from exc
            if not resolved_mp3.is_file() or resolved_mp3.suffix.casefold() != ".mp3":
                raise FileNotFoundError(resolved_mp3)
        asset_id = f"music-{uuid.uuid4().hex[:12]}"
        sidecar = resolved_music.with_suffix(".json")
        asset = {
            "schema": ASSET_SCHEMA,
            "asset_id": asset_id,
            "type": "music",
            "status": "candidate",
            "project_id": manifest["project_id"],
            "created_at": utc_now(),
            "files": {"media": str(resolved_music.relative_to(self._project_dir(manifest["project_id"]))), "metadata": str(sidecar.relative_to(self._project_dir(manifest["project_id"])))},
            "provenance": provenance,
        }
        if resolved_mp3 is not None:
            asset["files"]["mp3"] = str(resolved_mp3.relative_to(self._project_dir(manifest["project_id"])))
        self._write_json(sidecar, asset)
        assets = manifest.setdefault("assets", {}).setdefault("music", [])
        if not isinstance(assets, list):
            raise ValueError("Project manifest music collection is invalid.")
        assets.append(asset)
        manifest.setdefault("components", {}).setdefault("music", {})["state"] = "candidate_ready"
        self._save_project(manifest)
        return asset

    def accept_music(self, project_id: object, asset_id: object) -> dict[str, Any]:
        manifest = self.project(project_id)
        normalized_asset = self._asset_id(asset_id)
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        music_assets = assets.get("music") if isinstance(assets.get("music"), list) else []
        asset = next((item for item in music_assets if isinstance(item, dict) and item.get("asset_id") == normalized_asset), None)
        if asset is None:
            raise FileNotFoundError(normalized_asset)
        if asset.get("status") == "accepted":
            return asset
        files = asset.get("files") if isinstance(asset.get("files"), dict) else {}
        project_dir = self._project_dir(manifest["project_id"])
        source_media = (project_dir / str(files.get("media") or "")).resolve()
        source_metadata = (project_dir / str(files.get("metadata") or "")).resolve()
        source_mp3 = (project_dir / str(files.get("mp3") or "")).resolve() if files.get("mp3") else None
        source_root = self.music_candidate_directory(manifest["project_id"]).resolve()
        for source in tuple(item for item in (source_media, source_metadata, source_mp3) if item is not None):
            try:
                source.relative_to(source_root)
            except ValueError as exc:
                raise ValueError("Project asset path is invalid.") from exc
            if not source.is_file():
                raise FileNotFoundError(source)
        accepted_root = (project_dir / "music" / "accepted").resolve()
        accepted_root.mkdir(parents=True, exist_ok=True)
        target_media = accepted_root / source_media.name
        target_metadata = accepted_root / source_metadata.name
        target_mp3 = accepted_root / source_mp3.name if source_mp3 is not None else None
        if target_media.exists() or target_metadata.exists() or (target_mp3 is not None and target_mp3.exists()):
            raise FileExistsError("An accepted copy of this asset already exists.")
        shutil.move(str(source_media), str(target_media))
        if source_mp3 is not None:
            try:
                shutil.move(str(source_mp3), str(target_mp3))
            except Exception:
                shutil.move(str(target_media), str(source_media))
                raise
        try:
            shutil.move(str(source_metadata), str(target_metadata))
        except Exception:
            if target_mp3 is not None and target_mp3.exists():
                shutil.move(str(target_mp3), str(source_mp3))
            shutil.move(str(target_media), str(source_media))
            raise
        asset["status"] = "accepted"
        asset["accepted_at"] = utc_now()
        asset["files"] = {"media": str(target_media.relative_to(project_dir)), "metadata": str(target_metadata.relative_to(project_dir))}
        if target_mp3 is not None:
            asset["files"]["mp3"] = str(target_mp3.relative_to(project_dir))
        self._write_json(target_metadata, asset)
        manifest.setdefault("components", {}).setdefault("music", {})["state"] = "accepted"
        manifest["components"]["music"]["asset"] = asset["asset_id"]
        self._save_project(manifest)
        return asset
