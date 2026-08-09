from db import get_connection
import queries

seizoen = queries.get_active_season()
nog_niet_gespeeld = ['PEC Zwolle', 'AFC Ajax', 'FC Groningen', 'FC Utrecht', 'SC Heerenveen', "FC Twente '65"]

with get_connection() as conn:
    print('=== Voor correctie ===')
    for team in nog_niet_gespeeld:
        r = conn.execute('SELECT * FROM standings WHERE seizoen = ? AND team = ?', (seizoen, team)).fetchone()
        print(' ', dict(r) if r else f'{team}: NIET GEVONDEN (controleer de exacte naam)')

    placeholders = ','.join('?' * len(nog_niet_gespeeld))
    aantal_wel_gespeeld = conn.execute(
        f'SELECT COUNT(*) AS n FROM standings WHERE seizoen = ? AND team NOT IN ({placeholders})',
        (seizoen, *nog_niet_gespeeld)
    ).fetchone()['n']
    gedeelde_positie = aantal_wel_gespeeld + 1

    for team in nog_niet_gespeeld:
        conn.execute(
            'UPDATE standings SET positie = ?, punten = 0, gespeeld = 0, winst = 0, gelijk = 0, verlies = 0 WHERE seizoen = ? AND team = ?',
            (gedeelde_positie, seizoen, team)
        )

    print()
    print(f'Gecorrigeerd naar 0 gespeeld, gedeelde positie {gedeelde_positie}.')
    print()
    print('=== Na correctie: volledige stand ===')
    for r in conn.execute('SELECT positie, team, punten, gespeeld FROM standings WHERE seizoen = ? ORDER BY positie, team', (seizoen,)):
        print(' ', dict(r))
