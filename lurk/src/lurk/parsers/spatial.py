"""Spatial OCR clustering — groups screen text by position, labels by location.

Takes OCR results with bounding boxes, clusters them into spatially adjacent
groups, then assigns positional labels (e.g. "Top Left", "Bottom Right")
based on each group's bounding box location on screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TextBlock:
    """One OCR observation with its normalized bounding box."""
    text: str
    x: float  # left edge, normalized [0,1]
    y: float  # bottom edge, normalized [0,1], bottom-left origin
    w: float
    h: float


@dataclass
class BlockGroup:
    """A cluster of spatially adjacent text blocks."""
    blocks: list[TextBlock] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    @property
    def avg_x(self) -> float:
        if not self.blocks:
            return 0.0
        return sum(b.x for b in self.blocks) / len(self.blocks)

    @property
    def min_y(self) -> float:
        if not self.blocks:
            return 0.0
        return min(b.y for b in self.blocks)

    @property
    def max_y(self) -> float:
        if not self.blocks:
            return 0.0
        return max(b.y + b.h for b in self.blocks)


@dataclass
class ScreenRegion:
    """A classified screen region."""
    label: str
    blocks: list[TextBlock]

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


# ---------------------------------------------------------------------------
# Phase A: Spatial grouping
# ---------------------------------------------------------------------------

_Y_GAP_THRESHOLD = 0.03  # 3% of screen height
_X_SHIFT_THRESHOLD = 0.15  # 15% horizontal shift starts new group


def group_spatially(blocks: list[TextBlock]) -> list[BlockGroup]:
    """Cluster text blocks into groups by spatial proximity."""
    if not blocks:
        return []

    # Sort by y descending (Vision uses bottom-left origin, so top of screen = high y)
    sorted_blocks = sorted(blocks, key=lambda b: -b.y)

    groups: list[BlockGroup] = []
    current = BlockGroup(blocks=[sorted_blocks[0]])

    for block in sorted_blocks[1:]:
        prev = current.blocks[-1]
        y_gap = abs(prev.y - block.y)
        x_shift = abs(block.x - current.avg_x)

        if y_gap > _Y_GAP_THRESHOLD or x_shift > _X_SHIFT_THRESHOLD:
            groups.append(current)
            current = BlockGroup(blocks=[block])
        else:
            current.blocks.append(block)

    groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Positional labeling & orchestration
# ---------------------------------------------------------------------------

def _positional_label(cx: float, cy: float, n_groups: int,
                      all_cx: list[float], all_cy: list[float]) -> str:
    """Assign a positional label based on relative position among groups."""
    if n_groups == 1:
        return "content"

    if n_groups == 2:
        h_spread = max(all_cx) - min(all_cx)
        v_spread = max(all_cy) - min(all_cy)
        if h_spread >= v_spread:
            return "Left" if cx <= min(all_cx) + h_spread / 2 else "Right"
        else:
            return "Top" if cy >= min(all_cy) + v_spread / 2 else "Bottom"

    # 3+ groups: divide into thirds on each axis
    sorted_cx = sorted(all_cx)
    sorted_cy = sorted(all_cy)
    x_lo = sorted_cx[len(sorted_cx) // 3]
    x_hi = sorted_cx[2 * len(sorted_cx) // 3]
    y_lo = sorted_cy[len(sorted_cy) // 3]
    y_hi = sorted_cy[2 * len(sorted_cy) // 3]

    v = "Top" if cy >= y_hi else ("Bottom" if cy < y_lo else "Center")
    h = "Left" if cx < x_lo else ("Right" if cx >= x_hi else "Center")

    if v == "Center" and h == "Center":
        return "Center"
    if v == "Center":
        return h
    if h == "Center":
        return v
    return f"{v} {h}"


def cluster_into_regions(blocks: list[TextBlock], app: str = "") -> list[ScreenRegion]:
    """Group blocks spatially, then assign positional labels."""
    groups = group_spatially(blocks)
    if not groups:
        return []

    centers = [(g.avg_x, (g.min_y + g.max_y) / 2) for g in groups]
    all_cx = [c[0] for c in centers]
    all_cy = [c[1] for c in centers]
    n = len(groups)

    regions: list[ScreenRegion] = []
    used_labels: dict[str, int] = {}
    for group, (cx, cy) in zip(groups, centers):
        label = _positional_label(cx, cy, n, all_cx, all_cy)
        used_labels[label] = used_labels.get(label, 0) + 1
        if used_labels[label] > 1:
            label = f"{label} {used_labels[label]}"
        regions.append(ScreenRegion(label=label, blocks=group.blocks))

    return regions


def format_regions(regions: list[ScreenRegion]) -> str:
    """Format regions as positional markdown sections."""
    non_empty = [r for r in regions if r.text.strip()]
    if len(non_empty) <= 1:
        return non_empty[0].text.strip() if non_empty else ""
    parts = [f"## {r.label}\n{r.text.strip()}" for r in non_empty]
    return "\n\n".join(parts)
