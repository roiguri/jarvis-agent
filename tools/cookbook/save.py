"""save_recipe: a recipe the owner agreed on, saved to the family cookbook after a
final confirmation.

The recipe arrives as fields, however Jarvis got them; photos and step media as
stored files' ids. Nothing here reads a link or calls another tool. The recipe
itself is checked by the cookbook, not here: its field-by-field refusal comes back
as the outcome, and Jarvis fixes it and asks again. Files are prepared within the
API's limits, and the request is sent only when the owner confirms.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from files import model_input, store
from tools.cookbook._client import CookbookError, categories, request
from tools.registry import tool_register

PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}
PHOTO_MAX_BYTES = 5 * 1024 * 1024
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/mov"}
STEP_MEDIA_MAX_BYTES = 50 * 1024 * 1024
REQUEST_MAX_BYTES = 30 * 1024 * 1024 - 256 * 1024  # room for the JSON and the framing
# The fields a source often leaves out: how the estimate note names each, and its
# gender (for the Hebrew verb: הוא / היא; several fields take הם).
ESTIMABLE = {"prepTime": ("זמן ההכנה", "m"), "waitTime": ("זמן ההמתנה", "m"),
             "servings": ("כמות המנות", "f"), "servingsUnit": ("יחידת המנה", "f"),
             "difficulty": ("רמת הקושי", "f")}


class Ingredient(BaseModel):
    item: str = Field(description="the ingredient")
    amount: str | None = Field(None, description="a number or a fraction: 2, 0.5, 1/2, 1½. For a "
                               "range (2–3), choose one number. None when there is no amount")
    unit: str | None = None


class IngredientSection(BaseModel):
    title: str
    items: list[Ingredient]


class Stage(BaseModel):
    title: str
    instructions: list[str]


class Recipe(BaseModel):
    """A complete recipe, in Hebrew, in the cookbook's own shape."""
    name: str
    category: str = Field(description="a category id (find_recipes shows the ids)")
    difficulty: str = Field(description="קלה, בינונית or קשה")
    prepTime: int = Field(description="whole minutes")
    servings: int
    waitTime: int | None = Field(None, description="whole minutes")
    servingsUnit: str | None = Field(None, description="e.g. עוגיות")
    ingredients: list[Ingredient] | None = Field(None, description="one list; or ingredientSections")
    ingredientSections: list[IngredientSection] | None = Field(
        None, description="two or more titled lists, instead of ingredients")
    instructions: list[str] | None = Field(None, description="the steps; or stages")
    stages: list[Stage] | None = Field(None, description="two or more titled step lists")
    description: str | None = None
    attribution: str | None = Field(None, description="who the recipe is from, and where")
    mainIngredient: str | None = None
    tags: list[str] | None = None
    comments: list[str] | None = None


class StepMedia(BaseModel):
    file_id: str = Field(description="a stored photo or video")
    caption: str | None = None


@tool_register(namespace="cookbook")
@tool
def save_recipe(recipe: Recipe, source: str = "", estimated: list[str] | None = None,
                stage: str = "to-try", submit: bool = False,
                photo_file_ids: list[str] | None = None,
                step_media: list[StepMedia] | None = None) -> str:
    """Save a recipe to the family cookbook, after the owner agreed on it in chat. The
    owner gets a final Confirm / Cancel; nothing is saved before that. Saved recipes
    are private. Saving the same source again changes nothing.

    Args:
        recipe: the whole recipe, in Hebrew.
        source: where it came from: the link, or the file ids it was read from.
            Keeps the same recipe from being saved twice.
        estimated: the fields you estimated because the source didn't say (of
            prepTime, waitTime, servings, servingsUnit, difficulty); the recipe
            gets a note naming them.
        stage: "to-try", or "tested" if the owner has made it.
        submit: True only if the owner asked to send it for approval.
        photo_file_ids: stored photos, the main one first (at most 5).
        step_media: stored photos or videos shown with the steps, in order, each
            with an optional caption.
    """
    from gateway.factory import get_confirmation

    try:
        labels = categories()
    except CookbookError as e:
        return f"Error: {e}"
    unknown = [f for f in estimated or [] if f not in ESTIMABLE]
    if unknown:
        return (f"Not saved: estimated can only name {', '.join(ESTIMABLE)} "
                f"(not {', '.join(unknown)}).")
    files, notes, problems = _files(photo_file_ids or [], step_media or [])
    if problems:
        return "Not saved: " + "; ".join(problems)

    content = _content(recipe, estimated or [])
    payload = {"id": recipe_id(source, content), "stage": stage, "submit": submit,
               "recipe": content}
    description = _summary(content, labels, stage, submit, estimated or [], files, notes)

    async def _run() -> str:
        return await asyncio.to_thread(_send, payload, files, notes)

    try:
        return get_confirmation().request_confirmation_sync(
            description=description, action_fn=_run,
            result_ok_text="Done.", result_cancel_text="Not saved.")
    except Exception as e:
        return f"Error requesting confirmation: {e}"


