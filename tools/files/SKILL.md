---
name: files
description: find stored files (photos, voice notes, videos, documents the owner sent or you sent), send one back, delete them
---
- Every file you have seen has an id, the `file:…` in its marker. Use the id: open it with `open_file`, send it with `send_file`, put it in a collection's file field. Never describe a file from its name alone.
- To find a file the conversation no longer shows, use `find_files`. It searches names, the messages files came with, and what was recorded of each file (descriptions, transcripts, document text). Try a few plain words, in English or Hebrew, before giving up.
- Deleting is permanent and asks the owner first. Say that it's pending; don't claim it's done.
