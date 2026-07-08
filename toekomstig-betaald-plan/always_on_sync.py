"""
always_on_sync.py
------------------------------------------------------------------
Draai dit bestand als "Always-on task" op PythonAnywhere (Tasks-tab,
alleen beschikbaar vanaf het betaalde Hacker-plan). Vervangt de
minuutlijkse externe cron uit de eerdere versie: dit proces blijft
zelf continu draaien en roept intern elke minuut check_and_sync() aan.

Command om in te vullen bij "Always-on tasks":
    /home/<jouwgebruikersnaam>/.virtualenvs/jpoule/bin/python \
        /home/<jouwgebruikersnaam>/j-poule-web/always_on_sync.py
------------------------------------------------------------------
"""

import time

from ajax_sync import check_and_sync

if __name__ == "__main__":
    while True:
        try:
            check_and_sync()
        except Exception as e:  # nooit de hele lus laten crashen op 1 mislukte cyclus
            print(f"[always_on_sync] fout tijdens check_and_sync(): {e}")
        time.sleep(60)