def recipe_id(source: str, content: dict) -> str:
    """An id from the source and the recipe's name (the whole recipe when there's no
    source): the same reel always gets the same id, so a second save is the API's
    'already yours', while a second recipe from the same source gets its own."""
    canonical = _canonical(source)
    basis = (f"{canonical}\n{content.get('name', '').strip()}" if canonical
             else json.dumps(content, ensure_ascii=False, sort_keys=True))
    return "jarvis-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# Query parameters that only track who shared a link, never which page it is.
_TRACKING = {"srtk", "igsh", "igshid", "si", "fbclid", "gclid", "ref", "ref_src", "is_from_webapp",
             "sender_device", "_r", "_t", "share_id", "share_app_id", "t", "start", "feature",
             "pp"}


def _canonical(source: str) -> str:
    """A link without its sharing noise (tracking parameters, fragment, case of the
    host, a trailing slash), so the same post shared twice gives the same id."""
    source = source.strip()
    parts = urlsplit(source)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return source
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING and not k.lower().startswith("utm_")]
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parts.path.rstrip("/")
    if host == "youtu.be" and path:  # the short form of a watch link
        host, query, path = "youtube.com", [*query, ("v", path.lstrip("/"))], "/watch"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def _content(recipe: Recipe, estimated: list[str]) -> dict:
    """The API's recipe body: Nones dropped, amounts as numbers where they are
    plain numbers, and the note naming the estimates."""
    content = recipe.model_dump(exclude_none=True)
    for ingredient in _all_ingredients(content):
        if "amount" in ingredient:
            ingredient["amount"] = _amount(ingredient["amount"])
    if estimated:
        content["comments"] = [*content.get("comments", []), estimate_note(estimated)]
    return content


def estimate_note(estimated: list[str]) -> str:
    """e.g. "זמן ההכנה וכמות המנות הם הערכה בלבד." — about the recipe, not who
    estimated it."""
    names = [ESTIMABLE[f][0] for f in dict.fromkeys(estimated)]
    if len(names) == 1:
        verb = "הוא" if ESTIMABLE[estimated[0]][1] == "m" else "היא"
        return f"{names[0]} {verb} הערכה בלבד."
    listed = ", ".join(names[:-1]) + " ו" + names[-1]
    return f"{listed} הם הערכה בלבד."


def _all_ingredients(content: dict) -> list[dict]:
    found = list(content.get("ingredients", []))
    for section in content.get("ingredientSections", []):
        found += section.get("items", [])
    return found


def _amount(value):
    """A plain number as a number; anything else as given (the cookbook checks it)."""
    text = str(value).strip()
    if re.fullmatch(r"\d*\.?\d+", text):
        number = float(text)
        return int(number) if number.is_integer() else number
    return text


def _files(photo_ids: list[str], steps: list[StepMedia]):
    """(multipart file parts with their JSON entries, notes, problems)."""
    parts, notes, problems = {"images": [], "mediaInstructions": [], "parts": []}, [], []
    budget = REQUEST_MAX_BYTES
    for n, file_id in enumerate(photo_ids, 1):
        rec = store.get(file_id)
        if rec is None or rec.kind != "image":
            problems.append(f"{file_id} isn't a stored photo")
            continue
        try:
            data, mime = _photo(store.read_bytes(rec.id), model_input.normalize_mime(rec.mime_type))
        except store.FileUnavailable:
            problems.append(f"{rec.marker()} is no longer stored")
            continue
        except (OSError, ValueError):
            problems.append(f"{rec.marker()} can't be converted to a JPEG, PNG or WebP photo")
            continue
        budget -= len(data)
        parts["images"].append({"part": f"photo{n}"} | ({"isPrimary": True} if n == 1 else {}))
        parts["parts"].append((f"photo{n}", (f"photo{n}.{mime.split('/')[1]}", data, mime)))
    for n, step in enumerate(steps, 1):
        try:
            data, mime = _step_file(step.file_id)
        except ValueError as e:
            problems.append(str(e))
            continue
        if len(data) > budget:
            notes.append(f"step media {n} ({len(data) / 2**20:.0f} MB) is left out: with the "
                         "other files it would pass the cookbook's 30 MB limit")
            continue
        budget -= len(data)
        caption = (step.caption or "").strip()
        parts["mediaInstructions"].append({"part": f"step{n}"} | ({"caption": caption} if caption else {}))
        ext = {"video/webm": "webm", "video/quicktime": "mov", "video/mp4": "mp4"}.get(
            mime, mime.split("/")[1])
        parts["parts"].append((f"step{n}", (f"step{n}.{ext}", data, mime)))
    return parts, notes, problems


