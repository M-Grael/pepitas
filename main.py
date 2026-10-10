from settings import *
from logger import InputLogger
from view import *
from model import *
from routine_manager import *
import json
import os


class Experiment():
    def __init__(self, info):
        self.player_info = info

        self.display_surface = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.FULLSCREEN | pygame.SCALED)
        pygame.display.set_caption('Pepita3!')
        self.clock   = pygame.time.Clock()
        self.running = True

        os.makedirs('data', exist_ok=True)
        log_path = join('data', f"{info['session']}_{info['name']}.json")
        
        random_seed = random.randrange(2**32)
        random.seed(random_seed)

        self.log = InputLogger(
            log_path,
            name=info['name'],
            age=info['age'],
            session=info['session'],
            random_seed=random_seed
        )
        
        self.waiting_input = True

        self.scroll_y         = 0
        self.last_scroll_time = 0

        # Salva a imagem base intocada na memória para evitar perda de qualidade ou lentidão
        self.original_bg = pygame.image.load(join('figs', 'background.png')).convert_alpha()
        
        self.background_surf = pygame.transform.scale(self.original_bg, (WORLD_WIDTH, WORLD_HEIGHT))
        self.background_rect = self.background_surf.get_frect(topleft=(0, 0))


        self.cash_sound    = pygame.mixer.Sound(join('audio', 'cash.mp3'))
        self.pickaxe_sound = pygame.mixer.Sound(join('audio', 'scroll.mp3'))
 
        self.last_clicked_pepita_id = None
        self.cod_end_time           = 0
    

        # Carrega as configurações da rotina utilizando o seu interpretador
        routine_path = join('routines', info['routine'])
        data = parse_routine_file(routine_path)
        self.config = data.get("config", {})

        self.all_sprites     = pygame.sprite.LayeredUpdates()
        self.pepitas_sprites = pygame.sprite.Group()
        self.reward_sprites  = pygame.sprite.Group()

        for n in range(self.config['n_pepitas']):
            Pepita((self.all_sprites, self.pepitas_sprites), id=n)

        for p in self.pepitas_sprites:
            p.original_center = pygame.Vector2(p.rect.center)

        self.cursor = Cursor(self.pepitas_sprites, self.all_sprites)
        self.all_sprites.change_layer(self.cursor, 99)

        pepitas_list = self.pepitas_sprites.sprites()
        self.camera        = Camera(WORLD_WIDTH, WORLD_HEIGHT)
        self.routine_manager = RoutineManager(join('routines', info['routine']), pepitas_list, self.camera)

        self.cursor.camera = self.camera
        self.hitting_pepita = None

    def _evaluate_response(self, pepita_alvo, world_mouse, magnitude=1):
        agora = pygame.time.get_ticks()

        # Verifica e aplica o COD (Changeover Delay)
        if (self.last_clicked_pepita_id is not None
                and self.last_clicked_pepita_id != pepita_alvo.id):
            self.cod_end_time = agora + self.routine_manager.cod_duration

        self.last_clicked_pepita_id = pepita_alvo.id

        # Calcula as distâncias para registrar no log
        dist_pepitas = [
            (pygame.Vector2(world_mouse).distance_to(p.rect.center), p)
            for p in self.pepitas_sprites
        ]
        menor_dist = min([d[0] for d in dist_pepitas]) if dist_pepitas else 0
        todas_dists = [d[0] for d in dist_pepitas]
        hitting_pepita_burst = pepita_alvo if pepita_alvo.rect.collidepoint(world_mouse) else None

        # Validação do reforço vs COD
        if agora < self.cod_end_time:
            self._log_burst_end(pepita_alvo.id, dist_pepitas, hitting_pepita=hitting_pepita_burst)
        else:
            if pepita_alvo.controller.register_response(magnitude, menor_dist, todas_dists):
                if len(self.reward_sprites) == 0:
                    if pepita_alvo.controller.search_radius != 0:
                        if menor_dist <= pepita_alvo.controller.search_radius:
                            self.reward = Reward((int(self.camera.world_width), int(self.camera.world_height)), (self.all_sprites, self.reward_sprites))
                            self.pepita_alvo_recompensa = pepita_alvo
                    else:
                        if pepita_alvo.rect.collidepoint(world_mouse):
                            self.reward = Reward((int(self.camera.world_width), int(self.camera.world_height)), (self.all_sprites, self.reward_sprites))
                            self.pepita_alvo_recompensa = pepita_alvo

            self._log_burst_end(pepita_alvo.id, dist_pepitas, hitting_pepita=hitting_pepita_burst)

    def _log_burst_end(self, nearest_pepita_id, dist_list, hitting_pepita=None):
        """Loga um evento sintético BurstEnd com o total acumulado da rajada."""
        burst_event          = pygame.event.Event(pygame.USEREVENT)
        burst_event.subtype  = 'BurstEnd'
        schedules      = [p.controller.current_schedule.get_name() for p in self.pepitas_sprites]
        pepita_rewards = [p.controller.total_rewards for p in self.pepitas_sprites]
        # Usa o argumento explícito; self.hitting_pepita já foi resetado pelo loop de eventos
        hitting_id     = hitting_pepita.id if hitting_pepita else None

        log_dist_list = [(dist, p.id) for dist, p in dist_list]

        self.log.log_response(burst_event, self.scroll_y, nearest_pepita_id, schedules, pepita_rewards, hitting_id, log_dist_list)

    def run(self):
        while self.running:

            # --- TELA DE INSTRUÇÃO ---
            if self.waiting_input:
                if instruction_box(self.display_surface):
                    self.waiting_input = False
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                pygame.display.flip()
                continue

            dt = self.clock.tick() / 1000
            self.camera.update(dt)

            zoom_ratio_x = self.camera.world_width / WORLD_WIDTH
            zoom_ratio_y = self.camera.world_height / WORLD_HEIGHT

            for p in self.pepitas_sprites:
                p.rect.centerx = int(p.original_center.x * zoom_ratio_x)
                p.rect.centery = int(p.original_center.y * zoom_ratio_y)

            # --- EVENTOS ---
            for event in pygame.event.get():
                keys = pygame.key.get_pressed()
                if event.type == pygame.QUIT or (
                    keys[pygame.K_ESCAPE] and keys[pygame.K_SPACE] and keys[pygame.K_m]
                ):
                    self.running = False

                # Reseta o alvo a cada evento processado para o log ser exato
                self.hitting_pepita = None
                world_mouse = self.camera.world_pos(pygame.mouse.get_pos())

                # Apenas clique para o efeito de "hit" na picareta e validar a resposta
                if event.type == pygame.MOUSEBUTTONDOWN and event.button in [1, 2, 3]:
                    resp_type_map = {1: "left_click", 2: "scroll_click", 3: "right_click"}
                    resp_type = resp_type_map[event.button]
                    
                    world_click = self.camera.world_pos(event.pos)
                    clicou_recompensa = any(reward.rect.collidepoint(world_click) for reward in self.reward_sprites)

                    if not clicou_recompensa:
                        for p in self.pepitas_sprites:
                            # Checa colisão com base no raio ativo ou no hitbox estrito
                            dist = pygame.Vector2(world_click).distance_to(p.rect.center)
                            if p.controller.search_radius != 0:
                                valid_hit = dist <= p.controller.search_radius
                            else:
                                valid_hit = p.rect.collidepoint(world_click)

                            if valid_hit and not isinstance(p.controller.current_schedule, Extinction):
                                if resp_type == 'left_click':
                                    self.cursor.start_hit_effect()
                                    self.hitting_pepita = p
                                
                                # Verifica se a pepita aceita o tipo do clique realizado
                                if resp_type in p.controller.target_responses:
                                    self._evaluate_response(p, world_click, magnitude=1)
                                break

                # Verifica o Scroll — acumula mas NÃO loga (BurstEnd registra a rajada completa)
                if event.type == pygame.MOUSEWHEEL:
                    self.scroll_y        += event.y
                    self.last_scroll_time = pygame.time.get_ticks()
                    
                    if self.scroll_y > 0 and not pygame.mixer.get_busy():
                        self.pickaxe_sound.play()
                    
                    # Identifica qual pepita está sofrendo o scroll
                    for p in self.pepitas_sprites:
                        if p.rect.collidepoint(world_mouse):
                            self.hitting_pepita = p
                            break
                    continue 

                # Prepara e salva o Log do evento atual
                schedules      = [p.controller.current_schedule.get_name() for p in self.pepitas_sprites]
                pepita_rewards = [p.controller.total_rewards for p in self.pepitas_sprites]
                hitting_id     = self.hitting_pepita.id if self.hitting_pepita else None

                # nearest_pepita: pepita geometricamente mais próxima do mouse neste momento
                dist_list  = [
                    (pygame.Vector2(world_mouse).distance_to(p.rect.center), p)
                    for p in self.pepitas_sprites
                ]
                nearest_id = min(dist_list, key=lambda x: x[0])[1].id if dist_list else None

                log_dist_list = [dist for dist, p in dist_list]

                self.log.log_response(event, self.scroll_y, nearest_id, schedules, pepita_rewards, hitting_id, log_dist_list)

            # --- COLETA DE RECOMPENSA ---
            # Retirado de dentro do loop de eventos para evitar checagens múltiplas no mesmo frame
            if pygame.mouse.get_pressed()[0]:
                world_mouse = self.camera.world_pos(pygame.mouse.get_pos())
                for reward in self.reward_sprites:
                    if reward.consumation(world_mouse):
                        reward.kill()
                        self.cash_sound.play()
                        
                        if hasattr(self, 'pepita_alvo_recompensa'):
                            self.pepita_alvo_recompensa.controller.total_rewards += 1
                            self.pepita_alvo_recompensa.controller.reward_on = False

            # --- SCROLL / COD ---
            if abs(self.scroll_y) > 0 and (
                pygame.time.get_ticks() - self.last_scroll_time >= 200
            ):
                self.pickaxe_sound.stop()

                world_mouse = self.camera.world_pos(pygame.mouse.get_pos())
                dist_pepitas = [
                    (pygame.Vector2(world_mouse).distance_to(p.rect.center), p)
                    for p in self.pepitas_sprites
                ]

                if dist_pepitas:
                    menor_dist, pepita_alvo = min(dist_pepitas, key=lambda x: x[0])
                    
                    # 1. Determina a direção e a magnitude do scroll
                    direcao_scroll = "scroll_up" if self.scroll_y > 0 else "scroll_down"
                    magnitude = abs(self.scroll_y)
                    
                    # 2. Checa se a direção executada (ou "scroll" genérico) é válida
                    respostas_validas = pepita_alvo.controller.target_responses
                    if "scroll" in respostas_validas or direcao_scroll in respostas_validas:
                        
                        # 3. Compara a magnitude absoluta com o min_scroll_y
                        if magnitude >= pepita_alvo.controller.min_scroll_y:
                            self._evaluate_response(pepita_alvo, world_mouse, magnitude=magnitude)
                        else:
                            hitting_abaixo_minimo = (
                                pepita_alvo if pepita_alvo.rect.collidepoint(world_mouse) else None
                            )
                            self._log_burst_end(pepita_alvo.id, dist_pepitas, hitting_pepita=hitting_abaixo_minimo)

                self.scroll_y = 0
 

            # --- RENDERIZAÇÃO ---
            atual_w = int(self.camera.world_width)
            atual_h = int(self.camera.world_height)

            if self.background_rect.width != atual_w or self.background_rect.height != atual_h:
                self.background_surf = pygame.transform.scale(
                    self.original_bg, 
                    (atual_w, atual_h)
                )
                self.background_rect = self.background_surf.get_frect(topleft=(0, 0))

            self.display_surface.blit(
                self.background_surf,
                self.camera.apply(self.background_rect)
            )

            self.all_sprites.update(dt)
            for sprite in self.all_sprites:
                if sprite is self.cursor:
                    continue
                screen_rect = self.camera.apply(sprite.rect)
                self.display_surface.blit(sprite.image, screen_rect)

            total_rewards = sum(p.controller.total_rewards for p in self.pepitas_sprites)
            pepita_counter(self.display_surface, total_rewards)

            self.display_surface.blit(self.cursor.image, self.cursor.rect)

            if self.routine_manager.check_routine():
                self.running = False

            pygame.display.flip()

        # --- ENCERRAMENTO ---
        final_box(self.display_surface)
        pygame.display.flip()

        esperando = True
        while esperando:
            for event in pygame.event.get():
                if event.type in (pygame.QUIT, pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                    esperando = False
            self.clock.tick(15)

        pygame.quit()
        self.log.save()
        exit()


if __name__ == '__main__':
    session_info = get_player_info()
    experiment   = Experiment(info=session_info)
    experiment.run()