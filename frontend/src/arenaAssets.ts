/** Local, text-free decorative assets. Never accept asset URLs from account data. */
export const ARENA_DECOR = {
  hall: { path: '/assets/arena-v2/assets/art/saga-hall-great-hall.webp', width: 1672, height: 941 },
  hallSmall: {
    path: '/assets/arena-v2/assets/art/saga-hall-great-hall-small.webp',
    width: 1280,
    height: 720,
  },
  forge: { path: '/assets/arena-v2/assets/art/iron-ash-forge.webp', width: 1672, height: 941 },
  forgeSmall: {
    path: '/assets/arena-v2/assets/art/iron-ash-forge-small.webp',
    width: 1280,
    height: 720,
  },
  frame: { path: '/assets/arena-v2/assets/icons/carved-frame.svg', width: 120, height: 120 },
  grain: { path: '/assets/arena-v2/assets/textures/board-ash-grain.webp', width: 768, height: 768 },
  forest: { path: '/assets/arena-v2/assets/art/kings-table-forest.webp', width: 512, height: 768 },
  carved: {
    path: '/assets/arena-v2/assets/art/saga-hall-raven-panel.webp',
    width: 1104,
    height: 736,
  },
} as const;
