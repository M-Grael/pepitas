from settings import *
import time
from random import choice
from model import SubjectController

class Pepita(pygame.sprite.Sprite):
    def __init__(self, *groups, id):
        super().__init__(*groups)

        self.image = pygame.Surface((15, 15), pygame.SRCALPHA)
        pygame.draw.rect(self.image, choice(possible_pepita_colors), (0, 0, 7, 7), border_radius=4)

        positions = [
            (WORLD_WIDTH  * 0.1,  WORLD_HEIGHT * 0.9),
            (WORLD_WIDTH  * 0.84, WORLD_HEIGHT * 0.16),
            (WORLD_WIDTH  * 0.69, WORLD_HEIGHT * 0.80),
        ]

        self.rect = self.image.get_frect(center=positions[id])
        self.id   = id
        self.controller = SubjectController()

class Cursor(pygame.sprite.Sprite):
    """O cursor vive no espaço de TELA — não recebe offset de câmera."""
    def __init__(self, pepitas_group, *groups):
        super().__init__(*groups)
        self.pepitas_group = pepitas_group

        self.cursor_on  = pygame.image.load(join('figs', 'finder_on.png')).convert_alpha()
        self.cursor_on  = pygame.transform.rotozoom(self.cursor_on,  0, 0.8)
        self.cursor_off = pygame.image.load(join('figs', 'finder_off.png')).convert_alpha()
        self.cursor_off = pygame.transform.rotozoom(self.cursor_off, 0, 0.8)

        self.finding_sound = pygame.mixer.Sound(join('audio', 'click.mp3'))

        self.image = self.cursor_off
        self.pos   = pygame.mouse.get_pos()

        self.hiting_pepita   = False
        self.click_timestamp = 0
        self.current_time    = pygame.time.get_ticks()

        self.camera = None

    def finder(self):
        if self.hiting_pepita:
            return

        mouse_press   = pygame.mouse.get_pressed()
        dentro_do_raio = False

        if self.camera is not None:
            world_mouse = self.camera.world_pos(self.pos)
            for pepita in self.pepitas_group:
                distance = pygame.Vector2(world_mouse).distance_to(pepita.rect.center)
                if (distance <= pepita.controller.search_radius
                        and pepita.controller.current_schedule.get_name() != 'EXT'):
                    dentro_do_raio = True
                    break

        if mouse_press[0] and dentro_do_raio:
            self.image = self.cursor_on
        else:
            self.image = self.cursor_off

        mouse_just_pressed = pygame.mouse.get_just_pressed()
        if mouse_just_pressed[0] and dentro_do_raio:
            if not pygame.mixer.get_busy():
                self.finding_sound.play()

    def start_hit_effect(self):
        if not pygame.mixer.get_busy():
            self.finding_sound.play()
        self.hiting_pepita   = True
        self.click_timestamp = pygame.time.get_ticks()
        self.image           = self.cursor_on

    def manage_timer(self):
        if self.hiting_pepita:
            if self.current_time - self.click_timestamp >= 200:
                self.image         = self.cursor_off
                self.hiting_pepita = False

    def update(self, dt):
        self.pos          = pygame.mouse.get_pos()
        self.rect         = self.image.get_frect(topleft=self.pos)
        self.current_time = pygame.time.get_ticks()
        self.finder()
        self.manage_timer()


