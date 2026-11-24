"""Falešný `claude` pro testy generátoru: chování podle FAKE_CLAUDE_MODE (ok, sleep, fail, write:<složka>).

Přepínače prostředím: FAKE_CLAUDE_NOWRITE=1 = nic nezapíše, rc 0; FAKE_CLAUDE_ERROR=1 = rc 1, `is_error: true`
(jako vyčerpaný limit předplatného), nic nezapíše.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
prompt = sys.stdin.buffer.read().decode("utf-8")
Path("fake_args.txt").write_text("\n".join(sys.argv[1:]) + "\n---\n" + prompt, encoding="utf-8")
if os.environ.get("FAKE_CLAUDE_ERROR") == "1":
    print(json.dumps({"type": "result", "is_error": True, "result": "Claude AI usage limit reached"}))
    sys.exit(1)
if os.environ.get("FAKE_CLAUDE_NOWRITE") == "1":
    print(json.dumps({"type": "result", "is_error": False, "result": "Varianty zapsány."}))
    sys.exit(0)
if mode == "sleep":
    time.sleep(60)
elif mode == "fail":
    print("chyba modelu", file=sys.stderr)
    sys.exit(3)
elif mode.startswith("write:"):
    shutil.copytree(mode[len("write:"):], ".", dirs_exist_ok=True)
print('{"result": "ok"}')
