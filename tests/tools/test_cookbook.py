"""The cookbook skill against a fake Recipe API: settings, reads, rendering, errors.
No network."""
import httpx
import pytest

from tools.cookbook import _client, find_recipes, get_recipe

TOKEN = "ckb_test-secret"
CATEGORIES = {"categories": [{"id": "desserts", "label": "קינוחים"},
                             {"id": "breakfast-brunch", "label": "ארוחות בוקר"}]}
MINE = {"recipes": [
    {"id": "r1", "name": "עוגת שוקולד", "category": "desserts", "stage": "tested",
     "visibility": "private", "isMine": True, "path": "/recipe/r1"},
    {"id": "r2", "name": "שקשוקה", "category": "breakfast-brunch", "stage": "to-try",
     "visibility": "private", "isMine": True, "path": "/recipe/r2"},
]}
FULL = {"id": "r2", "path": "/recipe/r2", "visibility": "private", "stage": "to-try",
        "isMine": True, "recipe": {
            "name": "שקשוקה", "category": "breakfast-brunch", "difficulty": "קלה",
            "prepTime": 25, "servings": 4, "attribution": "https://example.com/shakshuka",
            "ingredients": [{"item": "ביצים", "amount": 6}, {"item": "רסק", "amount": 0.5,
                                                              "unit": "כוס"}],
            "instructions": ["לטגן בצל", "לשבור ביצים"], "comments": ["חריף"]},
        "images": [{"id": "img-1", "isPrimary": True, "url": "https://img/1.jpg"}],
        "mediaInstructions": []}


@pytest.fixture
def api(monkeypatch):
    """A fake API: `routes` maps (method, path) to (status, body); `seen` the requests."""
    state = {"routes": {("GET", "/v1/categories"): (200, CATEGORIES),
                        ("GET", "/v1/recipes/mine"): (200, MINE),
                        ("GET", "/v1/recipes/r2"): (200, FULL)},
             "seen": []}

    def handler(request):
        state["seen"].append(request)
        path = request.url.path.removeprefix("/recipeApi")
        status, body = state["routes"].get((request.method, path),
                                           (404, {"error": {"code": "not-found", "message": "x"}}))
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    monkeypatch.setenv("COOKBOOK_API_URL", "https://cookbook.test/recipeApi")
    monkeypatch.setenv("COOKBOOK_API_TOKEN", TOKEN)
    monkeypatch.setattr(_client, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(_client, "_categories", None)
    return state


def test_every_call_carries_the_token_and_nothing_shows_it(api):
    out = find_recipes.invoke({})
    assert all(r.headers["Authorization"] == f"Bearer {TOKEN}" for r in api["seen"])
    assert TOKEN not in out
    api["routes"][("GET", "/v1/recipes/mine")] = (401, {"error": {"code": "unauthorized",
                                                                  "message": "bad token"}})
    assert TOKEN not in find_recipes.invoke({})


def test_own_recipes_are_listed_with_their_category_label_and_id(api):
    out = find_recipes.invoke({})
    assert "• עוגת שוקולד — קינוחים (desserts) · tried · private · id r1" in out
    assert "• שקשוקה — ארוחות בוקר (breakfast-brunch) · to try · private · id r2" in out


def test_own_recipes_are_filtered_by_name_and_category_here(api):
    assert "r2" in find_recipes.invoke({"query": "שקשו"}) and "r1" not in find_recipes.invoke(
        {"query": "שקשו"})
    assert "r1" not in find_recipes.invoke({"category": "breakfast-brunch"})
    find_recipes.invoke({"stage": "tested"})
    assert api["seen"][-1].url.params["stage"] == "tested"


def test_published_recipes_are_searched_by_the_api(api):
    api["routes"][("GET", "/v1/recipes")] = (200, {"recipes": [dict(MINE["recipes"][0],
                                                                   visibility="published")]})
    out = find_recipes.invoke({"query": "עוגה", "mine": False, "category": "desserts"})
    params = api["seen"][-1].url.params
    assert (params["q"], params["category"]) == ("עוגה", "desserts")
    assert "published · yours · id r1" in out


def test_a_full_recipe_is_rendered_as_data(api):
    out = get_recipe.invoke({"recipe_id": "r2"})
    assert out.startswith("שקשוקה — id r2, /recipe/r2")
    assert "<recipe>" in out and "</recipe>" in out
    assert "  - 6 ביצים" in out and "  - 0.5 כוס רסק" in out
    assert "  1. לטגן בצל" in out and "Prep time: 25 min" in out and "Notes: חריף" in out
    assert "Photo (main): https://img/1.jpg" in out


def test_sections_and_stages_are_rendered_with_their_titles(api):
    full = dict(FULL, recipe=dict(FULL["recipe"], ingredients=None, instructions=None,
                                  ingredientSections=[{"title": "בצק", "items": [{"item": "קמח"}]},
                                                      {"title": "מלית", "items": [{"item": "גבינה"}]}],
                                  stages=[{"title": "הכנה", "instructions": ["ללוש"]},
                                          {"title": "אפייה", "instructions": ["לאפות"]}]))
    api["routes"][("GET", "/v1/recipes/r2")] = (200, full)
    out = get_recipe.invoke({"recipe_id": "r2"})
    assert "  בצק:\n    - קמח" in out and "  אפייה:\n    1. לאפות" in out


@pytest.mark.parametrize("status, body, says", [
    (404, {"error": {"code": "not-found", "message": "x"}}, "No such recipe"),
    (403, {"error": {"code": "forbidden", "message": "x"}}, "refused"),
    (429, {"error": {"code": "rate-limited", "message": "x"}}, "daily request limit"),
    (502, "<html>bad gateway</html>", "HTTP 502"),
])
def test_errors_become_one_sentence(api, status, body, says):
    api["routes"][("GET", "/v1/recipes/r2")] = (status, body)
    out = get_recipe.invoke({"recipe_id": "r2"})
    assert out.startswith("Error: ") and says in out


def test_without_settings_the_tools_say_so(api, monkeypatch):
    monkeypatch.delenv("COOKBOOK_API_TOKEN")
    assert "isn't set up here" in find_recipes.invoke({})
    assert api["seen"] == []


def test_a_timeout_is_reported(api, monkeypatch):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)
    monkeypatch.setattr(_client, "_transport", httpx.MockTransport(slow))
    assert "didn't answer in time" in get_recipe.invoke({"recipe_id": "r2"})


