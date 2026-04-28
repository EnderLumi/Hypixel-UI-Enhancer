from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

_MISSING_DEPENDENCIES: list[tuple[str, str]] = []

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]
    _MISSING_DEPENDENCIES.append(("Pillow", "PIL"))

try:
    from PySide6.QtCore import Qt, Signal
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QButtonGroup,
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QStackedWidget,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    _MISSING_DEPENDENCIES.append(("PySide6", "PySide6"))

    class _QtPlaceholder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("PySide6 is not installed.")

    class _QtEnumPlaceholder:
        def __getattr__(self, _name: str) -> "_QtEnumPlaceholder":
            return self

    def Signal(*_args: Any, **_kwargs: Any) -> None:  # type: ignore[override]
        return None

    Qt = _QtEnumPlaceholder()
    QPixmap = _QtPlaceholder
    QApplication = _QtPlaceholder
    QButtonGroup = _QtPlaceholder
    QFileDialog = _QtPlaceholder
    QFrame = _QtPlaceholder
    QHBoxLayout = _QtPlaceholder
    QLabel = _QtPlaceholder
    QLineEdit = _QtPlaceholder
    QMainWindow = _QtPlaceholder
    QMessageBox = _QtPlaceholder
    QPushButton = _QtPlaceholder
    QScrollArea = _QtPlaceholder
    QSizePolicy = _QtPlaceholder
    QStackedWidget = _QtPlaceholder
    QVBoxLayout = _QtPlaceholder
    QWidget = _QtPlaceholder

DEPENDENCIES_OK = not _MISSING_DEPENDENCIES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ITEM_ID: str = "402"
DEFAULT_ENTRY_TYPE = "map"


@dataclass(frozen=True)
class EntryTypeDefinition:
    key: str
    display_name: str
    drop_zone_hint: str
    preserve_original_size: bool
    requires_overlay: bool
    allows_custom_item_id: bool
    supports_favorite_variant: bool

FAVORITE_MARKER = r"\u272B"
MAP_TARGET_SIZE = (300, 300)
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FEEDBACK_LEVEL = Literal["info", "success", "warning", "error"]

_REL_MAPS_NOR = Path("maps") / "maps_nor"
_REL_MAPS_FAV = Path("maps") / "maps_fav"
_REL_OTHER = Path("other")
_REL_ASSETS = Path("assets")
_REL_DATA = Path("data")

# Drop zone hint messages
DROP_ZONE_HINT_DEFAULT = "Drag an image here or click to choose one"
DROP_ZONE_HINT_MAP = "Drag an image here or click to choose one\n\n(Images will be resized to 300×300px)"
DROP_ZONE_HINT_NORMAL = "Drag an image here or click to choose one\n\n(Original resolution will be preserved)"

ENTRY_TYPE_DEFINITIONS: dict[str, EntryTypeDefinition] = {
    "map": EntryTypeDefinition(
        key="map",
        display_name="Map",
        drop_zone_hint=DROP_ZONE_HINT_MAP,
        preserve_original_size=False,
        requires_overlay=True,
        allows_custom_item_id=False,
        supports_favorite_variant=True,
    ),
    "normal": EntryTypeDefinition(
        key="normal",
        display_name="Normal",
        drop_zone_hint=DROP_ZONE_HINT_NORMAL,
        preserve_original_size=True,
        requires_overlay=False,
        allows_custom_item_id=True,
        supports_favorite_variant=False,
    ),
}
ENTRY_TYPES: tuple[str, ...] = tuple(ENTRY_TYPE_DEFINITIONS.keys())

# Card layout configuration
CARD_HEIGHT = 59  # Fixed height for entry cards (adjust this value as needed)
CARD_THUMBNAIL_PADDING = 8  # Padding around thumbnail within the card


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    value = name.strip().lower()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^a-z0-9_-]", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "entry"


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in ALLOWED_EXTENSIONS


def print_missing_dependencies() -> None:
    if not _MISSING_DEPENDENCIES:
        return
    print("Fehlende Abhaengigkeiten fuer 'app.py':")
    for package, module_name in _MISSING_DEPENDENCIES:
        print(f"  - {package} (Modul: {module_name})")
    print()
    print("Installiere sie und starte dann erneut:")
    print("  python -m pip install Pillow PySide6")


def resolve_entry_type(entry_type: str | None) -> str:
    candidate = str(entry_type or "").strip().lower()
    if candidate in ENTRY_TYPE_DEFINITIONS:
        return candidate
    return DEFAULT_ENTRY_TYPE


def get_entry_type_definition(entry_type: str) -> EntryTypeDefinition:
    return ENTRY_TYPE_DEFINITIONS[resolve_entry_type(entry_type)]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TextureEntry:
    """
    Represents a single CIT texture entry.

    Fields
    ------
    name          : Display name shown in-game and in the UI.
    picture_name  : Optional short identifier to prevent accidental image reuse.
    slug          : Filesystem-safe identifier derived from `name`.
    entry_type    : "map" or "normal". Determines folder layout and whether a
                    favourite variant is generated.
    item_id       : Minecraft numeric item ID used in the .properties file.
    normal_image  : Path to the standard PNG, relative to the repository root.
    favorite_image: Path to the favourite PNG (map type only), relative to root.
                    Empty string for normal entries.
    created_at    : ISO-8601 timestamp of creation; empty when auto-discovered.
    """

    name: str
    picture_name: str
    slug: str
    entry_type: str       # "map" | "normal"
    item_id: str          # e.g. "402"
    normal_image: str
    favorite_image: str   # empty for normal entries
    created_at: str


# ---------------------------------------------------------------------------
# Image Processing
# ---------------------------------------------------------------------------

