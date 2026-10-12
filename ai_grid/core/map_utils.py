# map_utils.py - v1.5.0 Stable
import datetime
import random
from sqlalchemy.future import select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from ai_grid.models import Character, GridNode, NodeConnection
from ai_grid.grid_utils import C_CYAN, C_GREEN, C_RED, C_YELLOW, C_WHITE, C_GREY, format_text

def get_node_symbol(node: GridNode, char: Character, machine_mode: bool = False, intel_level: str = "NONE") -> str:
    """Determine the symbol and color for a node based on state and intelligence tiers."""
    
    # 0. Priority: Current node is always visible
    if node.id == char.node_id:
        return format_text("[@]", C_CYAN)

    # 1. Fog of War / Intel Tiers
    if intel_level == "NONE":
        return format_text("[?]", C_GREY)

    # 2. Base Symbol Logic (Visited/Open)
    color = C_WHITE
    if node.owner_character_id == char.id:
        color = C_GREEN
    
    # Determine the single character symbol
    if node.active_target:
        char_sym = node.active_target.target_type[0].upper()
    else:
        # Map node types to single characters
        type_map = {
            'safezone': 'S',
            'arena': 'A',
            'merchant': '$',
            'void': 'V'
        }
        char_sym = type_map.get(node.node_type, 'V')
    
    # Special coloring for certain types if not owned
    if node.owner_character_id != char.id:
        if node.node_type == 'safezone': color = C_YELLOW
        elif node.node_type == 'arena': color = C_RED
        elif node.node_type == 'merchant': color = C_YELLOW

    return format_text(f"[{char_sym}]", color)

def get_connector_symbol(source: GridNode, target: GridNode, vertical: bool = False) -> str:
    """Return a 1-2 character connector symbol based on connection health/status."""
    # Logic: Damaged (Durability < 70) > Closed > Normal
    
    is_closed = source.availability_mode == 'CLOSED' or target.availability_mode == 'CLOSED'
    is_damaged = source.durability < 70 or target.durability < 70
    
    if vertical:
        if is_damaged: return "!"
        if is_closed: return "X"
        return "|"
    else:
        # Horizontal - 2 chars wide exactly
        if is_damaged: return "!!"
        if is_closed: return "##"
        return "--"

