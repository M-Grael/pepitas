from settings import *
import json
from datetime import date

class InputLogger:
    def __init__(self, filename, name, age, session, random_seed):
        self.start_time = pygame.time.get_ticks()
        self.start_date = date.today().isoformat()
        self.data       = []
        self.filename   = filename

        self.metadata = {
            'participant_name': name,
            'age':              age,
            'start_time':       self.start_time,
            'start_date':       self.start_date,
            'session':          session,
            'random_seed':      random_seed
        }

        self.fieldnames = [
            "time_abs",
            "time_rel",
            "event_type",
            "button",
            "x_pos",
            "y_pos",
            "key",
            'scroll_y',
            'nearest_pepita',
            'schedules',
            'rewards',
            'hitting_pepita',
            'dist_list'
        ]

    def log_response(self, event, scroll_y, nearest_pepita, schedules, rewards, hitting_pepita, dist_list):
        now   = pygame.time.get_ticks()
        entry = {
            "time_abs":        now,
            "time_rel":        now - self.start_time,
            "event_type":      pygame.event.event_name(event.type),
            "button":          None,
            "x_pos":           None,
            "y_pos":           None,
            "key":             None,
            'scroll_y':        None,
            'nearest_pepita':  None,
            'schedules':       None,
            'rewards':         None,
            'hitting_pepita':  None,
            'dist_list':       None
        }

        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            entry["button"] = {1: "left", 2: "scroll_press", 3: "right",
                               4: "scroll_up", 5: "scroll_down"}.get(event.button)
            entry["x_pos"], entry["y_pos"] = event.pos

        elif event.type == pygame.MOUSEMOTION:
            entry["x_pos"], entry["y_pos"] = event.pos

        elif event.type == pygame.MOUSEWHEEL:
            # Eventos intermediários de scroll são suprimidos.
            # Apenas o BurstEnd registra a rajada completa.
            return

        elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
            entry["key"] = pygame.key.name(event.key)

        elif event.type == pygame.USEREVENT and getattr(event, 'subtype', None) == 'BurstEnd':
            # Evento sintético disparado em main.py quando a rajada termina (reset do acumulador).
            # Registra o total acumulado da rajada inteira e a posição do mouse naquele momento.
            entry["event_type"]        = "BurstEnd"
            entry['scroll_y']          = scroll_y
            entry["x_pos"], entry["y_pos"] = pygame.mouse.get_pos()

        entry['nearest_pepita'] = nearest_pepita
        entry['schedules']      = schedules[0] if len(schedules) == 1 else schedules
        entry['rewards']        = rewards[0] if len(rewards) == 1 else rewards
        entry['hitting_pepita'] = hitting_pepita
        entry['dist_list'] = dist_list[0] if len(dist_list) == 1 else dist_list

        self.data.append(entry)

    def save(self):
        if not self.data:
            return

        export_data = {
            "metadata": self.metadata,
            "data": self.data
        }

        with open(self.filename, "w", encoding='utf-8') as f:
            json.dump(export_data, f, indent=4, ensure_ascii=False)