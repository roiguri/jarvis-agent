---
name: cookbook
description: the family cookbook — find and read recipes, save new ones after talking them through
---
- Address a recipe by its id from `find_recipes`; never guess one.
- The cookbook searches names only. To find a recipe by an ingredient, list the owner's recipes and open the likely ones with `get_recipe`.
- Saving is a conversation first. When the owner wants to keep a recipe — from a link, a photo, a voice note, a page or their own words — draft it in chat in Hebrew (translate if needed; keep the original name in the description): name, category, difficulty, times, servings, ingredients, steps. Say plainly which details you estimated because the source didn't give them, and ask about anything that matters. Adjust it as the owner asks. Call `save_recipe` only once they agree; it then asks them to confirm. Say that it's pending; don't claim it's saved.
- Set `attribution` to who the recipe is from and where: the poster and the link for a link, the person the owner names otherwise.
- Never invent an amount, an ingredient or a step the source doesn't have. Only times, servings and difficulty may be estimated, and each estimate goes in `estimated`.
- Before drafting, check `find_recipes` for a recipe with the same name, and mention it if there is one.
- Stage is "to-try" unless the owner says they've made it ("tested"). Send for approval (`submit`) only when the owner asks.
- Photos and a step video go by their file ids (the `file:…` in a marker): the owner's own, or a link's cover picture or video. Pass `source` (the link, or the file ids) so the same recipe is never saved twice.
- If a call says the token isn't valid or access was refused, tell the owner plainly; don't retry.
