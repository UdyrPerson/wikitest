"""
Lance wm_sales_reference.py comme processus VRAIMENT independant.

Sous Windows, un enfant lance normalement (nohup, &, subprocess simple)
reste rattache au job du processus courant et se fait tuer avec lui. Un
scrape de 45 minutes lance depuis un outil qui rend la main aussitot ne
survit donc pas. Meme contournement que pour la fenetre Chrome persistante
de wm_open_booster.py : CREATE_BREAKAWAY_FROM_JOB pour sortir du job,
DETACHED_PROCESS et CREATE_NEW_PROCESS_GROUP pour ne pas heriter de la
console.

    python wm_scrape_launch.py --rarity UR --delay 0.2

La sortie va dans data/scrape-{rarite}.log. Le scrape etant reprenable,
relancer apres une interruption ne recommence pas de zero.
"""

import subprocess
import sys
from pathlib import Path

DATA = Path("data")


def main():
    rarity = "L"
    if "--rarity" in sys.argv:
        rarity = sys.argv[sys.argv.index("--rarity") + 1].upper()
    delay = "0.2"
    if "--delay" in sys.argv:
        delay = sys.argv[sys.argv.index("--delay") + 1]

    DATA.mkdir(exist_ok=True)
    log = DATA / f"scrape-{rarity}.log"

    flags = 0
    for name in ("CREATE_BREAKAWAY_FROM_JOB", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)

    with log.open("a", encoding="utf-8") as fh:
        p = subprocess.Popen(
            [sys.executable, "wm_sales_reference.py", "--rarity", rarity, "--delay", delay],
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            cwd=str(Path.cwd()),
        )
    print(f"Scrape {rarity} lance en processus independant (PID {p.pid}).")
    print(f"Journal : {log.resolve()}")


if __name__ == "__main__":
    main()
