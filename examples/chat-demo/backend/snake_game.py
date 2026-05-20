import pygame
import random

# Initialize pygame
pygame.init()

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)

# Screen dimensions
SCREEN_WIDTH = 600
SCREEN_HEIGHT = 600

# Create screen
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption('Snake Game')

# Game settings
clock = pygame.time.Clock()
FPS = 15  # Controls game speed

class Snake:
    def __init__(self):
        self.size = 1
        self.positions = [(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2)]  # Start in center
        self.direction = random.choice([(-10, 0), (10, 0), (0, -10), (0, 10)])  # Start with random direction
        self.speed = 10
    
    def get_head_position(self):
        return self.positions[0]
    
    def move(self):
        # Get current head position
        head_x, head_y = self.get_head_position()
        # Get direction change
        delta_x, delta_y = self.direction
        # Calculate new head position
        new_x = (head_x + delta_x) % SCREEN_WIDTH
        new_y = (head_y + delta_y) % SCREEN_HEIGHT
        
        # Check if new position collides with body
        if (new_x, new_y) in self.positions[1:]:
            return False  # Game over
        
        # Insert new head position
        self.positions.insert(0, (new_x, new_y))
        
        # Remove tail if not growing
        if len(self.positions) > self.size:
            self.positions.pop()
            
        return True  # Movement successful
    
    def change_direction(self, new_direction):
        # Prevent 180-degree turns
        opposite = (-self.direction[0], -self.direction[1])
        if new_direction != opposite:
            self.direction = new_direction
    
    def draw(self, surface):
        for position in self.positions:
            rect = pygame.Rect(position[0], position[1], 10, 10)
            pygame.draw.rect(surface, GREEN, rect)
            pygame.draw.rect(surface, WHITE, rect, 1)  # Border