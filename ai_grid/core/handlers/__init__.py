# handlers/__init__.py - Coordinator for the Handlers Package
from .base import (
    handle_help,
    is_machine_mode,
    check_rate_limit
)
from .personal import (
    handle_registration,
    handle_login,
    handle_tasks_view,
    handle_options,
    handle_stats,
    handle_news_view,
    handle_memos,
    handle_rep_view
)
from .grid import (
    handle_grid_movement,
    handle_grid_view,
    handle_node_explore,
    handle_grid_map,
    handle_grid_command,
    handle_grid_loot,
    handle_grid_network_msg,
    handle_pulse_resolve
)
from .economy import (
    handle_shop_view,
    handle_merchant_tx,
    handle_auction,
    handle_market_view
)
from .machine import (
    handle_powergen,
    handle_training,
    handle_gibson_status,
    handle_gibson_compile,
    handle_gibson_assemble,
    handle_craft,
    handle_item_use
)
from .combat import (
    handle_mob_encounter,
    resolve_mob,
    handle_pvp_command,
    start_pvp_combat,
    resolve_pvp_challenge,
    handle_ready,
    handle_dice_roll,
    handle_cipher_start,
    handle_guess,
    handle_leaderboard,
    handle_bet
)
from .admin import (
    handle_admin_command
)
from .spectator import (
    handle_spectator_view,
    handle_spectator_stats,
    handle_spectator_help,
    handle_spectator_inventory,
    handle_spectator_drop
)
from .osint import (
    handle_gridpower_osint,
    handle_gridstability_osint,
    handle_networks_osint,
    handle_about_osint,
    handle_info_nick
)
from .hardware import (
    handle_grid_hardware
)
from .incursion import (
    handle_incursion_defend
)
from .skills import (
    handle_skill
)
from .remote_net import (
    handle_remote_net_command
)

__all__ = [
    'handle_help', 'is_machine_mode', 'check_rate_limit',
    'handle_registration', 'handle_login', 'handle_tasks_view', 'handle_options', 'handle_stats', 'handle_news_view', 'handle_memos', 'handle_rep_view',
    'handle_grid_movement', 'handle_grid_view', 'handle_node_explore', 'handle_grid_map', 'handle_grid_command', 'handle_grid_loot', 'handle_grid_network_msg',
    'handle_shop_view', 'handle_merchant_tx', 'handle_auction', 'handle_market_view',
    'handle_powergen', 'handle_training', 'handle_gibson_status', 'handle_gibson_compile', 'handle_gibson_assemble', 'handle_craft', 'handle_item_use',
    'handle_mob_encounter', 'resolve_mob', 'handle_pvp_command', 'start_pvp_combat', 'resolve_pvp_challenge', 'handle_ready', 'handle_dice_roll', 'handle_cipher_start', 'handle_guess', 'handle_leaderboard', 'handle_bet',
    'handle_admin_command', 'handle_spectator_view', 'handle_spectator_stats', 'handle_spectator_help', 'handle_spectator_inventory', 'handle_spectator_drop',
    'handle_economy_osint', 'handle_gridpower_osint', 'handle_gridstability_osint', 'handle_networks_osint', 'handle_about_osint', 'handle_info_nick',
    'handle_grid_hardware', 'handle_incursion_defend', 'handle_skill', 'handle_remote_net_command'
]
