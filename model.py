"""
model.py
=========

Modelo de domínio do experimento: esquemas de reforço, o controlador do
sujeito (pepita), a câmera do mundo e a interface de coleta de dados do
jogador (Tkinter).

A responsabilidade de LER e INTERPRETAR arquivos de rotina (a linguagem
de configuração das fases do experimento) foi extraída para o módulo
`routine_interpreter.py` (Lexer + Parser + RoutineManager). Este arquivo
não sabe nada sobre a sintaxe do arquivo de rotina — ele só expõe as
classes de esquema de reforço que o interpretador usa para instanciar
cada contingência.
"""

import random
import math
import tkinter as tk
from tkinter import ttk, messagebox
from os import listdir
from os.path import exists
import pygame
from settings import *


def fleshler_hoffman(media_ms, n, shuffle=True):
    """Gera intervalos pelo método Fleshler-Hoffman.
    media_ms: média em milissegundos.
    """
    intervalos = []
    for i in range(1, n + 1):
        t = -media_ms * math.log(1 - (i - 0.5) / n)
        intervalos.append(t)
    if shuffle:
        random.shuffle(intervalos)
    return intervalos


# ---------------------------------------------------------------------------
# Esquemas de reforço
# ---------------------------------------------------------------------------

class ScheduleState:
    """Classe base para todos os esquemas de reforço."""
    def on_response(self):
        return False

    def get_name(self):
        return "BASE"


class FixedRatio(ScheduleState):
    def __init__(self, requirement):
        self.requirement = requirement
        self.response_counter = 0

    def on_response(self):
        self.response_counter += 1
        if self.response_counter >= self.requirement:
            self.response_counter = 0
            return True
        return False

    def get_name(self):
        return f"FR-{self.requirement}"


class VariableInterval(ScheduleState):
    def __init__(self, interval_seconds):
        self.interval_ms = interval_seconds * 1000
        self.intervals_pool = []
        self._set_next_target_time()

    def _set_next_target_time(self):
        if not self.intervals_pool:
            self.intervals_pool = fleshler_hoffman(self.interval_ms, 10, True)
        next_delay = self.intervals_pool.pop()
        self.next_reward_time = pygame.time.get_ticks() + next_delay

    def on_response(self):
        if pygame.time.get_ticks() >= self.next_reward_time:
            self._set_next_target_time()
            return True
        return False

    def get_name(self):
        return f"VI-{self.interval_ms // 1000}s"


class Extinction(ScheduleState):
    def on_response(self):
        return False

    def get_name(self):
        return "EXT"


class RandomInterval(ScheduleState):
    def __init__(self, interval_seconds):
        self.interval_ms = interval_seconds * 1000
        self._set_next_target_time()

    def _set_next_target_time(self):
        u = random.random()
        while u == 0:
            u = random.random()
        next_delay = -self.interval_ms * math.log(u)
        self.next_reward_time = pygame.time.get_ticks() + next_delay

    def on_response(self):
        if pygame.time.get_ticks() >= self.next_reward_time:
            self._set_next_target_time()
            return True
        return False

    def get_name(self):
        return f"RI-{self.interval_ms // 1000}s"


class DifferentialReinforcementOfLowRates(ScheduleState):
    def __init__(self, interval_seconds):
        self.interval_ms = interval_seconds * 1000
        self.last_response_time = None

    def on_response(self):
        now = pygame.time.get_ticks()
        if self.last_response_time is None:
            self.last_response_time = now
            return False
        irt = now - self.last_response_time
        self.last_response_time = now
        return irt >= self.interval_ms

    def get_name(self):
        return f"DRL-{self.interval_ms // 1000}s"


