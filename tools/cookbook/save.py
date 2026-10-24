"""save_recipe: a recipe the owner agreed on, saved to the family cookbook after a
final confirmation.

The recipe arrives as fields, however Jarvis got them; photos and a step video as
stored files' ids. Nothing here reads a link or calls another tool. The body is
checked here first (most mistakes never reach the API), files are prepared within
the API's limits, and the request is sent only when the owner confirms.
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

DIFFICULTIES = ("קלה", "בינונית", "קשה")
STAGES = ("to-try", "tested")
PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTOS_MAX = 5
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/mov"}
REQUEST_MAX_BYTES = 30 * 1024 * 1024 - 256 * 1024  # room for the JSON and the framing
# The fields a source often leaves out: how the estimate note names each, and its
# gender (for the Hebrew verb: הוא / היא; several fields take הם).
ESTIMABLE = {"prepTime": ("זמן ההכנה", "m"), "waitTime": ("זמן ההמתנה", "m"),
             "servings": ("כמות המנות", "f"), "servingsUnit": ("יחידת המנה", "f"),
             "difficulty": ("רמת הקושי", "f")}


class Ingredient(BaseModel):
    item: str = Field(description="the ingredient; a range or 'to taste' goes here too")
    amount: str | None = Field(None, description="a number or a fraction: 2, 0.5, 1/2, 1½")
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


@tool_register(namespace="cookbook")
@tool
def save_recipe(recipe: Recipe, source: str = "", estimated: list[str] | None = None,
                stage: str = "to-try", submit: bool = False,
                photo_file_ids: list[str] | None = None, video_file_id: str = "",
                video_caption: str = "") -> str:
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
        video_file_id: one stored video to attach as a step.
        video_caption: optional caption for that video.
    """
    from gateway.factory import get_confirmation

    try:
        labels = categories()
    except CookbookError as e:
        return f"Error: {e}"
    problems = _check(recipe, labels, estimated or [], stage)
    if problems:
        return "Not saved — fix these first: " + "; ".join(problems)
    files, notes, problems = _files(photo_file_ids or [], video_file_id, video_caption)
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


def _check(recipe: Recipe, labels: dict[str, str], estimated: list[str], stage: str) -> list[str]:
    problems = []
    if not recipe.name.strip():
        problems.append("name is empty")
    if recipe.category not in labels:
        problems.append(f"category {recipe.category!r} isn't one of: "
                        + ", ".join(f"{k} ({v})" for k, v in labels.items()))
    if recipe.difficulty not in DIFFICULTIES:
        problems.append(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    if recipe.prepTime < 1:
        problems.append("prepTime must be at least 1 minute")
    if recipe.servings < 1:
        problems.append("servings must be at least 1")
    if bool(recipe.ingredients) == bool(recipe.ingredientSections):
        problems.append("give ingredients or ingredientSections, exactly one")
    elif recipe.ingredientSections and len(recipe.ingredientSections) < 2:
        problems.append("ingredientSections needs two or more sections (else use ingredients)")
    if bool(recipe.instructions) == bool(recipe.stages):
        problems.append("give instructions or stages, exactly one")
    elif recipe.stages and len(recipe.stages) < 2:
        problems.append("stages needs two or more stages (else use instructions)")
    for ingredient in _all_ingredients(recipe.model_dump(exclude_none=True)):
        if "amount" in ingredient and _amount(ingredient["amount"]) is None:
            problems.append(f"amount {ingredient['amount']!r} ({ingredient['item']}) must be a "
                            "positive number or a fraction such as 1/2; a range or 'to taste' "
                            "goes in item")
    unknown = [f for f in estimated if f not in ESTIMABLE]
    if unknown:
        problems.append(f"estimated can only name {', '.join(ESTIMABLE)}; not {', '.join(unknown)}")
    if stage not in STAGES:
        problems.append("stage must be to-try or tested")
    return problems


def _content(recipe: Recipe, estimated: list[str]) -> dict:
    """The API's recipe body: Nones dropped, amounts as numbers where they are
    plain numbers, and the note naming the estimates."""
    content = recipe.model_dump(exclude_none=True)
    for ingredient in _all_ingredients(content):
        if "amount" in ingredient:
            ingredient["amount"] = _amount(ingredient["amount"])  # checked in _check
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


_FRACTION = re.compile(r"(\d+\s*)?([½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\d+/[1-9]\d*)")


def _amount(value):
    """A plain number as a number; a fraction (1/2, 1½, 1 1/2) as given; None for
    anything the cookbook won't take (zero, a range, words)."""
    text = str(value).strip().replace(",", ".")
    if re.fullmatch(r"\d*\.?\d+", text):
        number = float(text)
        if number <= 0:
            return None
        return int(number) if number.is_integer() else number
    return text if _FRACTION.fullmatch(text) else None


def _files(photo_ids: list[str], video_id: str, caption: str):
    """(multipart file parts with their JSON entries, notes, problems)."""
    parts, notes, problems = {"images": [], "mediaInstructions": [], "parts": []}, [], []
    if len(photo_ids) > PHOTOS_MAX:
        problems.append(f"at most {PHOTOS_MAX} photos")
        return parts, notes, problems
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
    if video_id:
        rec = store.get(video_id)
        mime = model_input.normalize_mime(rec.mime_type) if rec else ""
        if rec is None or rec.kind != "video" or mime not in VIDEO_MIMES:
            problems.append(f"{video_id} isn't a stored MP4, WebM or QuickTime video")
        elif rec.state not in ("available", "trash"):
            problems.append(f"{rec.marker()} is no longer stored")
        elif rec.size > budget:
            notes.append(f"the video ({rec.size / 2**20:.0f} MB) is left out: with the photos "
                         "it would pass the cookbook's 30 MB limit")
        else:
            entry = {"part": "step1"} | ({"caption": caption} if caption.strip() else {})
            parts["mediaInstructions"].append(entry)
            ext = {"video/webm": "webm", "video/quicktime": "mov", "video/mov": "mov"}.get(mime, "mp4")
            mime = "video/quicktime" if mime == "video/mov" else mime
            parts["parts"].append(("step1", (f"step1.{ext}", store.read_bytes(rec.id), mime)))
    return parts, notes, problems


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
    n_photos, n_video = len(files["images"]), len(files["mediaInstructions"])
    if n_photos or n_video:
        lines.append(f"{n_photos} photo(s)" + (" and a step video" if n_video else ""))
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
        extras.append("a step video")
    if answer.get("submitted"):
        extras.append("sent for approval")
    tail = f" ({', '.join(extras)})" if extras else ""
    note = "".join(f" Note: {n}." for n in notes)
    return f"Saved “{name}” to the cookbook, {where}{tail}.{note}"