def test_categories_are_fetched_once(api):
    find_recipes.invoke({})
    find_recipes.invoke({})
    assert sum(r.url.path == "/recipeApi/v1/categories" for r in api["seen"]) == 1


# --- save_recipe ---------------------------------------------------------------------

import io  # noqa: E402
import json  # noqa: E402

from files import store  # noqa: E402
from tools.cookbook import save as save_mod  # noqa: E402
from tools.cookbook import save_recipe  # noqa: E402

RECIPE = {"name": "ראמן חמאת בוטנים", "category": "desserts", "difficulty": "קלה",
          "prepTime": 20, "servings": 2,
          "ingredients": [{"item": "אטריות ראמן", "amount": "2", "unit": "חבילות"},
                          {"item": "חמאת בוטנים", "amount": "1/2", "unit": "כוס"},
                          {"item": "מלח", "amount": None}],
          "instructions": ["לבשל אטריות", "לערבב רוטב"],
          "attribution": "@homemade_style_recipe, https://www.instagram.com/reel/De1X6FFP_tt/"}
REEL = "https://www.instagram.com/reel/De1X6FFP_tt/"


@pytest.fixture
def confirm(monkeypatch):
    from gateway import factory
    from tests.tools.test_collections import FakeConfirmation

    fake = FakeConfirmation()
    monkeypatch.setattr(factory, "get_confirmation", lambda: fake)
    return fake


@pytest.fixture
def created(api):
    """The POST route: 201 the first time an id is seen, 200 'already yours' after."""
    seen_ids = set()

    def post(request):
        if request.headers["content-type"].startswith("multipart/"):
            body = request.read()
            start = body.index(b"{")
            payload = json.JSONDecoder().raw_decode(body[start:].decode("utf-8", "replace"))[0]
        else:
            payload = json.loads(request.content)
        first = payload["id"] not in seen_ids
        seen_ids.add(payload["id"])
        api["posted"].append((payload, request))
        return httpx.Response(201 if first else 200, json={
            "id": payload["id"], "path": f"/recipe/{payload['id']}", "stage": payload["stage"],
            "submitted": payload.get("submit", False), "created": first,
            "images": len(payload.get("images", [])),
            "mediaInstructions": len(payload.get("mediaInstructions", []))})
    api["posted"] = []
    inner = _client._transport

    def route(request):
        if request.method == "POST":
            return post(request)
        return inner.handle_request(request)
    _client._transport = httpx.MockTransport(route)
    return api


def save(**kw):
    return save_recipe.invoke({"recipe": RECIPE, **kw})


def test_nothing_is_sent_before_the_owner_confirms(created, confirm):
    out = save(source=REEL)
    assert out.startswith("Confirmation request sent.") and created["posted"] == []
    description, _ = confirm.pending[0]
    assert "save a recipe to the cookbook: ראמן חמאת בוטנים" in description
    assert "קינוחים · קלה · 20 min · 2 servings · to try" in description
    assert confirm.tap().startswith("Saved “ראמן חמאת בוטנים” to the cookbook, /recipe/jarvis-")


