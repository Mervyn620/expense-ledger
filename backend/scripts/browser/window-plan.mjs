// Parallel collection gives every store its own browser window. A background tab is
// throttled by Chrome (timers slowed, rendering paused); a separate window is not, because
// the dedicated browser starts with --disable-backgrounding-occluded-windows.
//
// entries: [{ platform, targetId, windowId }] in collection order. The first store found in
// a window keeps it; every later store sharing that window is moved to a new one.
export function planOwnWindows(entries) {
  const claimed = new Set();
  const moves = [];
  for (const entry of entries) {
    if (claimed.has(entry.windowId)) moves.push(entry);
    else claimed.add(entry.windowId);
  }
  return moves;
}
