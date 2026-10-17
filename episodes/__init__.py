"""The episode store (L2 memory): everything that happened, recorded by code.

- ``store``: the SQLite file, schema, content hygiene.
- ``record``: what is written when (turn input, turn steps, notifications,
  slash commands).
- ``search``: search_history's logic.
- ``today``: the today view for the user-scope prompt.
"""