def test_the_body_is_the_apis_shape(created, confirm):
    save(source=REEL)
    confirm.tap()
    (payload, request), = created["posted"]
    assert request.headers["content-type"] == "application/json"
    assert (payload["stage"], payload["submit"]) == ("to-try", False)
    items = payload["recipe"]["ingredients"]
    assert [i.get("amount") for i in items] == [2, "1/2", None]
    assert "amount" not in items[2], "an empty field is left out, not sent as null"
    assert "ingredientSections" not in payload["recipe"] and "stages" not in payload["recipe"]


def test_the_same_source_is_never_saved_twice(created, confirm):
    save(source=REEL)
    confirm.tap()
    save(source=REEL + " ")
    assert "already in the cookbook" in confirm.tap()
    assert created["posted"][0][0]["id"] == created["posted"][1][0]["id"]
    assert save_mod.recipe_id("", {"a": 1}) == save_mod.recipe_id("", {"a": 1})


def test_estimates_are_marked_in_the_recipe_and_the_confirmation(created, confirm):
    save(source=REEL, estimated=["prepTime", "servings"])
    assert "Estimated by Jarvis: prepTime, servings" in confirm.pending[0][0]
    confirm.tap()
    assert created["posted"][0][0]["recipe"]["comments"] == ["זמן ההכנה וכמות המנות הם הערכה בלבד."]


def test_the_estimate_note_is_plain_hebrew():
    note = save_mod.estimate_note
    assert note(["difficulty"]) == "רמת הקושי היא הערכה בלבד."
    assert note(["prepTime"]) == "זמן ההכנה הוא הערכה בלבד."
    assert note(["prepTime", "waitTime", "servings"]) == \
        "זמן ההכנה, זמן ההמתנה וכמות המנות הם הערכה בלבד."


def test_tested_and_submit_go_through(created, confirm):
    save(source=REEL, stage="tested", submit=True)
    assert "tried · and send it for approval" in confirm.pending[0][0]
    assert "sent for approval" in confirm.tap()


@pytest.mark.parametrize("change, says", [
    ({"category": "pasta"}, "category 'pasta' isn't one of: desserts (קינוחים)"),
    ({"difficulty": "easy"}, "difficulty must be one of"),
    ({"prepTime": 0}, "prepTime must be at least 1"),
    ({"ingredientSections": [{"title": "א", "items": [{"item": "x"}]}]}, "exactly one"),
    ({"ingredients": None, "ingredientSections": [{"title": "א", "items": [{"item": "x"}]}]},
     "two or more sections"),
    ({"instructions": None}, "give instructions or stages"),
])
def test_mistakes_are_caught_before_anything_is_asked(created, confirm, change, says):
    out = save_recipe.invoke({"recipe": {**RECIPE, **change}})
    assert out.startswith("Not saved — fix these first:") and says in out
    assert confirm.pending == [] and created["posted"] == []


def test_estimated_names_only_what_may_be_estimated(created, confirm):
    assert "estimated can only name" in save(estimated=["ingredients"])


def test_api_refusals_come_back_as_the_outcome(created, confirm):
    created["routes"][("GET", "/v1/categories")] = (200, CATEGORIES)

    def refuse(request):
        if request.method == "POST":
            return httpx.Response(400, json={"error": {"code": "invalid-recipe", "message": "x",
                                                       "details": [{"field": "servings",
                                                                    "message": "too many"}]}})
        return httpx.Response(200, json=CATEGORIES)
    _client._transport = httpx.MockTransport(refuse)
    save(source=REEL)
    with pytest.raises(_client.CookbookError) as e:
        confirm.tap()
    assert str(e.value) == ("Not saved: The cookbook refused the recipe (invalid-recipe). "
                            "Fix these fields: servings: too many."), "the prompt shows a failure"


# --- files --------------------------------------------------------------------------

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64


