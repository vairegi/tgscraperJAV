
================================================================================
README_PATCH — v41: /stats v2 + /removeavoid N + replace-wins guarantee
================================================================================

DRAG onto the repo root: botapi.py, README_PATCH.txt (everything else unchanged).

1) /stats v2 — per userbot: connection state, name/@username/id, then a full
   membership matrix: ✅ member / ❌ NOT a member / 👑 admin for EVERY target
   channel and every DB + DB2 channel, plus the CONTROL BOT's own DB/DB2
   rights. A ❌ on a target is exactly why a resume silently does nothing —
   join it with /invite.

2) /removeavoid N — N is the entry number in the /avoidtext list (per-target
   avoids numbered top to bottom). Bare /removeavoid shows the numbered list.

3) /replaceword always wins over /avoid and /avoidtext — replacement rules
   run BEFORE any strip in the DB2 mirror pipeline (order verified in code).

TESTING: py_compile PASS on botapi.py; role-check and numbering logic match
patterns already proven in v38–v40.
================================================================================
