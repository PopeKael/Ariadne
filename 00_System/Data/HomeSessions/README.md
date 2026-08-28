# Ariadne HomeSessions

This directory contains short-lived, backend-owned JSON recovery records for
Ariadne Home chats. Each record is named by its durable `chat_id` and is
retained for seven days after `last_activity_at`.

The records are local temporary state and are intentionally ignored by Git.
Completed, explicitly closed, or expired chats are preserved separately as
human-readable Markdown under `Archive/Chats/YYYY/MM/`. Archive files are not
part of the normal Knowledge Vault source corpus.

Do not hand-edit or move active JSON records while Ariadne is running. The Home
backend uses atomic replacement and a cross-process lock for updates.