class ImageProcessor:
    """Handles image loading, validation, and transformation logic."""

    @staticmethod
    def open_image(image_path: Path) -> Image.Image:
        """Open and convert an image to RGBA."""
        try:
            with Image.open(image_path) as raw:
                return raw.convert("RGBA")
        except OSError as error:
            raise ValueError(f"Image could not be read: {error}") from error

    @staticmethod
    def normalize_for_map(image: Image.Image) -> Image.Image:
        """Resize image to MAP_TARGET_SIZE for map entries."""
        if image.size != MAP_TARGET_SIZE:
            return image.resize(MAP_TARGET_SIZE, Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def preserve_original(image: Image.Image) -> Image.Image:
        """Return image unchanged (for normal entries)."""
        return image

    @staticmethod
    def create_favorite_variant(
        base_image: Image.Image, overlay_path: Path
    ) -> Image.Image:
        """Composite the favorite overlay onto the base image."""
        with Image.open(overlay_path) as ov_raw:
            overlay = ov_raw.convert("RGBA")
            if overlay.size != base_image.size:
                overlay = overlay.resize(base_image.size, Image.Resampling.LANCZOS)
            return Image.alpha_composite(base_image, overlay)

    @staticmethod
    def get_dimensions(image_path: Path) -> tuple[int, int]:
        """Get image dimensions without fully loading it."""
        with Image.open(image_path) as img:
            return img.size


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class MapRepository:
    """
    Manages all CIT entries on disk and in entrys.json.

    Directory layout (all paths relative to `root`):
        fav_overlay.png          — overlay composited onto map favourites
        entrys.json              — metadata for all entries
        cit/
          maps/
            maps_nor/<slug>/     — standard map PNG + .properties
            maps_fav/<slug>/     — favourite map PNG + .properties
          other/<slug>/          — normal (non-map) PNG + .properties
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.cit_root = root / "cit"
        self.maps_nor_root = self.cit_root / _REL_MAPS_NOR
        self.maps_fav_root = self.cit_root / _REL_MAPS_FAV
        self.other_root = self.cit_root / _REL_OTHER
        self.assets_root = root / _REL_ASSETS
        self.data_root = root / _REL_DATA
        self.overlay_path = self.assets_root / "fav_overlay.png"
        self.metadata_path = self.data_root / "entrys.json"

        self.maps_nor_root.mkdir(parents=True, exist_ok=True)
        self.maps_fav_root.mkdir(parents=True, exist_ok=True)
        self.other_root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)

        self._image_processor = ImageProcessor()

    # ------------------------------------------------------------------
    # Loading / saving
    # ------------------------------------------------------------------

    def load_entries(self) -> tuple[list[TextureEntry], list[str]]:
        """
        Load entries from entrys.json, merge with any newly discovered
        on-disk entries, and return (entries, warnings).
        """
        warnings: list[str] = []
        entries: list[TextureEntry] = []

        if self.metadata_path.exists():
            try:
                with self.metadata_path.open("r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                if not isinstance(payload, dict):
                    raise ValueError("entrys.json has an invalid format.")
                raw_entries = payload.get("entries", [])
                if not isinstance(raw_entries, list):
                    raise ValueError("entrys.json does not contain a valid list of entries.")
                for raw in raw_entries:
                    if not isinstance(raw, dict):
                        continue
                    entry = TextureEntry(
                        name=str(raw.get("name", "")).strip(),
                        picture_name=str(raw.get("picture_name", raw.get("block", ""))).strip(),
                        slug=str(raw.get("slug", "")).strip(),
                        entry_type=resolve_entry_type(
                            str(raw.get("entry_type", DEFAULT_ENTRY_TYPE)).strip() or DEFAULT_ENTRY_TYPE
                        ),
                        item_id=str(raw.get("item_id", ITEM_ID)).strip() or ITEM_ID,
                        normal_image=str(raw.get("normal_image", "")).strip(),
                        favorite_image=str(raw.get("favorite_image", "")).strip(),
                        created_at=str(raw.get("created_at", "")).strip(),
                    )
                    if entry.name and entry.slug:
                        entries.append(entry)
            except json.JSONDecodeError as error:
                backup = self._backup_broken_metadata()
                msg = f"entrys.json was invalid and was saved as {backup.name}." if backup else \
                      "entrys.json was invalid and could not be backed up."
                warnings.extend([msg, f"Details: {error}"])
            except ValueError as error:
                backup = self._backup_broken_metadata()
                msg = f"entrys.json had an invalid structure and was saved as {backup.name}." if backup else \
                      "entrys.json had an invalid structure and could not be backed up."
                warnings.extend([msg, f"Details: {error}"])
            except OSError as error:
                warnings.append(f"entrys.json could not be read: {error}")

        by_slug = {entry.slug: entry for entry in entries}
        discovered_new = False
        for scanned in self.scan_existing_entries():
            if scanned.slug not in by_slug:
                by_slug[scanned.slug] = scanned
                discovered_new = True

        merged = sorted(by_slug.values(), key=lambda e: e.name.casefold())
        if not self.metadata_path.exists() or discovered_new:
            try:
                self.save_entries(merged)
            except OSError as error:
                warnings.append(f"entrys.json could not be saved: {error}")

        return merged, warnings

    def save_entries(self, entries: list[TextureEntry]) -> None:
        payload = {
            "version": 2,
            "entries": [asdict(entry) for entry in entries],
        }
        with self.metadata_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Scanning (auto-discovery)
    # ------------------------------------------------------------------

    def scan_existing_entries(self) -> list[TextureEntry]:
        """
        Walk the cit/ directory tree and return all entries found on disk
        that are not yet tracked in entrys.json.
        """
        return self._scan_map_entries() + self._scan_normal_entries()

    def _scan_map_entries(self) -> list[TextureEntry]:
        """Discover entries under cit/maps/maps_nor/."""
        entries: list[TextureEntry] = []
        if not self.maps_nor_root.exists():
            return entries
        try:
            folders = sorted(self.maps_nor_root.iterdir())
        except OSError:
            return entries

        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                png_files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".png")
                props_files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".properties")
            except OSError:
                continue
            if not png_files:
                continue

            slug = folder.name
            name = ""
            if props_files:
                name, _ = self._parse_name_from_properties(props_files[0])
            if not name:
                name = slug.replace("_", " ").strip().title()

            normal_rel = png_files[0].relative_to(self.root).as_posix()
            fav_candidate = self.maps_fav_root / slug / png_files[0].name
            favorite_rel = (
                fav_candidate.relative_to(self.root).as_posix()
                if fav_candidate.exists()
                else ""
            )

            entries.append(TextureEntry(
                name=name,
                picture_name="",
                slug=slug,
                entry_type="map",
                item_id=ITEM_ID,
                normal_image=normal_rel,
                favorite_image=favorite_rel,
                created_at="",
            ))
        return entries

    def _scan_normal_entries(self) -> list[TextureEntry]:
        """Discover entries under cit/other/."""
        entries: list[TextureEntry] = []
        if not self.other_root.exists():
            return entries
        try:
            folders = sorted(self.other_root.iterdir())
        except OSError:
            return entries

        for folder in folders:
            if not folder.is_dir():
                continue
            try:
                png_files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".png")
                props_files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".properties")
            except OSError:
                continue
            if not png_files:
                continue

            slug = folder.name
            name = ""
            item_id = ITEM_ID
            if props_files:
                name, parsed_item_id = self._parse_name_from_properties(props_files[0])
                if parsed_item_id:
                    item_id = parsed_item_id
            if not name:
                name = slug.replace("_", " ").strip().title()

            entries.append(TextureEntry(
                name=name,
                picture_name="",
                slug=slug,
                entry_type="normal",
                item_id=item_id,
                normal_image=png_files[0].relative_to(self.root).as_posix(),
                favorite_image="",
                created_at="",
            ))
        return entries

    def _parse_name_from_properties(self, props_path: Path) -> tuple[str, str]:
        """
        Parse (name, item_id) from a .properties file.
        Returns ('', '') if the file cannot be read or parsed.
        """
        try:
            props: dict[str, str] = {}
            for line in props_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                props[key.strip()] = value.strip()

            item_id = props.get("items", "")
            raw_pattern = props.get("nbt.display.Name", "")

            match = re.search(r"ipattern:\*(?:\\u272B\*)?(.*?)\*\s*$", raw_pattern)
            if match:
                name = (
                    match.group(1)
                    .replace(r"\*", "*")
                    .replace(r"\?", "?")
                    .replace("\\\\", "\\")
                    .strip()
                )
                return name, item_id
            return "", item_id
        except OSError:
            return "", ""

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_entry(
        self,
        entries: list[TextureEntry],
        name: str,
        picture_name: str,
        source_image_path: Path,
        entry_type: str = "map",
        item_id: str = ITEM_ID,
    ) -> TextureEntry:
        name = name.strip()
        picture_name = picture_name.strip()
        entry_type = resolve_entry_type(entry_type)
        item_id = self._normalize_item_id(entry_type=entry_type, requested_item_id=item_id)
        entry_type_def = get_entry_type_definition(entry_type)

        self._validate_entry_values(name=name)

        if entry_type_def.requires_overlay and not self.overlay_path.exists():
            raise ValueError("fav_overlay.png was not found in the root folder.")
        if not source_image_path.exists() or not is_image_file(source_image_path):
            raise ValueError("Please select a valid image.")

        self._check_name_uniqueness(entries, name)
        self._check_picture_name_uniqueness(entries, picture_name)

        slug = self._build_unique_slug(name, {e.slug for e in entries})
        base_image = self._prepare_image_for_entry_type(source_image_path, entry_type)

        created_dirs: list[Path] = []
        created_files: list[Path] = []

        try:
            normal_rel, favorite_rel = self._write_entry_files(
                entry_type=entry_type,
                slug=slug,
                name=name,
                item_id=item_id,
                base_image=base_image,
                created_dirs=created_dirs,
                created_files=created_files,
            )
        except Exception as error:
            self._rollback_created_files(created_files=created_files, created_dirs=created_dirs)
            if isinstance(error, ValueError):
                raise
            raise OSError(f"Files could not be created: {error}") from error

        return TextureEntry(
            name=name,
            picture_name=picture_name,
            slug=slug,
            entry_type=entry_type,
            item_id=item_id,
            normal_image=normal_rel,
            favorite_image=favorite_rel,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def update_entry(
        self,
        entries: list[TextureEntry],
        original_slug: str,
        name: str,
        picture_name: str,
        source_image_path: Path | None,
        item_id: str | None = None,
    ) -> TextureEntry:
        current = next((e for e in entries if e.slug == original_slug), None)
        if current is None:
            raise ValueError("The selected entry could not be found.")

        name = name.strip()
        picture_name = picture_name.strip()
        entry_type = resolve_entry_type(current.entry_type)
        effective_item_id = self._normalize_item_id(
            entry_type=entry_type,
            requested_item_id=item_id,
            fallback_item_id=current.item_id,
        )
        entry_type_def = get_entry_type_definition(entry_type)

        self._validate_entry_values(name=name)

        if entry_type_def.requires_overlay and not self.overlay_path.exists():
            raise ValueError("fav_overlay.png was not found in the root folder.")

        other_entries = [e for e in entries if e.slug != original_slug]
        self._check_name_uniqueness(other_entries, name)
        self._check_picture_name_uniqueness(other_entries, picture_name)

        existing_slugs = {e.slug for e in other_entries}
        desired_slug = slugify(name)
        slug_matches_original = desired_slug.casefold() == original_slug.casefold()
        existing_slug_keys = {slug.casefold() for slug in existing_slugs}
        if slug_matches_original:
            # Keep original casing to avoid case-only rename issues on
            # case-insensitive file systems (e.g. default macOS).
            target_slug = original_slug
        elif desired_slug.casefold() not in existing_slug_keys:
            target_slug = desired_slug
        else:
            target_slug = self._build_unique_slug(name, existing_slugs)

        if source_image_path is None:
            source_image_path = self.root / current.normal_image
        if not source_image_path.exists() or not is_image_file(source_image_path):
            raise ValueError("Please select a valid image.")

        base_image = self._prepare_image_for_entry_type(source_image_path, entry_type)
        existing_snapshot: dict[Path, bytes | None] | None = None
        if target_slug == original_slug:
            existing_snapshot = self._capture_entry_snapshot(slug=original_slug, entry_type=entry_type)
        created_dirs: list[Path] = []
        created_files: list[Path] = []

        try:
            normal_rel, favorite_rel = self._write_entry_files(
                entry_type=entry_type,
                slug=target_slug,
                name=name,
                item_id=effective_item_id,
                base_image=base_image,
                created_dirs=created_dirs,
                created_files=created_files,
            )
        except Exception as error:
            if existing_snapshot is not None:
                self._restore_entry_snapshot(existing_snapshot)
            else:
                self._rollback_created_files(created_files=created_files, created_dirs=created_dirs)
            if isinstance(error, ValueError):
                raise
            raise OSError(f"Files could not be updated: {error}") from error

        if target_slug != original_slug:
            self._remove_entry_files(original_slug, entry_type)

        return TextureEntry(
            name=name,
            picture_name=picture_name,
            slug=target_slug,
            entry_type=entry_type,
            item_id=effective_item_id,
            normal_image=normal_rel,
            favorite_image=favorite_rel,
            created_at=current.created_at or datetime.now().isoformat(timespec="seconds"),
        )

    def delete_entry(self, slug: str, entry_type: str) -> None:
        self._remove_entry_files(slug, entry_type)

    # ------------------------------------------------------------------
    # Private: image preparation
    # ------------------------------------------------------------------

    def _prepare_image_for_entry_type(
        self, image_path: Path, entry_type: str
    ) -> Image.Image:
        """Load and process image according to entry type rules."""
        image = self._image_processor.open_image(image_path)
        entry_type_def = get_entry_type_definition(entry_type)
        if entry_type_def.preserve_original_size:
            return self._image_processor.preserve_original(image)
        return self._image_processor.normalize_for_map(image)

    def _normalize_item_id(
        self,
        entry_type: str,
        requested_item_id: str | None,
        fallback_item_id: str = ITEM_ID,
    ) -> str:
        entry_type_def = get_entry_type_definition(entry_type)
        if not entry_type_def.allows_custom_item_id:
            return ITEM_ID
        return (requested_item_id or "").strip() or fallback_item_id or ITEM_ID

    # ------------------------------------------------------------------
    # Private: file writing helpers
    # ------------------------------------------------------------------

    def _write_entry_files(
        self,
        entry_type: str,
        slug: str,
        name: str,
        item_id: str,
        base_image: Image.Image,
        created_dirs: list[Path],
        created_files: list[Path],
    ) -> tuple[str, str]:
        entry_type = resolve_entry_type(entry_type)
        if entry_type == "map":
            return self._write_map_files(
                slug=slug,
                name=name,
                base_image=base_image,
                created_dirs=created_dirs,
                created_files=created_files,
            )
        if entry_type == "normal":
            return self._write_normal_files(
                slug=slug,
                name=name,
                item_id=item_id,
                base_image=base_image,
                created_dirs=created_dirs,
                created_files=created_files,
            )
        raise ValueError(f"Unsupported entry type '{entry_type}'.")

    def _write_map_files(
        self,
        slug: str,
        name: str,
        base_image: Image.Image,
        created_dirs: list[Path],
        created_files: list[Path],
    ) -> tuple[str, str]:
        """Write PNG + .properties for both normal and favourite map variants."""
        nor_folder = self.maps_nor_root / slug
        fav_folder = self.maps_fav_root / slug

        for folder in (nor_folder, fav_folder):
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
                created_dirs.append(folder)

        nor_png = nor_folder / f"{slug}.png"
        fav_png = fav_folder / f"{slug}.png"
        nor_props = nor_folder / f"{slug}.properties"
        fav_props = fav_folder / f"{slug}.properties"

        base_image.save(nor_png, format="PNG")
        created_files.append(nor_png)

        fav_img = self._image_processor.create_favorite_variant(
            base_image, self.overlay_path
        )
        fav_img.save(fav_png, format="PNG")
        created_files.append(fav_png)

        nor_props.write_text(
            self._build_properties(name=name, texture=slug, item_id=ITEM_ID, favorite=False),
            encoding="utf-8",
        )
        created_files.append(nor_props)
        fav_props.write_text(
            self._build_properties(name=name, texture=slug, item_id=ITEM_ID, favorite=True),
            encoding="utf-8",
        )
        created_files.append(fav_props)

        return (
            nor_png.relative_to(self.root).as_posix(),
            fav_png.relative_to(self.root).as_posix(),
        )

    def _write_normal_files(
        self,
        slug: str,
        name: str,
        item_id: str,
        base_image: Image.Image,
        created_dirs: list[Path],
        created_files: list[Path],
    ) -> tuple[str, str]:
        """Write PNG + .properties for a single normal (non-map) entry."""
        folder = self.other_root / slug
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
            created_dirs.append(folder)

        png_path = folder / f"{slug}.png"
        props_path = folder / f"{slug}.properties"

        base_image.save(png_path, format="PNG")
        created_files.append(png_path)

        props_path.write_text(
            self._build_properties(name=name, texture=slug, item_id=item_id, favorite=False),
            encoding="utf-8",
        )
        created_files.append(props_path)

        return png_path.relative_to(self.root).as_posix(), ""

    # ------------------------------------------------------------------
    # Private: validation helpers
    # ------------------------------------------------------------------

    def _check_name_uniqueness(self, entries: list[TextureEntry], name: str) -> None:
        if any(e.name.casefold() == name.casefold() for e in entries):
            raise ValueError("This name already exists.")

    def _check_picture_name_uniqueness(
        self, entries: list[TextureEntry], picture_name: str
    ) -> None:
        if not picture_name:
            return
        clash = next(
            (e for e in entries if e.picture_name and e.picture_name.casefold() == picture_name.casefold()),
            None,
        )
        if clash:
            raise ValueError(
                f"The picture description '{picture_name}' is already used by '{clash.name}'."
            )

    def _validate_entry_values(self, name: str) -> None:
        if not name:
            raise ValueError("The name cannot be empty.")
        if any(char in name for char in ("\n", "\r", "\t")):
            raise ValueError("The name cannot contain line breaks or tabs.")
        if len(name) > 80:
            raise ValueError("The name can have a maximum of 80 characters.")

    # ------------------------------------------------------------------
    # Private: low-level file helpers
    # ------------------------------------------------------------------

    def _build_unique_slug(self, name: str, existing_slugs: set[str]) -> str:
        base = slugify(name)
        existing_keys = {slug.casefold() for slug in existing_slugs}
        if base.casefold() not in existing_keys:
            return base
        index = 2
        while f"{base}_{index}".casefold() in existing_keys:
            index += 1
        return f"{base}_{index}"

    def _build_properties(
        self, name: str, texture: str, item_id: str, favorite: bool = False
    ) -> str:
        escaped = self._escape_ipattern_literal(name)
        pattern = f"*{FAVORITE_MARKER}*{escaped}*" if favorite else f"*{escaped}*"
        return (
            "type=item\n"
            f"items={item_id}\n"
            f"nbt.display.Name=ipattern:{pattern}\n"
            f"texture={texture}\n"
        )

    def _escape_ipattern_literal(self, value: str) -> str:
        escaped = value.replace("\\", "\\\\")
        escaped = escaped.replace("*", r"\*").replace("?", r"\?")
        return escaped

    def _rollback_created_files(
        self, created_files: list[Path], created_dirs: list[Path]
    ) -> None:
        for path in reversed(created_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass

    def _backup_broken_metadata(self) -> Path | None:
        if not self.metadata_path.exists():
            return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = self.root / f"maps.broken.{ts}.json"
        try:
            self.metadata_path.replace(backup)
            return backup
        except OSError:
            return None

    def _remove_entry_files(self, slug: str, entry_type: str) -> None:
        files_by_folder: dict[Path, list[Path]] = {}
        for file_path in self._iter_expected_entry_files(slug=slug, entry_type=entry_type):
            files_by_folder.setdefault(file_path.parent, []).append(file_path)

        for folder, files in files_by_folder.items():
            for file_path in files:
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass

    def _capture_entry_snapshot(self, slug: str, entry_type: str) -> dict[Path, bytes | None]:
        snapshot: dict[Path, bytes | None] = {}
        for file_path in self._iter_expected_entry_files(slug=slug, entry_type=entry_type):
            if file_path.exists():
                try:
                    snapshot[file_path] = file_path.read_bytes()
                except OSError as error:
                    raise OSError(f"Existing files could not be backed up: {error}") from error
            else:
                snapshot[file_path] = None
        return snapshot

    def _restore_entry_snapshot(self, snapshot: dict[Path, bytes | None]) -> None:
        for file_path, content in snapshot.items():
            try:
                if content is None:
                    file_path.unlink(missing_ok=True)
                    continue
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)
            except OSError:
                pass

    def _iter_expected_entry_files(self, slug: str, entry_type: str) -> list[Path]:
        file_names = (f"{slug}.png", f"{slug}.properties")
        if get_entry_type_definition(entry_type).supports_favorite_variant:
            folders = [self.maps_nor_root / slug, self.maps_fav_root / slug]
        else:
            folders = [self.other_root / slug]
        return [folder / file_name for folder in folders for file_name in file_names]


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class TypeSelector(QFrame):
    """A segmented-control-style toggle to pick between entry types."""

    type_changed = Signal(str)

    _STYLE_ACTIVE = (
        "QPushButton { background-color: #3b82f6; color: #ffffff; "
        "border: 1px solid #5C9AFF; border-radius: 0px; "
        "padding: 6px 18px; font-weight: 600; }"
    )
    _STYLE_INACTIVE = (
        "QPushButton { background-color: #4F4F4D; color: #9ca3af; "
        "border: 1px solid #656565; border-radius: 0px; padding: 5px 16px; }"
        "QPushButton:hover { background-color: #656565; color: #E4E4E4; }"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        initial_type = DEFAULT_ENTRY_TYPE if DEFAULT_ENTRY_TYPE in ENTRY_TYPES else ENTRY_TYPES[0]
        self._selected_type = initial_type

        for entry_type in ENTRY_TYPES:
            type_def = get_entry_type_definition(entry_type)
            button = QPushButton(type_def.display_name)
            button.setCheckable(True)
            button.setChecked(entry_type == initial_type)
            button.clicked.connect(
                lambda _checked=False, value=entry_type: self._select(value)
            )
            self._group.addButton(button)
            self._buttons[entry_type] = button
            layout.addWidget(button)

        layout.addStretch()

        self._update_styles()

    @property
    def selected_type(self) -> str:
        return self._selected_type

    def reset(self) -> None:
        self._select(DEFAULT_ENTRY_TYPE)

    def _select(self, entry_type: str) -> None:
        resolved = resolve_entry_type(entry_type)
        button = self._buttons.get(resolved)
        if button is None:
            return
        button.setChecked(True)
        self._selected_type = resolved
        self._update_styles()
        self.type_changed.emit(resolved)

    def _update_styles(self) -> None:
        for button in self._buttons.values():
            button.setStyleSheet(
                self._STYLE_ACTIVE if button.isChecked() else self._STYLE_INACTIVE
            )


class ImageDropZone(QFrame):
    """
    A drag-and-drop zone for selecting images.

    Supports contextual hint messages based on entry type.
    """

    image_selected = Signal(str)
    image_rejected = Signal(str)

    def __init__(self, hint_text: str = DROP_ZONE_HINT_DEFAULT) -> None:
        super().__init__()
        self._image_path: Path | None = None
        self._default_hint = hint_text
        self.setAcceptDrops(True)
        self.setObjectName("imageDropZone")
        self.setStyleSheet(
            "#imageDropZone { border: 3px dashed #8a8a8a; border-radius: 4px; padding: 12px; }"
        )

        layout = QVBoxLayout(self)
        self.preview = QLabel(self._default_hint)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 180)
        self.preview.setWordWrap(True)

        self.path_label = QLabel("")
        self.path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.path_label.setWordWrap(True)

        pick_button = QPushButton("Choose image")
        pick_button.setFixedHeight(38)
        pick_button.clicked.connect(self._open_file_dialog)

        layout.addWidget(self.preview)
        layout.addWidget(self.path_label)
        layout.addWidget(pick_button)

    @property
    def image_path(self) -> Path | None:
        return self._image_path

    def set_hint_text(self, hint: str) -> None:
        """Update the default hint text shown when no image is selected."""
        self._default_hint = hint
        if self._image_path is None:
            self.preview.setText(self._default_hint)

    def clear(self) -> None:
        self._image_path = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText(self._default_hint)
        self.path_label.clear()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_file_dialog()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                path = Path(urls[0].toLocalFile())
                if is_image_file(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        path = Path(urls[0].toLocalFile())
        if self.set_image(path):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _open_file_dialog(self) -> None:
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Choose image",
            "",
            "Images (*.png *.jpg *.jpeg *.webp)",
        )
        if picked:
            self.set_image(Path(picked))

    def set_image(self, path: Path) -> bool:
        if not path.exists():
            self.image_rejected.emit("The selected file was not found.")
            return False
        if not is_image_file(path):
            self.image_rejected.emit("This file format is not supported.")
            return False
        self._image_path = path
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.image_rejected.emit("The image could not be loaded.")
            self._image_path = None
            return False
        self.preview.setPixmap(
            pixmap.scaled(
                220,
                220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.preview.setText("")
        self.path_label.setText(path.name)
        self.image_selected.emit(str(path))
        return True


class MapCard(QFrame):
    edit_requested = Signal(str)

    _BADGE_STYLES: dict[str, str] = {
        "map": (
            "background-color: #1e3a5f; color: #93c5fd; "
            "border-radius: 0px; padding: 0px 5px; font-size: 10px; font-weight: 600;"
        ),
        "normal": (
            "background-color: #1e3a1e; color: #86efac; "
            "border-radius: 0px; padding: 0px 5px; font-size: 10px; font-weight: 600;"
        ),
    }

    # Feste Höhe für den Badge (unabhängig vom Karteninhalt)
    BADGE_HEIGHT = 20

    def __init__(self, root: Path, entry: TextureEntry, card_height: int = CARD_HEIGHT) -> None:
        super().__init__()
        self._card_height = card_height
        self._thumbnail_size = self._calculate_thumbnail_size()

        self.setObjectName("mapCard")
        self.setFixedHeight(self._card_height)
        self.setStyleSheet(
            "#mapCard { border: 1px solid #656565; border-radius: 0px; background-color: #232323; }"
        )

        layout = QHBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(8, 4, 8, 4)

        # Thumbnail
        thumbnail = self._create_thumbnail(root, entry)

        # Details (Name + Badge + optionale Beschreibung)
        details_widget = self._create_details_widget(entry)

        # Actions (Edit-Button)
        actions_widget = self._create_actions_widget(entry)

        layout.addWidget(thumbnail)
        layout.addWidget(details_widget, stretch=1)
        layout.addWidget(actions_widget)

    def _calculate_thumbnail_size(self) -> int:
        """Berechnet Thumbnail-Größe basierend auf Kartenhöhe."""
        return self._card_height - (CARD_THUMBNAIL_PADDING * 2)

    def _create_thumbnail(self, root: Path, entry: TextureEntry) -> QLabel:
        """Erstellt das Thumbnail-Widget."""
        thumbnail = QLabel()
        thumbnail.setFixedSize(self._thumbnail_size, self._thumbnail_size)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)

        image_path = root / entry.normal_image
        pixmap = QPixmap(str(image_path))

        if not pixmap.isNull():
            display_size = self._thumbnail_size - 4
            thumbnail.setPixmap(
                pixmap.scaled(
                    display_size, display_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            thumbnail.setText("N/A")

        return thumbnail

    def _create_details_widget(self, entry: TextureEntry) -> QWidget:
        """Erstellt den Details-Bereich mit Name, Badge und optionaler Beschreibung."""
        details_widget = QWidget()
        details_layout = QVBoxLayout(details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)

        # Name-Zeile mit Badge
        name_row = QHBoxLayout()
        name_row.setSpacing(8)

        name_label = QLabel(entry.name)
        name_label.setStyleSheet("font-size: 15px; font-weight: 600; color: #ffffff;")

        badge = self._create_badge(entry.entry_type)

        name_row.addWidget(name_label)
        name_row.addWidget(badge)
        name_row.addStretch()

        details_layout.addLayout(name_row)

        # Optionale Beschreibung
        if entry.picture_name.strip():
            picture_name_label = QLabel(f"{entry.picture_name}")
            picture_name_label.setStyleSheet("color: #d1d5db; font-size: 12px;")
            details_layout.addWidget(picture_name_label)

        # Vertikalen Stretch hinzufügen, damit der Inhalt oben bleibt
        details_layout.addStretch()

        return details_widget

    def _create_badge(self, entry_type: str) -> QLabel:
        """Erstellt einen Badge mit fester Höhe."""
        type_def = get_entry_type_definition(entry_type)
        badge_text = type_def.display_name.upper()
        badge = QLabel(badge_text)

        # Feste Höhe setzen
        badge.setFixedHeight(self.BADGE_HEIGHT)

        # Styling anwenden
        style = self._BADGE_STYLES.get(resolve_entry_type(entry_type), self._BADGE_STYLES["map"])
        badge.setStyleSheet(style)

        # Vertikale Zentrierung des Textes
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Verhindert, dass der Badge horizontal expandiert
        badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        return badge

    def _create_actions_widget(self, entry: TextureEntry) -> QWidget:
        """Erstellt den Actions-Bereich mit Edit-Button."""
        actions_widget = QWidget()
        actions_layout = QVBoxLayout(actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)

        actions_layout.addStretch()

        edit_button = QPushButton("Edit")
        edit_button.setFixedHeight(28)
        edit_button.setStyleSheet(
            "QPushButton { background-color: #4F4F4D; color: #E4E4E4; border: 1px solid #656565; "
            "border-radius: 0px; padding: 4px 10px; }"
            "QPushButton:hover { background-color: #5E5E5C; }"
        )

        edit_button.clicked.connect(lambda: self.edit_requested.emit(entry.slug))

        actions_layout.addWidget(edit_button)
        actions_layout.addStretch()

        return actions_widget



# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, repository: MapRepository) -> None:
        super().__init__()
        self.repository = repository
        self.entries, load_warnings = self.repository.load_entries()
        self.current_edit_slug: str | None = None

        self.setWindowTitle("Texturepack CIT Manager")
        self.resize(980, 680)

        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root_layout = QVBoxLayout(root_widget)

        # Header
        header_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search by name or picture description …")
        self.search_input.setFixedHeight(30)
        self.search_input.textChanged.connect(self._refresh_list)

        self.results_label = QLabel("")
        self.results_label.setStyleSheet("color: #5f5f5f;")

        self.add_button = QPushButton("+")
        self.add_button.setFixedSize(29, 29)
        self.add_button.setStyleSheet(
            "QPushButton { background-color: #4F4F4D; color: #E4E4E4; border: 1px solid #656565; "
            "border-radius: 0px; padding: 4px 4px; }"
            "QPushButton:hover { background-color: #5E5E5C; }"
            "QPushButton { font-size: 22px; font-weight: 500; }"
        )
        self.add_button.clicked.connect(self._show_create_page)

        self.back_button = QPushButton("Back")
        self.back_button.setFixedSize(90, 38)
        self.back_button.clicked.connect(self._show_list_page)
        self.back_button.hide()

        header_layout.addWidget(self.search_input)
        header_layout.addWidget(self.results_label)
        header_layout.addWidget(self.back_button)
        header_layout.addWidget(self.add_button)
        root_layout.addLayout(header_layout)

        # Pages
        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack)

        self.list_page = self._build_list_page()
        self.create_page = self._build_create_page()
        self.edit_page = self._build_edit_page()
        self.stack.addWidget(self.list_page)
        self.stack.addWidget(self.create_page)
        self.stack.addWidget(self.edit_page)
        self.stack.setCurrentWidget(self.list_page)

        # Status bar
        status_bar = self.statusBar()
        status_bar.setStyleSheet("QStatusBar { background-color: #232323; color: #f5f5f5; }")
        status_bar.setSizeGripEnabled(False)
        status_bar.setFixedHeight(20)

        self._refresh_list("")
        self._set_feedback(f"{len(self.entries)} entries loaded.", "info")

        if load_warnings:
            details = "\n".join(load_warnings)
            QMessageBox.warning(
                self,
                "Issues with loading",
                "Problems occurred during loading:\n\n" + details,
            )
            self._set_feedback("Loading complete. See the dialog for details.", "warning")

    # ------------------------------------------------------------------
    # Page builders
    # ------------------------------------------------------------------

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_container = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_container)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll_area.setWidget(self.scroll_container)

        layout.addWidget(self.scroll_area)
        return page

    def _build_create_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        title = QLabel("Create a new entry")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")

        self.type_selector = TypeSelector()
        self.type_selector.type_changed.connect(self._on_create_type_changed)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Itemname")

        self.picture_name_input = QLineEdit()
        self.picture_name_input.setPlaceholderText("Picture description (optional, e.g. redstone_block)")

        # Item ID — only visible for "normal" type
        self.item_id_label = QLabel("Item ID")
        self.item_id_label.setStyleSheet("color: #d1d5db; font-size: 12px;")
        self.item_id_input = QLineEdit()
        self.item_id_input.setPlaceholderText("Item ID or item_name (e.g. 152 or redstone_block)")
        self.item_id_label.hide()
        self.item_id_input.hide()

        # Image drop zone with default-type hint
        default_type_def = get_entry_type_definition(DEFAULT_ENTRY_TYPE)
        self.image_zone = ImageDropZone(hint_text=default_type_def.drop_zone_hint)
        self.image_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.image_zone.image_selected.connect(self._on_image_selected)
        self.image_zone.image_rejected.connect(self._on_image_rejected)

        self.create_button = QPushButton("Create")
        self.create_button.setFixedHeight(40)
        self.create_button.clicked.connect(self._create_entry)

        layout.addWidget(title)
        layout.addWidget(self.type_selector)
        layout.addWidget(self.name_input)
        layout.addWidget(self.picture_name_input)
        layout.addWidget(self.item_id_label)
        layout.addWidget(self.item_id_input)
        layout.addWidget(self.image_zone)
        layout.addWidget(self.create_button)
        layout.addStretch()
        return page

    def _build_edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        title = QLabel("Edit entry")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")

        # Read-only type indicator
        self.edit_type_label = QLabel()
        self.edit_type_label.setStyleSheet("color: #9ca3af; font-size: 13px;")

        self.edit_name_input = QLineEdit()
        self.edit_name_input.setPlaceholderText("Name")

        self.edit_picture_name_input = QLineEdit()
        self.edit_picture_name_input.setPlaceholderText("Picture description (optional)")

        # Item ID — only visible for "normal" type
        self.edit_item_id_label = QLabel("Item ID")
        self.edit_item_id_label.setStyleSheet("color: #d1d5db; font-size: 12px;")
        self.edit_item_id_input = QLineEdit()
        self.edit_item_id_input.setPlaceholderText(f"Item ID or item_name (e.g. 152 or redstone_block)")
        self.edit_item_id_label.hide()
        self.edit_item_id_input.hide()

        self.edit_image_zone = ImageDropZone()
        self.edit_image_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.edit_image_zone.image_selected.connect(self._on_image_selected)
        self.edit_image_zone.image_rejected.connect(self._on_image_rejected)

        buttons_layout = QHBoxLayout()
        self.save_edit_button = QPushButton("Save changes")
        self.save_edit_button.setFixedHeight(40)
        self.save_edit_button.clicked.connect(self._save_edited_entry)

        self.delete_edit_button = QPushButton("Delete")
        self.delete_edit_button.setFixedHeight(40)
        # self.delete_edit_button.setStyleSheet(
        #     "QPushButton { background-color: #7f1d1d; color: #ffffff; border: 1px solid #991b1b; "
        #     "border-radius: 6px; padding: 4px 10px; }"
        #     "QPushButton:hover { background-color: #991b1b; }"
        # )
        self.delete_edit_button.clicked.connect(self._delete_edited_entry)

        buttons_layout.addWidget(self.save_edit_button)
        buttons_layout.addWidget(self.delete_edit_button)

        layout.addWidget(title)
        layout.addWidget(self.edit_type_label)
        layout.addWidget(self.edit_name_input)
        layout.addWidget(self.edit_picture_name_input)
        layout.addWidget(self.edit_item_id_label)
        layout.addWidget(self.edit_item_id_input)
        layout.addWidget(self.edit_image_zone)
        layout.addLayout(buttons_layout)
        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_create_page(self, show_feedback: bool = True) -> None:
        self.type_selector.reset()
        self._update_create_page_for_type(DEFAULT_ENTRY_TYPE)
        self.stack.setCurrentWidget(self.create_page)
        self.search_input.hide()
        self.results_label.hide()
        self.add_button.hide()
        self.back_button.show()
        if show_feedback:
            self._set_feedback("Creation page opened.", "info")

    def _show_edit_page(self, show_feedback: bool = True) -> None:
        self.stack.setCurrentWidget(self.edit_page)
        self.search_input.hide()
        self.results_label.hide()
        self.add_button.hide()
        self.back_button.show()
        if show_feedback:
            self._set_feedback("Edit page opened.", "info")

    def _show_list_page(self, show_feedback: bool = True) -> None:
        self.stack.setCurrentWidget(self.list_page)
        self.search_input.show()
        self.results_label.show()
        self.add_button.show()
        self.back_button.hide()
        self.current_edit_slug = None
        self._refresh_list(self.search_input.text())
        if show_feedback:
            self._set_feedback("List view open.", "info")

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def _clear_list(self) -> None:
        while self.scroll_layout.count():
            item = self.scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_list(self, query: str) -> None:
        if self.stack.currentWidget() is not self.list_page:
            return
        term = query.strip().casefold()
        self._clear_list()

        filtered = [
            entry for entry in self.entries
            if term in " ".join([entry.name, entry.picture_name]).casefold()
        ]
        self.results_label.setText(f"{len(filtered)} Matches")

        if not filtered:
            empty = QLabel("No entries found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #6a6a6a; padding: 24px;")
            self.scroll_layout.addWidget(empty)
            return

        for entry in filtered:
            card = MapCard(self.repository.root, entry)
            card.edit_requested.connect(self._open_edit_page)
            self.scroll_layout.addWidget(card)

        self.scroll_layout.addStretch()

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def _set_feedback(self, message: str, level: FEEDBACK_LEVEL = "info") -> None:
        _ = level
        self.statusBar().showMessage(message, 7000)

    def _on_image_selected(self, image_path: str) -> None:
        path = Path(image_path)
        try:
            width, height = ImageProcessor.get_dimensions(path)
        except OSError as error:
            self._on_image_rejected(f"Image could not be read: {error}")
            return

        # Determine current entry type context
        current_type = self._get_current_entry_type_context()

        type_def = get_entry_type_definition(current_type)

        if not type_def.preserve_original_size:
            if (width, height) == MAP_TARGET_SIZE:
                self._set_feedback(f"Image '{path.name}' loaded ({width}×{height}).", "success")
            else:
                self._set_feedback(
                    f"Image '{path.name}' loaded ({width}×{height}) — will be resized to 300×300.",
                    "warning",
                )
        else:
            self._set_feedback(
                f"Image '{path.name}' loaded ({width}×{height}) — original size will be preserved.",
                "success",
            )

    def _on_image_rejected(self, message: str) -> None:
        self._set_feedback(message, "error")
        QMessageBox.warning(self, "Image error", message)

    def _get_current_entry_type_context(self) -> str:
        """Determine the entry type based on current page context."""
        if self.stack.currentWidget() is self.create_page:
            return self.type_selector.selected_type
        elif self.stack.currentWidget() is self.edit_page and self.current_edit_slug:
            entry = next((e for e in self.entries if e.slug == self.current_edit_slug), None)
            if entry:
                return entry.entry_type
        return DEFAULT_ENTRY_TYPE

    def _on_create_type_changed(self, entry_type: str) -> None:
        self._update_create_page_for_type(entry_type)

    def _update_create_page_for_type(self, entry_type: str) -> None:
        """Update the create page UI based on selected entry type."""
        type_def = get_entry_type_definition(entry_type)
        show_item_id = type_def.allows_custom_item_id
        self.item_id_label.setVisible(show_item_id)
        self.item_id_input.setVisible(show_item_id)

        self.image_zone.set_hint_text(type_def.drop_zone_hint)

    def _confirm_missing_picture_name(self) -> bool:
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Picture description missing")
        confirm.setText("You have not entered a picture description.")
        confirm.setInformativeText(
            "A picture description helps avoid accidentally reusing the same image idea for multiple entries.\n\n"
            "Do you want to continue without a picture description?"
        )
        continue_button = confirm.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
        confirm.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        confirm.setDefaultButton(continue_button)
        confirm.exec()
        return confirm.clickedButton() is continue_button

    # ------------------------------------------------------------------
    # Edit page
    # ------------------------------------------------------------------

    def _open_edit_page(self, slug: str) -> None:
        entry = next((e for e in self.entries if e.slug == slug), None)
        if entry is None:
            QMessageBox.warning(self, "Entry missing", "The selected entry could not be found.")
            return

        self.current_edit_slug = slug
        self.edit_name_input.setText(entry.name)
        self.edit_picture_name_input.setText(entry.picture_name)

        type_def = get_entry_type_definition(entry.entry_type)
        type_display = type_def.display_name
        self.edit_type_label.setText(f"Type: {type_display}  (cannot be changed after creation)")

        self.edit_item_id_label.setVisible(type_def.allows_custom_item_id)
        self.edit_item_id_input.setVisible(type_def.allows_custom_item_id)
        if type_def.allows_custom_item_id:
            self.edit_item_id_input.setText(entry.item_id)
        else:
            self.edit_item_id_input.clear()

        self.edit_image_zone.set_hint_text(type_def.drop_zone_hint)

        self.edit_image_zone.clear()
        current_image_path = self.repository.root / entry.normal_image
        if current_image_path.exists():
            self.edit_image_zone.set_image(current_image_path)
        else:
            self._set_feedback(
                "Current image is missing. Please choose a new one before saving.",
                "warning",
            )

        self._show_edit_page(show_feedback=False)

    def _save_edited_entry(self) -> None:
        if self.current_edit_slug is None:
            QMessageBox.warning(self, "No entry selected", "Please select an entry to edit.")
            return

        current_entry = next((e for e in self.entries if e.slug == self.current_edit_slug), None)
        if current_entry is None:
            QMessageBox.warning(self, "Entry missing", "The selected entry could not be found.")
            return

        name = self.edit_name_input.text().strip()
        picture_name = self.edit_picture_name_input.text().strip()
        source_image_path = self.edit_image_zone.image_path
        type_def = get_entry_type_definition(current_entry.entry_type)
        item_id = (
            self.edit_item_id_input.text().strip()
            if type_def.allows_custom_item_id
            else None
        )

        if not name:
            QMessageBox.warning(self, "Missing name", "Please enter a name.")
            return
        if not picture_name and not self._confirm_missing_picture_name():
            self._set_feedback("Edit cancelled.", "info")
            return

        original_slug = self.current_edit_slug
        self.save_edit_button.setEnabled(False)
        self.delete_edit_button.setEnabled(False)

        try:
            updated = self.repository.update_entry(
                entries=self.entries,
                original_slug=original_slug,
                name=name,
                picture_name=picture_name,
                source_image_path=source_image_path,
                item_id=item_id,
            )

            for i, e in enumerate(self.entries):
                if e.slug == original_slug:
                    self.entries[i] = updated
                    break
            self.entries.sort(key=lambda e: e.name.casefold())

            try:
                self.repository.save_entries(self.entries)
            except OSError as error:
                self._set_feedback(
                    f"Entry updated, but entrys.json could not be saved: {error}", "warning"
                )
                QMessageBox.warning(
                    self,
                    "Save warning",
                    f"Entry updated but entrys.json could not be saved.\nError: {error}",
                )
            else:
                self._set_feedback(f"Entry '{updated.name}' updated.", "success")
                QMessageBox.information(
                    self, "Updated", f"'{updated.name}' has been updated successfully."
                )

            self.search_input.clear()
            self._show_list_page(show_feedback=False)

        except ValueError as error:
            self._set_feedback(str(error), "error")
            QMessageBox.warning(self, "Update failed", str(error))
        except OSError as error:
            self._set_feedback(str(error), "error")
            QMessageBox.critical(self, "File error", str(error))
        except Exception as error:
            self._set_feedback(f"Unexpected error: {error}", "error")
            QMessageBox.critical(
                self, "Unexpected error",
                f"An unexpected error occurred during update.\nError: {error}",
            )
        finally:
            self.save_edit_button.setEnabled(True)
            self.delete_edit_button.setEnabled(True)

    def _delete_edited_entry(self) -> None:
        if self.current_edit_slug is None:
            QMessageBox.warning(self, "No entry selected", "Please select an entry to delete.")
            return

        entry = next((e for e in self.entries if e.slug == self.current_edit_slug), None)
        if entry is None:
            QMessageBox.warning(self, "Entry missing", "The selected entry could not be found.")
            return

        confirm = QMessageBox.question(
            self,
            "Delete entry",
            f"Do you really want to delete '{entry.name}'?\n\nThis removes its .png and .properties files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._set_feedback("Deletion cancelled.", "info")
            return

        self.save_edit_button.setEnabled(False)
        self.delete_edit_button.setEnabled(False)

        try:
            self.repository.delete_entry(entry.slug, entry.entry_type)
            self.entries = [e for e in self.entries if e.slug != entry.slug]

            try:
                self.repository.save_entries(self.entries)
            except OSError as error:
                self._set_feedback(
                    f"Entry deleted, but entrys.json could not be saved: {error}", "warning"
                )
                QMessageBox.warning(
                    self,
                    "Save warning",
                    f"Entry deleted but entrys.json could not be saved.\nError: {error}",
                )
            else:
                self._set_feedback(f"Entry '{entry.name}' deleted.", "success")
                QMessageBox.information(
                    self, "Deleted", f"'{entry.name}' has been deleted successfully."
                )

            self.search_input.clear()
            self._show_list_page(show_feedback=False)

        except OSError as error:
            self._set_feedback(str(error), "error")
            QMessageBox.critical(self, "Delete failed", str(error))
        except Exception as error:
            self._set_feedback(f"Unexpected error: {error}", "error")
            QMessageBox.critical(
                self, "Unexpected error",
                f"An unexpected error occurred during deletion.\nError: {error}",
            )
        finally:
            self.save_edit_button.setEnabled(True)
            self.delete_edit_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Create page
    # ------------------------------------------------------------------

    def _create_entry(self) -> None:
        name = self.name_input.text().strip()
        picture_name = self.picture_name_input.text().strip()
        image_path = self.image_zone.image_path
        entry_type = resolve_entry_type(self.type_selector.selected_type)
        type_def = get_entry_type_definition(entry_type)
        item_id = (
            self.item_id_input.text().strip()
            if type_def.allows_custom_item_id
            else ITEM_ID
        )
        if type_def.allows_custom_item_id and not item_id:
            QMessageBox.warning(
                self,
                "Missing Item ID",
                "Please enter an Item ID for this entry type.",
            )
            return

        if not name:
            QMessageBox.warning(self, "Missing name", "Please enter a name.")
            return
        if image_path is None:
            QMessageBox.warning(
                self, "Missing image",
                "Please select an image (you can also use drag-and-drop).",
            )
            return
        if not picture_name and not self._confirm_missing_picture_name():
            self._set_feedback("Creation cancelled.", "info")
            return

        self.create_button.setEnabled(False)
        try:
            new_entry = self.repository.create_entry(
                entries=self.entries,
                name=name,
                picture_name=picture_name,
                source_image_path=image_path,
                entry_type=entry_type,
                item_id=item_id,
            )
            self.entries.append(new_entry)
            self.entries.sort(key=lambda e: e.name.casefold())

            try:
                self.repository.save_entries(self.entries)
            except OSError as error:
                self._set_feedback(
                    f"Entry created, but entrys.json could not be saved: {error}", "warning"
                )
                QMessageBox.warning(
                    self,
                    "Save warning",
                    f"Entry created but entrys.json could not be saved.\nError: {error}",
                )
            else:
                self._set_feedback(f"Entry '{new_entry.name}' created.", "success")
                QMessageBox.information(
                    self, "Created", f"'{new_entry.name}' has been successfully added."
                )

            # Reset form
            self.name_input.clear()
            self.picture_name_input.clear()
            self.item_id_input.setText(ITEM_ID)
            self.image_zone.clear()
            self.type_selector.reset()
            self.search_input.clear()
            self._show_list_page(show_feedback=False)

        except ValueError as error:
            self._set_feedback(str(error), "error")
            QMessageBox.warning(self, "Creation failed", str(error))
        except OSError as error:
            self._set_feedback(str(error), "error")
            QMessageBox.critical(self, "File error", str(error))
        except Exception as error:
            self._set_feedback(f"Unexpected error: {error}", "error")
            QMessageBox.critical(
                self, "Unexpected error",
                f"An unexpected error occurred during creation.\nError: {error}",
            )
        finally:
            self.create_button.setEnabled(True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    if not DEPENDENCIES_OK:
        print_missing_dependencies()
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Texturepack CIT Manager")
    try:
        window = MainWindow(MapRepository(Path(__file__).resolve().parent))
    except Exception as error:
        QMessageBox.critical(
            None,
            "Start error",
            f"The application could not be started.\nError: {error}",
        )
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
