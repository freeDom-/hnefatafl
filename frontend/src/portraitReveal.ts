export const REVEAL_DURATION_MS = 2400;
export const CELEBRATION_DURATION_MS = 1050;
const FINAL_EXPANSION_MS = 2250;
const MAX_ART_RESOLUTION = 640;
const MAX_ART_PIXEL_RATIO = 2;
const MASK_RESOLUTION = 480;
const MASK_PADDING = 8;

const clamp = (value: number) => Math.max(0, Math.min(value, 1));
function smoothstep(value: number) {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
}

function makeShapes() {
  let seed = 8721;
  function random() {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 2 ** 32;
  }

  // The centers, delays, growth and seeded contours come from the approved demo.
  return [
    { x: 0.63, y: 0.29, delay: 120, reach: 0.6 },
    { x: 0.25, y: 0.54, delay: 680, reach: 0.57 },
    { x: 0.78, y: 0.72, delay: 920, reach: 0.53 },
    { x: 0.23, y: 0.16, delay: 1130, reach: 0.52 },
    { x: 0.48, y: 0.86, delay: 1340, reach: 0.57 },
  ].map((shape) => {
    const rotation = random() * 6.28;
    const points = Array.from({ length: 24 }, (_, index) => {
      const angle = rotation + (index * 2 * Math.PI) / 24;
      const radius = 0.84 + random() * 0.32;
      return {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius * (0.91 + 0.09 * Math.sin(angle + 1)),
      };
    });
    const outline = new Path2D();
    const first = points[0];
    const last = points[points.length - 1];
    outline.moveTo((first.x + last.x) / 2, (first.y + last.y) / 2);
    points.forEach((point, index) => {
      const next = points[(index + 1) % points.length];
      outline.quadraticCurveTo(point.x, point.y, (point.x + next.x) / 2, (point.y + next.y) / 2);
    });
    outline.closePath();
    const details = Array.from({ length: 11 }, () => {
      const angle = random() * 6.28;
      const distance = 0.78 + random() * 0.7;
      const cosine = Math.cos(angle);
      const sine = Math.sin(angle);
      return {
        angle,
        cosine,
        sine,
        x: cosine * distance,
        y: sine * distance,
        size: 0.035 + random() * 0.1,
      };
    });
    return { ...shape, outline, details };
  });
}

let shapes: ReturnType<typeof makeShapes> | undefined;

function drawMask(
  context: CanvasRenderingContext2D,
  side: number,
  elapsed: number,
  areas: ReturnType<typeof makeShapes>,
) {
  context.resetTransform();
  context.clearRect(0, 0, context.canvas.width, context.canvas.height);
  for (const area of areas) {
    const age = elapsed - area.delay;
    if (age < 0) continue;
    const quickStart = 0.18 * (1 - Math.exp(-age * 0.018));
    const expansion = 0.82 * smoothstep(age / (FINAL_EXPANSION_MS - area.delay));
    const radius = side * (3 / MASK_RESOLUTION + area.reach * (quickStart + expansion));
    context.setTransform(
      radius,
      0,
      0,
      radius,
      area.x * side + MASK_PADDING,
      area.y * side + MASK_PADDING,
    );
    context.fill(area.outline);
    const detailGrowth = clamp(age * 0.007);
    context.beginPath();
    for (const detail of area.details) {
      const width = Math.max(0.5 / radius, detail.size * detailGrowth);
      const height = Math.max(0.5 / radius, detail.size * detailGrowth * 0.7);
      context.moveTo(detail.x + detail.cosine * width, detail.y + detail.sine * width);
      context.ellipse(detail.x, detail.y, width, height, detail.angle, 0, 2 * Math.PI);
    }
    context.fill();
  }
  if (elapsed >= FINAL_EXPANSION_MS) {
    const first = areas[0];
    const coverage = smoothstep(
      (elapsed - FINAL_EXPANSION_MS) / (REVEAL_DURATION_MS - FINAL_EXPANSION_MS),
    );
    const radius = side * (first.reach + 1.7 * coverage);
    context.setTransform(
      radius,
      0,
      0,
      radius,
      first.x * side + MASK_PADDING,
      first.y * side + MASK_PADDING,
    );
    context.fill(first.outline);
  }
}