def _step_file(file_id: str) -> tuple[bytes, str]:
    """A step's bytes and type: a video as it is, a photo as the API takes it.
    Raises ValueError, phrased for the model, when the file can't be a step."""
    rec = store.get(file_id)
    mime = model_input.normalize_mime(rec.mime_type) if rec else ""
    if rec is None or not (rec.kind == "image" or (rec.kind == "video" and mime in VIDEO_MIMES)):
        raise ValueError(f"{file_id} isn't a stored photo or MP4, WebM or QuickTime video")
    try:
        data = store.read_bytes(rec.id)
    except store.FileUnavailable:
        raise ValueError(f"{rec.marker()} is no longer stored") from None
    if rec.kind == "video":
        if len(data) > STEP_MEDIA_MAX_BYTES:
            raise ValueError(f"{rec.marker()} is over the cookbook's 50 MB limit for step media")
        return data, "video/quicktime" if mime == "video/mov" else mime
    if mime == "image/gif":
        return data, mime
    try:
        return _photo(data, mime)
    except (OSError, ValueError):
        raise ValueError(f"{rec.marker()} can't be converted to a JPEG, PNG or WebP photo") from None


def _photo(data: bytes, mime: str) -> tuple[bytes, str]:
    """A photo the API takes as it is, or re-encoded as a JPEG under 5 MB."""
    if mime in PHOTO_MIMES and len(data) <= PHOTO_MAX_BYTES:
        return data, mime
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        edge = min(max(im.size), 4096)
        while True:
            copy = im.copy()
            copy.thumbnail((edge, edge))
            out = io.BytesIO()
            copy.save(out, "JPEG", quality=85)
            if out.tell() <= PHOTO_MAX_BYTES or edge <= 256:
                return out.getvalue(), "image/jpeg"
            edge = int(edge * 0.75)


def _summary(content: dict, labels: dict[str, str], stage: str, submit: bool,
             estimated: list[str], files: dict, notes: list[str]) -> str:
    lines = [f"save a recipe to the cookbook: {content['name']}",
             f"{labels.get(content['category'], content['category'])} · {content['difficulty']} · "
             f"{content['prepTime']} min · {content['servings']} servings · "
             f"{'to try' if stage == 'to-try' else 'tried'}"
             + (" · and send it for approval" if submit else "")]
    if estimated:
        lines.append("Estimated by Jarvis: " + ", ".join(estimated))
    n_photos, n_steps = len(files["images"]), len(files["mediaInstructions"])
    if n_photos or n_steps:
        lines.append(" and ".join(t for t in (n_photos and f"{n_photos} photo(s)",
                                             n_steps and f"{n_steps} step media file(s)") if t))
    lines += [f"Note: {n}" for n in notes]
    return "\n".join(lines)


def _send(payload: dict, files: dict, notes: list[str]) -> str:
    """The request, once the owner confirmed: its outcome, phrased for the model.
    Raises CookbookError when nothing was saved."""
    name = payload["recipe"]["name"]
    try:
        if files["parts"]:
            body = {**payload, "images": files["images"],
                    "mediaInstructions": files["mediaInstructions"]}
            body = {k: v for k, v in body.items() if v != []}
            status, answer = request("POST", "/recipes", files=[
                ("request", (None, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                             "application/json")), *files["parts"]])
        else:
            status, answer = request("POST", "/recipes", json=payload)
    except CookbookError as e:
        # Raised, so the owner's prompt shows the save failed rather than ✅.
        raise CookbookError(f"Not saved: {e}") from None
    where = f"{answer.get('path')} (id {answer.get('id')})"
    if not answer.get("created", status == 201):
        return f"“{name}” is already in the cookbook, {where}; nothing was changed."
    extras = []
    if answer.get("images"):
        extras.append(f"{answer['images']} photo(s)")
    if answer.get("mediaInstructions"):
        extras.append(f"{answer['mediaInstructions']} step media file(s)")
    if answer.get("submitted"):
        extras.append("sent for approval")
    tail = f" ({', '.join(extras)})" if extras else ""
    note = "".join(f" Note: {n}." for n in notes)
    return f"Saved “{name}” to the cookbook, {where}{tail}.{note}"
