import os
import datetime

LOG_PATH = os.path.join(os.path.dirname(__file__), 'output', 'debug.log')


def log(source: str, event: str, detail: str = ''):
    ts = datetime.datetime.now().isoformat()
    line = f'[{ts}] [{source}] {event}'
    if detail:
        line += f' | {detail[:500]}'
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, 'a') as f:
        f.write(line + '\n')
    print(line)
