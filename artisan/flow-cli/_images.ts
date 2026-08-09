/**
 * Image generation model catalog + cost calc + model picker.
 *
 * Based on Google Flow's Imagen family models available via labs.google/fx/tools/flow
 */

export type ImageModelSpec = {
  key: string;
  friendlyName: string;
  cost: number;                 // credits per image
  defaultWidth: number;
  defaultHeight: number;
  supportsSeed: boolean;
  supportsReferenceImages: boolean;
};

export const IMAGE_MODELS: Record<string, ImageModelSpec> = {
  // Imagen 4 (latest)
  'imagen-4': {
    key: 'imagen-4',
    friendlyName: 'Imagen 4',
    cost: 8,                    // Estimated cost - needs verification
    defaultWidth: 1024,
    defaultHeight: 1024,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
  // Nano Banana 2 Lite (fastest, cheapest)
  'nano-banana-2-lite': {
    key: 'nano-banana-2-lite',
    friendlyName: 'Nano Banana 2 Lite',
    cost: 3,                    // Estimated cost - needs verification
    defaultWidth: 512,
    defaultHeight: 512,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
  // Nano Banana 2 (balanced)
  'nano-banana-2': {
    key: 'nano-banana-2',
    friendlyName: 'Nano Banana 2',
    cost: 5,                    // Estimated cost - needs verification
    defaultWidth: 768,
    defaultHeight: 768,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
  // Nano Banana 2 Pro (highest quality)
  'nano-banana-2-pro': {
    key: 'nano-banana-2-pro',
    friendlyName: 'Nano Banana 2 Pro',
    cost: 8,                    // Estimated cost - needs verification
    defaultWidth: 1024,
    defaultHeight: 1024,
    supportsSeed: true,
    supportsReferenceImages: true,
  },
};

// Friendly aliases the user can pass to `--model`
const IMAGE_MODEL_ALIASES: Record<string, string> = {
  // Imagen 4
  'imagen-4': 'imagen-4',
  'imagen4': 'imagen-4',

  // Nano Banana variants
  'nano-banana-2-lite': 'nano-banana-2-lite',
  'nb2-lite': 'nano-banana-2-lite',
  'nano-banana-2': 'nano-banana-2',
  'nb2': 'nano-banana-2',
  'nano-banana-pro': 'nano-banana-2-pro',
  'nb2-pro': 'nano-banana-2-pro',
  'nano-banana-2-pro': 'nano-banana-2-pro',
};

export function resolveImageModelKey(input: string): string {
  return IMAGE_MODEL_ALIASES[input] || input;
}

export function pickImageModel(override?: string): ImageModelSpec {
  if (override) {
    const resolved = resolveImageModelKey(override);
    const m = IMAGE_MODELS[resolved];
    if (!m) {
      const aliases = Object.keys(IMAGE_MODEL_ALIASES).slice(0, 6).join(', ');
      throw new Error(
        `unknown image model "${override}". Try friendly aliases like: ${aliases}, ... ` +
        `or raw keys: ${Object.keys(IMAGE_MODELS).join(', ')}`,
      );
    }
    return m;
  }
  // Default to nano-banana-2 (balanced)
  return IMAGE_MODELS['nano-banana-2'];
}

export function totalImageCost(model: ImageModelSpec, count: number): number {
  return model.cost * Math.max(1, count);
}