/** Composites a color layer over the existing grayscale DOM image. */
export function playPortraitReveal(
  image: HTMLImageElement | null,
  canvas: HTMLCanvasElement | null,
  complete: () => void,
  fallback: () => void,
) {
  if (!canvas || !image?.complete || !image.naturalWidth) {
    fallback();
    return;
  }
  const color = document.createElement('canvas');
  const mask = document.createElement('canvas');
  const featheredMask = document.createElement('canvas');
  let frame = 0;
  const dispose = () => {
    cancelAnimationFrame(frame);
    canvas.removeEventListener('contextlost', fallback);
    for (const layer of [canvas, color, mask, featheredMask]) layer.width = layer.height = 0;
  };
  try {
    const ratio = Math.min(window.devicePixelRatio || 1, MAX_ART_PIXEL_RATIO);
    const rendered = Math.round(image.getBoundingClientRect().width * ratio);
    const side = Math.min(rendered, MAX_ART_RESOLUTION, image.naturalWidth, image.naturalHeight);
    const maskSide = Math.min(side, MASK_RESOLUTION);
    canvas.width = canvas.height = color.width = color.height = side;
    featheredMask.width =
      featheredMask.height =
      mask.width =
      mask.height =
        maskSide + MASK_PADDING * 2;
    const output = canvas.getContext('2d');
    const artwork = color.getContext('2d');
    const coverage = mask.getContext('2d');
    const feather = featheredMask.getContext('2d');
    if (side <= 0 || !output || !artwork || !coverage || !feather)
      throw new Error('Portrait canvas unavailable');

    const maskScale = side / maskSide;
    const maskOffset = -MASK_PADDING * maskScale;
    const maskSize = mask.width * maskScale;
    // Blur the union at mask resolution so high-DPI artwork stays sharp and
    // the filter cost does not grow with the output bitmap.
    feather.globalCompositeOperation = 'copy';
    const blurRadius = (2 * maskSide) / MASK_RESOLUTION;
    const hasFilter = typeof feather.filter === 'string';
    if (hasFilter) feather.filter = `blur(${blurRadius}px)`;
    let maskSourceX = 0;
    if (!hasFilter || feather.filter === 'none') {
      // WebKit can feather the same alpha mask with a shadow. Shift the source
      // out of the bitmap so only its blurred shadow lands in the mask buffer.
      maskSourceX = -mask.width;
      feather.shadowOffsetX = mask.width;
      feather.shadowColor = '#fff';
      feather.shadowBlur = blurRadius * 2;
      if (!feather.shadowBlur) throw new Error('Portrait mask filtering unavailable');
    }

    const areas = (shapes ??= makeShapes());
    canvas.addEventListener('contextlost', fallback);
    artwork.imageSmoothingQuality = 'high';
    artwork.drawImage(image, 0, 0, side, side);
    const start = performance.now();
    const step = (now: number) => {
      const elapsed = now - start;
      if (elapsed >= REVEAL_DURATION_MS) {
        complete();
        return;
      }
      if (elapsed >= areas[0].delay) {
        try {
          drawMask(coverage, maskSide, elapsed, areas);
          feather.drawImage(mask, maskSourceX, 0);
          output.globalCompositeOperation = 'copy';
          output.drawImage(color, 0, 0);
          output.globalCompositeOperation = 'destination-in';
          output.drawImage(featheredMask, maskOffset, maskOffset, maskSize, maskSize);
        } catch {
          dispose();
          fallback();
          return;
        }
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  } catch {
    dispose();
    fallback();
    return;
  }
  return dispose;
}
