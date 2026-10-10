import pygame
from sys import exit
from os.path import join
pygame.init()
 
WINDOW_WIDTH, WINDOW_HEIGHT = pygame.display.get_desktop_sizes()[0]

WORLD_WIDTH  = WINDOW_WIDTH  * 1  # 2880 px
WORLD_HEIGHT = WINDOW_HEIGHT * 1   # 1440 px

print(WORLD_HEIGHT, WORLD_WIDTH)
 
pygame.display.set_caption('Pepita3!')
pygame.mouse.set_visible(False)
 
possible_pepita_colors = ['#ECD25E', "#ECAA5E", "#EC6E5E"]