class Reward(pygame.sprite.Sprite):
    def __init__(self, pos, *groups):
        super().__init__(*groups)
        
        # IMPORTANTE: Usar convert_alpha() em vez de convert()
        original_image = pygame.image.load(join('figs', 'pepita.png')).convert_alpha()
        self.image = pygame.transform.rotozoom(original_image, 0, 0.4)
        self.rect = self.image.get_frect(center=(pos[0]// 2, pos[1]// 2))
        
        # --- Lógica da Borracha e Porcentagem ---
        # Cria a máscara inicial e conta quantos pixels formam a imagem
        self.mask = pygame.mask.from_surface(self.image)
        self.total_pixels = self.mask.count()
        
        # Configuração da superfície da borracha
        self.eraser_radius = 30  # Aumente ou diminua para mudar o tamanho da borracha
        self.eraser_surf = pygame.Surface((self.eraser_radius * 2, self.eraser_radius * 2), pygame.SRCALPHA)
        self.eraser_surf.fill((255, 255, 255, 255)) # Fundo totalmente opaco
        pygame.draw.circle(self.eraser_surf, (0, 0, 0, 0), (self.eraser_radius, self.eraser_radius), self.eraser_radius)
        
        self.is_consumed = False

    def consumation(self, world_mouse_pos):
        if self.is_consumed:
            return False

        # Verifica se o mouse está sobre a recompensa
        if self.rect.collidepoint(world_mouse_pos):
            # Encontra a posição relativa do mouse DENTRO da imagem da recompensa
            rel_x = world_mouse_pos[0] - self.rect.x
            rel_y = world_mouse_pos[1] - self.rect.y

            # Aplica a borracha na imagem usando BLEND_RGBA_MIN
            # Onde a borracha tem alpha 0, a imagem passará a ter alpha 0
            self.image.blit(
                self.eraser_surf, 
                (rel_x - self.eraser_radius, rel_y - self.eraser_radius), 
                special_flags=pygame.BLEND_RGBA_MIN
            )

            # Atualiza a máscara para contar os pixels restantes
            self.mask = pygame.mask.from_surface(self.image)
            current_pixels = self.mask.count()

            # Se restarem 10% ou menos dos pixels originais (95% apagado)
            if current_pixels <= self.total_pixels * 0.05:
                self.is_consumed = True
                return True # Retorna True avisando que o consumo foi concluído
                
        return False


def pepita_counter(surface, rewards):
    counter_font = pygame.font.SysFont('Times New Roman', 60)
    counter_surf = counter_font.render(f'Pepitas: {rewards}', True, "#BAC5D6")
    counter_rect = counter_surf.get_frect(topleft=(30, 30))

    padding  = 20
    box_size = (counter_rect.width + padding, counter_rect.height + padding)
    box_surf = pygame.Surface(box_size)
    box_surf.fill("#967C94")
    box_surf.set_alpha(100)
    box_rect = box_surf.get_frect(center=counter_rect.center)

    surface.blit(box_surf, box_rect)
    surface.blit(counter_surf, counter_rect)


def _render_multiline(surface, font, text, color, center_x, start_y, line_spacing=70):
    """Renderiza texto com quebras de linha manuais (\n).
    CORRIGIDO: pygame.font.render não suporta \n nativamente.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if not line:
            continue
        surf = font.render(line, True, color)
        rect = surf.get_frect(center=(center_x, start_y + i * line_spacing))
        surface.blit(surf, rect)


def instruction_box(surface):
    surface.fill("#021729")
    instruction_font = pygame.font.SysFont('Times New Roman', 60)

    _render_multiline(
        surface, instruction_font,
        'Pepitas are very nice!!!\n\nCollect as much pepitas as you can',
        "#BAC5D6",
        center_x=WINDOW_WIDTH / 2,
        start_y=WINDOW_HEIGHT / 2 - 70
    )

    espaco_surf = instruction_font.render('Press "SPACE" to continue', True, "#BAC5D6")
    espaco_rect = espaco_surf.get_frect(center=(WINDOW_WIDTH / 2, WINDOW_HEIGHT * 0.9))
    surface.blit(espaco_surf, espaco_rect)

    keys = pygame.key.get_just_pressed()
    return bool(keys[pygame.K_SPACE])


def final_box(surface):
    surface.fill("#021729")
    instruction_font = pygame.font.SysFont('Times New Roman', 60)

    instruction_surf = instruction_font.render('THE EXPERIMENT IS NOT FINISHED YET', True, "#BAC5D6")
    instruction_rect = instruction_surf.get_frect(center=(WINDOW_WIDTH / 2, WINDOW_HEIGHT / 2))
    surface.blit(instruction_surf, instruction_rect)

    espaco_surf = instruction_font.render('Call the experimenter to further instructions', True, "#BAC5D6")
    espaco_rect = espaco_surf.get_frect(center=(WINDOW_WIDTH / 2, WINDOW_HEIGHT * 0.9))
    surface.blit(espaco_surf, espaco_rect)

    keys = pygame.key.get_just_pressed()
    return bool(keys[pygame.K_SPACE])