def _png(size=(64, 48)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def test_photos_and_a_step_video_go_as_multipart(created, confirm):
    photo = store.add(_png(), source="t", filename="dish.png")
    video = store.add(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, source="web",
                      filename="reel.mp4", mime_hint="video/mp4")
    assert video.kind == "video"
    save(source=REEL, photo_file_ids=[photo.id], video_file_id=video.id, video_caption="ללוש")
    assert "1 photo(s) and a step video" in confirm.pending[0][0]
    out = confirm.tap()
    assert "(1 photo(s), a step video)" in out
    (payload, request), = created["posted"]
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert payload["images"] == [{"part": "photo1", "isPrimary": True}]
    assert payload["mediaInstructions"] == [{"part": "step1", "caption": "ללוש"}]
    raw = request.read()
    assert b'name="photo1"; filename="photo1.png"' in raw and b'name="step1"' in raw
    assert b'name="request"\r\nContent-Type: application/json' in raw


def test_a_large_or_unaccepted_photo_is_re_encoded_as_jpeg(monkeypatch):
    import os
    from PIL import Image

    noisy = io.BytesIO()
    Image.frombytes("RGB", (600, 600), os.urandom(600 * 600 * 3)).save(noisy, "PNG")
    monkeypatch.setattr(save_mod, "PHOTO_MAX_BYTES", 60_000)
    data, mime = save_mod._photo(noisy.getvalue(), "image/png")
    assert mime == "image/jpeg" and len(data) <= 60_000 < len(noisy.getvalue())
    monkeypatch.setattr(save_mod, "PHOTO_MAX_BYTES", 5 * 1024 * 1024)
    gif = io.BytesIO()
    Image.new("RGB", (20, 20)).save(gif, "GIF")
    assert save_mod._photo(gif.getvalue(), "image/gif")[1] == "image/jpeg"
    small = _png()
    assert save_mod._photo(small, "image/png") == (small, "image/png"), "kept as it is"


def test_files_that_cant_be_used_are_refused_before_asking(created, confirm):
    voice = store.add(b"OggS" + b"\x00" * 60, source="t", mime_hint="audio/ogg")
    assert "isn't a stored photo" in save(photo_file_ids=[voice.id])
    assert "isn't a stored MP4" in save(video_file_id=voice.id)
    gone = store.add(_png((10, 10)), source="t")
    store.delete(gone.id, "owner")
    assert "Not saved:" in save(photo_file_ids=[gone.id])
    assert "at most 5 photos" in save(photo_file_ids=["file:00000000"] * 6)
    assert confirm.pending == []


def test_a_video_over_the_request_limit_is_left_out_and_said(created, confirm, monkeypatch):
    monkeypatch.setattr(save_mod, "REQUEST_MAX_BYTES", 50)
    video = store.add(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, source="web", mime_hint="video/mp4")
    save(source=REEL, video_file_id=video.id)
    assert "the video" in confirm.pending[0][0] and "left out" in confirm.pending[0][0]
    out = confirm.tap()
    assert "left out" in out and created["posted"][0][1].headers["content-type"] == "application/json"



def test_a_link_shared_twice_gives_the_same_id():
    rid = lambda url: save_mod.recipe_id(url, {})  # noqa: E731
    reel = "https://www.instagram.com/reel/De1ZQmYsGlk/"
    assert rid(reel + "?srtk=bDdudjA5MDEwOXds") == rid(reel + "?igsh=abc") == rid(reel) \
        == rid("https://instagram.com/reel/De1ZQmYsGlk") == rid(reel + "?utm_source=ig#x")
    assert rid("https://www.youtube.com/watch?v=AODAwMANijw&si=xyz") == \
        rid("https://youtube.com/watch?v=AODAwMANijw")
    assert rid("https://www.youtube.com/watch?v=AAAAAAAAAAA") != rid(
        "https://www.youtube.com/watch?v=BBBBBBBBBBB"), "the page's own parameters stay"
    assert rid("file:2s11xcez") == rid(" file:2s11xcez ")



def test_a_second_recipe_from_the_same_source_gets_its_own_id():
    assert save_mod.recipe_id(REEL, {"name": "חומוס"}) != save_mod.recipe_id(REEL, {"name": "טחינה"})
    assert save_mod.recipe_id(REEL, {"name": "חומוס"}) == save_mod.recipe_id(REEL + "?igsh=1",
                                                                            {"name": " חומוס "})


def test_youtube_link_forms_and_timestamps_are_one_link():
    rid = lambda url: save_mod.recipe_id(url, {"name": "x"})  # noqa: E731
    assert rid("https://youtu.be/AODAwMANijw?t=30") == rid("https://www.youtube.com/watch?v=AODAwMANijw&t=1m")


@pytest.mark.parametrize("amount, sent", [("2", 2), ("0.5", 0.5), (".5", 0.5), ("1,5", 1.5),
                                          ("1/2", "1/2"), ("1½", "1½"), ("1 1/2", "1 1/2")])
def test_amounts_the_cookbook_takes(amount, sent):
    assert save_mod._amount(amount) == sent


@pytest.mark.parametrize("amount", ["0", "2-3", "a pinch", "1/0"])
def test_amounts_it_doesnt_are_caught_before_asking(created, confirm, amount):
    recipe = {**RECIPE, "ingredients": [{"item": "מלח", "amount": amount}]}
    out = save_recipe.invoke({"recipe": recipe})
    assert out.startswith("Not saved — fix these first:") and "must be a positive number" in out
    assert confirm.pending == []
