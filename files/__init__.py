"""The file store: every file Jarvis receives, fetches or sends, known by a stable id.

store       add / read / state changes / collection of unused bytes / health
references  which ids are in use, computed from the places that hold them
ids         the `file:` id format
blobs       bytes on disk (config.FILES_DIR), named by content hash
index       the SQLite index (jarvis_data/files/files.sqlite)
"""