async def generate_ascii_map(session, char: Character, machine_mode: bool = False, limit_radius: int = None, show_legend: bool = True, center_override: tuple = None) -> str:
    """Generate a grid representation using global coordinates and tiered intelligence."""
    import json
    
    # 1. Calculate Radius
    if limit_radius is not None:
        radius = limit_radius
    else:
        # Load from prefs, default to radius 2 (5x5) if not set
        try:
            prefs = json.loads(char.prefs or '{}')
            radius_pref = prefs.get('radius', 5)
            radius = radius_pref // 2
        except:
            radius = 2
    
    # 2. Determine Bounding Box
    if center_override and len(center_override) == 2:
        center_x, center_y = center_override
    else:
        curr = char.__dict__.get('current_node')
        if curr is not None and hasattr(curr, 'x') and hasattr(curr, 'y'):
            center_x, center_y = curr.x, curr.y
        elif getattr(char, 'node_id', None):
            node_stmt = select(GridNode).where(GridNode.id == char.node_id)
            node_res = await session.execute(node_stmt)
            curr = node_res.scalars().first()
            if curr:
                center_x, center_y = curr.x, curr.y
            else:
                center_x, center_y = 0, 0
        if center_x is None: center_x = 0
        if center_y is None: center_y = 0
        
    min_x, max_x = center_x - radius, center_x + radius
    min_y, max_y = center_y - radius, center_y + radius
    
    # Boundary Clamping
    bounds_res = (await session.execute(select(func.max(GridNode.x), func.max(GridNode.y)))).first()
    b_max_x = bounds_res[0] if bounds_res and bounds_res[0] is not None else 49
    b_max_y = bounds_res[1] if bounds_res and bounds_res[1] is not None else 49
    min_x, max_x = max(0, min_x), min(b_max_x, max_x)
    min_y, max_y = max(0, min_y), min(b_max_y, max_y)

    # 3. Fetch Nodes in Box
    stmt = select(GridNode).where(
        GridNode.x >= min_x, GridNode.x <= max_x,
        GridNode.y >= min_y, GridNode.y <= max_y
    )
    nodes = (await session.execute(stmt)).scalars().all()
    grid = {(n.x, n.y): n for n in nodes}

    # 4. Fetch Discovery Records for these nodes
    from ai_grid.models import DiscoveryRecord
    node_ids = [n.id for n in nodes]
    disc_stmt = select(DiscoveryRecord).where(
        DiscoveryRecord.character_id == char.id,
        DiscoveryRecord.node_id.in_(node_ids)
    )
    res = await session.execute(disc_stmt)
    records = res.scalars().all()
    disc_recs = {d.node_id: d.intel_level for d in records}
    disc_expires = {d.node_id: d.intel_expires_at for d in records}
    
    if not grid: return "MAP ERROR: Coordinate void detected."
    
    # 5. Build Map Output
    now = datetime.datetime.now(datetime.timezone.utc)
    def get_effective_intel(node_id):
        level = disc_recs.get(node_id, "NONE")
        exp = disc_expires.get(node_id)
        if level == "PROBE" and exp and now > exp:
            return "NONE"
        return level

    output = []
    for gy in range(min_y, max_y + 1):
        row = ""
        connector_row = ""
        has_connectors = False
        
        for gx in range(min_x, max_x + 1):
            curr = grid.get((gx, gy))
            if curr:
                # Add node with intel context
                intel = get_effective_intel(curr.id)
                node_sym = get_node_symbol(curr, char, machine_mode, intel)
                row += node_sym
                
                # Check East connector (Horizontal)
                east = grid.get((gx+1, gy))
                if east:
                    # Gated by discovery of BOTH Source and Target
                    east_intel = get_effective_intel(east.id)
                    if intel != "NONE" and east_intel != "NONE":
                        conn_sym = get_connector_symbol(curr, east, vertical=False)
                        row += format_text(conn_sym, C_GREY)
                    else:
                        row += "  "
                else:
                    row += "  "
                
                # Check South connector (Vertical)
                south = grid.get((gx, gy+1))
                if south:
                    # Gated by discovery of BOTH Source and Target
                    south_intel = get_effective_intel(south.id)
                    if intel != "NONE" and south_intel != "NONE":
                        conn_sym = get_connector_symbol(curr, south, vertical=True)
                        connector_row += f"  {format_text(conn_sym, C_GREY)}   "
                        has_connectors = True
                    else:
                        connector_row += "      "
                else:
                    connector_row += "      "
            else:
                row += "     "
                connector_row += "      "
                
        if row.strip():
            output.append(row)
        if has_connectors:
            output.append(connector_row)
            
    if not show_legend:
        return "\n".join(output)
            
    # 6. Build Legend for Center Node
    center_node = grid.get((center_x, center_y))
    if not center_node:
        return "\n".join(output) + "\n" + format_text(f"Grid: ({center_x}, {center_y}) | Intel: [UNK]", C_WHITE)

    intel = disc_recs.get(center_node.id, "NONE")
    expires_at = disc_expires.get(center_node.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Check expiration
    is_probed = (intel == "PROBE")
    if is_probed and expires_at and now > expires_at:
        is_probed = False
        intel = "EXPLORE"

    legend = ""
    # Use 3-char codes for legend types
    LEGEND_MAP = {
        'safezone': 'CIV',
        'arena': 'ARN',
        'merchant': 'MKT',
        'void': 'VOD'
    }
    raw_type = center_node.active_target.target_type.upper() if center_node.active_target else ((getattr(center_node, 'region_type', None) or center_node.node_type).upper())
    t_name = raw_type if (center_node.active_target or getattr(center_node, 'region_type', None)) else LEGEND_MAP.get(raw_type.lower(), raw_type.upper())
    
    if intel == "NONE":
        legend = f"[GRID]🛰️[GEOINT] Grid: ({center_x}, {center_y}) | Intel: [UNK]"
    elif is_probed:
        mins = max(0, int((expires_at - now).total_seconds() / 60))
        legend = f"[GRID]🛰️[GEOINT] Grid: ({center_x}, {center_y}) | Intel: [PRB:{mins}] | Type: [{t_name}:{center_node.upgrade_level}]"
    else:
        legend = f"[GRID]🛰️[GEOINT] Grid: ({center_x}, {center_y}) | Intel: [EXP] | Type: [{t_name}]"

    return "\n".join(output) + "\n" + format_text(legend, C_WHITE)