# ---------------------------------------------------------------------------
# Controlador do sujeito
# ---------------------------------------------------------------------------
class SubjectController:
    def __init__(self):
        self.total_rewards    = 0
        self.total_responses  = 0
        self.n_pepitas        = None
        self.search_radius    = 0
        self.current_schedule = FixedRatio(requirement=1)
        self.session_data     = []
        self.earned_reward    = False
        self.reward_on        = False
        # Adicione a lista de respostas alvo padrão
        self.target_responses = ["scroll"]
        self.min_scroll_y     = 1
        self.last_reward_time = pygame.time.get_ticks()

    # Adicione este método para alterar a resposta alvo
    def set_target_responses(self, responses):
        self.target_responses = responses

    def set_min_scroll_y(self, value):
        self.min_scroll_y = value if value is not None else 1

    def change_schedule(self, new_schedule_state):
        self.current_schedule = new_schedule_state

    def set_radius(self, radius):
        self.search_radius = radius if radius is not None else 0

    def register_response(self, magnitude, min_distance, distance_list):
        now = pygame.time.get_ticks()

        self.total_responses += 1
        self.earned_reward    = False
        self.earned_reward    = self.current_schedule.on_response()

        if self.earned_reward and not self.reward_on:
            self.reward_on = True

        if self.earned_reward:
            self.last_reward_time = now

        event = {
            'timestamp':     now,
            'schedule':      self.current_schedule.get_name(),
            'magnitude':     magnitude,
            'distance':      min_distance,
            'distance_list': distance_list,
            'rewarded':      self.earned_reward
        }
        self.session_data.append(event)
        return self.earned_reward


# ---------------------------------------------------------------------------
# Câmera
# ---------------------------------------------------------------------------

class Camera:
    """Gerencia o offset da câmera e o edge scrolling."""

    def __init__(self, world_width, world_height):
        self.offset       = pygame.Vector2(0, 0)
        self.world_width  = world_width
        self.world_height = world_height
        self.edge_margin  = 80
        self.speed        = 4000

    def scale_world(self, scale):
        self.world_width  *= scale
        self.world_height *= scale

    def update(self, dt):
        mx, my = pygame.mouse.get_pos()

        if mx < self.edge_margin:
            factor = 1.0 - (mx / self.edge_margin)
            self.offset.x -= self.speed * factor * dt
        elif mx > WINDOW_WIDTH - self.edge_margin:
            factor = 1.0 - ((WINDOW_WIDTH - mx) / self.edge_margin)
            self.offset.x += self.speed * factor * dt

        if my < self.edge_margin:
            factor = 1.0 - (my / self.edge_margin)
            self.offset.y -= self.speed * factor * dt
        elif my > WINDOW_HEIGHT - self.edge_margin:
            factor = 1.0 - ((WINDOW_HEIGHT - my) / self.edge_margin)
            self.offset.y += self.speed * factor * dt

        limite_x = max(0, self.world_width - WINDOW_WIDTH)
        limite_y = max(0, self.world_height - WINDOW_HEIGHT)

        self.offset.x = max(0, min(self.offset.x, limite_x))
        self.offset.y = max(0, min(self.offset.y, limite_y))

    def apply(self, rect):
        return rect.move(-self.offset.x, -self.offset.y)

    def world_pos(self, screen_pos):
        return (screen_pos[0] + self.offset.x, screen_pos[1] + self.offset.y)


# ---------------------------------------------------------------------------
# Interface de início (Tkinter)
# ---------------------------------------------------------------------------

def get_player_info():
    info = {}

    def submit():
        name    = entry_nome.get().strip()
        age     = entry_idade.get().strip()
        session = entry_sessao.get().strip()
        routine = combo_routine.get().strip()

        if not all([name, age, session, routine]):
            messagebox.showwarning("Aviso", "Por favor, preencha todos os campos antes de continuar.")
            return

        info['name']    = name
        info['age']     = age
        info['session'] = session
        info['routine'] = routine
        root.destroy()

    def on_closing():
        root.destroy()
        exit()

    root = tk.Tk()
    root.title('Pepitas3!')
    root.geometry("300x320")
    root.protocol("WM_DELETE_WINDOW", on_closing)

    ttk.Label(root, text="Nome do Jogador:").pack(pady=(15, 5))
    entry_nome = ttk.Entry(root, width=30)
    entry_nome.pack()

    ttk.Label(root, text="Idade:").pack(pady=(10, 5))
    entry_idade = ttk.Entry(root, width=30)
    entry_idade.pack()

    itens = listdir('routines') if exists('routines') else []
    ttk.Label(root, text="Rotina:").pack(pady=(10, 5))
    combo_routine = ttk.Combobox(root, values=itens, width=27, state="readonly")
    combo_routine.pack()

    ttk.Label(root, text="Sessão:").pack(pady=(10, 5))
    entry_sessao = ttk.Entry(root, width=30)
    entry_sessao.pack()

    ttk.Button(root, text="Iniciar Experimento", command=submit).pack(pady=25)

    root.eval('tk::PlaceWindow . center')
    root.mainloop()

    return info